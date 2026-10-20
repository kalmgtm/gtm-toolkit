#!/usr/bin/env python3
"""
Reply Classifier Agent — Classify inbound email replies and route to next action.

Usage:
    python3 reply_classifier.py replies.csv
    python3 reply_classifier.py replies.csv --output classified.csv
"""

import sys
import os
import csv
import argparse
import time
import json
from datetime import datetime

import anthropic

from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.env"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

CATEGORIES = {
    "INTERESTED": {
        "description": "Wants to learn more, take a meeting, see a demo, or asks qualifying questions",
        "action": "Route to AE immediately. Respond within 1 hour with calendar link.",
        "priority": 1,
    },
    "INTERESTED - TIMING": {
        "description": "Interested but not right now. 'Reach out next quarter', 'after our funding round', 'in the new year'",
        "action": "Add to nurture sequence. Set follow-up reminder for the date they specified.",
        "priority": 2,
    },
    "REFERRAL": {
        "description": "Not the right person but points you to someone else. 'Talk to our VP of...', 'CC-ing my colleague'",
        "action": "Extract the referred contact. Send warm outreach referencing the intro.",
        "priority": 2,
    },
    "QUESTION": {
        "description": "Asks about pricing, features, integrations, or how it works — not yet committed to a meeting",
        "action": "Answer the question directly. Include a soft CTA for a call.",
        "priority": 3,
    },
    "OBJECTION": {
        "description": "Pushback on need, timing, budget, or competition. 'We already use X', 'not a priority', 'too expensive'",
        "action": "Log the objection type. Send tailored rebuttal or case study.",
        "priority": 3,
    },
    "NOT INTERESTED": {
        "description": "Clear no. 'Not interested', 'please remove me', 'don't contact me again'",
        "action": "Remove from active sequence. Add to suppression list.",
        "priority": 4,
    },
    "OOO": {
        "description": "Out of office / auto-reply. Contains return date or backup contact",
        "action": "Extract return date if present. Re-queue send for return date + 1 day.",
        "priority": 5,
    },
    "BOUNCE": {
        "description": "Hard bounce, invalid email, mailbox full, delivery failure",
        "action": "Mark email invalid. Find alternate contact or updated email.",
        "priority": 5,
    },
    "UNSUBSCRIBE": {
        "description": "Requests removal from list. May be angry or neutral.",
        "action": "Remove immediately. Add to global suppression. Do NOT re-add.",
        "priority": 4,
    },
    "NOISE": {
        "description": "Auto-confirmations, read receipts, spam filter replies, 'thanks' with no substance",
        "action": "Ignore. No action needed.",
        "priority": 6,
    },
}

SYSTEM_PROMPT = """You are an email reply classifier for a B2B sales team. Given an inbound reply to an outbound sales email, classify it into exactly one category and extract key details.

CATEGORIES:
""" + "\n".join(
    f"- {cat}: {info['description']}" for cat, info in CATEGORIES.items()
) + """

Output EXACTLY this JSON format (no markdown, no code fences, just raw JSON):

{
    "category": "INTERESTED",
    "confidence": 0.95,
    "sentiment": "positive",
    "summary": "One sentence summary of what they said",
    "key_details": {
        "referred_contact": "Name and title if they referred someone, else null",
        "return_date": "Date if OOO, else null",
        "objection_type": "budget/timing/competition/need if objection, else null",
        "follow_up_date": "Suggested follow-up date if timing-based, else null",
        "question_topic": "What they asked about if question, else null"
    },
    "suggested_reply": "Draft reply if appropriate (2-3 sentences max), else null",
    "action": "Specific next action for the SDR"
}

Rules:
- If the reply contains BOTH interest and an objection, classify as INTERESTED (interest wins)
- If they refer someone AND show interest themselves, classify as INTERESTED and note the referral in key_details
- OOO auto-replies are always OOO regardless of content
- "Who is this?" or "What company are you with?" = QUESTION, not objection
- One-word replies like "Sure" or "Yes" = INTERESTED
- "Thanks" or "Got it" with nothing else = NOISE
- Be strict on confidence: only 0.9+ if the intent is unambiguous
"""


def classify_reply(reply_text: str, original_email: str = None, sender_name: str = None, company: str = None) -> dict:
    """Classify a single reply."""
    user_msg = ""
    if sender_name or company:
        user_msg += f"From: {sender_name or 'Unknown'}"
        if company:
            user_msg += f" ({company})"
        user_msg += "\n"
    if original_email:
        user_msg += f"\nORIGINAL OUTBOUND EMAIL:\n{original_email[:500]}\n"
    user_msg += f"\nINBOUND REPLY:\n{reply_text}"

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {
            "category": "NOISE",
            "confidence": 0.0,
            "summary": "Failed to parse classification",
            "raw_response": raw[:500],
        }

    # Add metadata from our category definitions
    cat = result.get("category", "NOISE")
    if cat in CATEGORIES:
        result["priority"] = CATEGORIES[cat]["priority"]
        result["default_action"] = CATEGORIES[cat]["action"]
    else:
        result["priority"] = 6

    return result


def classify_batch(replies: list, progress_callback=None) -> list:
    """Classify a list of replies. Each reply is a dict with at least 'reply_text'."""
    results = []
    total = len(replies)

    for i, reply in enumerate(replies):
        try:
            classification = classify_reply(
                reply_text=reply.get("reply_text", ""),
                original_email=reply.get("original_email"),
                sender_name=reply.get("sender_name"),
                company=reply.get("company"),
            )
            classification["sender_name"] = reply.get("sender_name", "")
            classification["company"] = reply.get("company", "")
            classification["reply_text"] = reply.get("reply_text", "")[:200]
            results.append(classification)
        except Exception as e:
            results.append({
                "category": "ERROR",
                "confidence": 0.0,
                "summary": str(e),
                "sender_name": reply.get("sender_name", ""),
                "company": reply.get("company", ""),
                "reply_text": reply.get("reply_text", "")[:200],
                "priority": 99,
            })

        if progress_callback:
            progress_callback(i + 1, total, reply.get("sender_name", "Unknown"))

    # Sort by priority (1 = most urgent)
    results.sort(key=lambda x: x.get("priority", 99))
    return results


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def read_replies_from_csv(filepath: str) -> list:
    """Read replies from CSV. Looks for 'reply', 'body', 'reply_text', or 'message' column."""
    replies = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)

        # Find the reply text column
        text_col = None
        for candidate in ["reply_text", "reply", "body", "message", "text", "email_body"]:
            for h in reader.fieldnames:
                if h.lower().strip() == candidate:
                    text_col = h
                    break
            if text_col:
                break
        if not text_col:
            text_col = reader.fieldnames[0]

        # Find optional columns
        name_col = None
        company_col = None
        original_col = None
        for h in reader.fieldnames:
            hl = h.lower().strip()
            if hl in ("sender_name", "name", "from", "sender"):
                name_col = h
            elif hl in ("company", "company_name", "organization"):
                company_col = h
            elif hl in ("original_email", "original", "outbound"):
                original_col = h

        for row in reader:
            reply_text = row.get(text_col, "").strip()
            if reply_text:
                replies.append({
                    "reply_text": reply_text,
                    "sender_name": row.get(name_col, "") if name_col else "",
                    "company": row.get(company_col, "") if company_col else "",
                    "original_email": row.get(original_col, "") if original_col else "",
                })
    return replies


def write_results_csv(results: list, filepath: str):
    if not results:
        return
    fields = ["priority", "category", "confidence", "sentiment", "sender_name", "company",
              "summary", "action", "suggested_reply", "reply_text"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Classify inbound email replies and route to next action")
    parser.add_argument("csv_file", help="CSV file with reply texts")
    parser.add_argument("--output", "-o", help="Output CSV path")
    args = parser.parse_args()

    replies = read_replies_from_csv(args.csv_file)
    if not replies:
        print("No replies found in CSV.")
        sys.exit(1)

    print(f"\n  Classifying {len(replies)} replies...\n")

    def progress(current, total, name):
        bar = "#" * int(current / total * 20)
        print(f"  [{bar:<20}] {current}/{total} — {name}", flush=True)

    start = time.time()
    results = classify_batch(replies, progress_callback=progress)
    elapsed = time.time() - start

    # Print summary
    print(f"\n  Done in {elapsed:.1f}s\n")

    # Category counts
    from collections import Counter
    counts = Counter(r.get("category", "?") for r in results)
    print(f"  BREAKDOWN:")
    for cat, count in counts.most_common():
        print(f"    {cat:<20} {count}")

    print(f"\n  {'PRI':<4} {'CATEGORY':<20} {'CONF':<6} {'SENDER':<20} {'SUMMARY'}")
    print(f"  {'─'*4} {'─'*20} {'─'*6} {'─'*20} {'─'*50}")
    for r in results:
        print(f"  {r.get('priority', '?'):<4} {r.get('category', '?'):<20} "
              f"{r.get('confidence', 0):<6.0%} {r.get('sender_name', '?')[:19]:<20} "
              f"{r.get('summary', '—')[:50]}")

    output_path = args.output or f"classified_{os.path.basename(args.csv_file)}"
    write_results_csv(results, output_path)
    print(f"\n  Saved to {output_path}\n")


if __name__ == "__main__":
    main()
