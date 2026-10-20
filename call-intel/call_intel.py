#!/usr/bin/env python3
"""
Call Intel Agent — 30-second account research for SDR call prep.

Usage:
    python3 call_intel.py https://mntn.com
    python3 call_intel.py mntn.com
    python3 call_intel.py https://mntn.com --person "Andrew Celaya"
"""

import sys
import os
import re
import argparse
import time
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from exa_py import Exa
import anthropic

from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.env"))

EXA_API_KEY = os.getenv("EXA_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

exa = Exa(api_key=EXA_API_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


# ---------------------------------------------------------------------------
# Research functions (run in parallel)
# ---------------------------------------------------------------------------

def normalize_domain(url_or_domain: str) -> str:
    """Turn input into a clean domain like 'mntn.com'."""
    if not url_or_domain.startswith("http"):
        url_or_domain = "https://" + url_or_domain
    parsed = urlparse(url_or_domain)
    domain = parsed.netloc.lower().replace("www.", "")
    return domain


def scrape_website(domain: str) -> dict:
    """Scrape homepage + about page for company basics."""
    pages = {}
    for path in ["", "/about", "/about-us", "/company"]:
        url = f"https://{domain}{path}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10, allow_redirects=True)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator=" ", strip=True)
                # Truncate to save tokens
                pages[path or "/"] = text[:3000]
        except Exception:
            continue
    return {"source": "website", "data": pages}


def exa_company_news(domain: str, company_name: str) -> dict:
    """Recent news, funding, product launches, partnerships."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        response = exa.search(
            f"{company_name} company news funding product launch",
            num_results=5,
            start_published_date=cutoff,
            include_domains=[domain],
            contents={"text": {"max_characters": 800}},
        )
        results = []
        for item in response.results:
            results.append({
                "title": getattr(item, "title", ""),
                "url": getattr(item, "url", ""),
                "text": (getattr(item, "text", "") or "")[:800],
                "date": (getattr(item, "published_date", "") or "")[:10],
            })
        return {"source": "company_news", "data": results}
    except Exception as e:
        return {"source": "company_news", "data": [], "error": str(e)}


def exa_broader_news(company_name: str) -> dict:
    """News about the company from external sources."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        response = exa.search(
            f'"{company_name}" announcement OR funding OR launch OR partnership',
            num_results=5,
            start_published_date=cutoff,
            contents={"text": {"max_characters": 800}},
        )
        results = []
        for item in response.results:
            results.append({
                "title": getattr(item, "title", ""),
                "url": getattr(item, "url", ""),
                "text": (getattr(item, "text", "") or "")[:800],
                "date": (getattr(item, "published_date", "") or "")[:10],
            })
        return {"source": "broader_news", "data": results}
    except Exception as e:
        return {"source": "broader_news", "data": [], "error": str(e)}


def exa_hiring_signals(domain: str, company_name: str) -> dict:
    """Job postings — what they're building, what tools they use."""
    try:
        response = exa.search(
            f"{company_name} hiring OR careers OR job posting",
            num_results=5,
            include_domains=[domain, "linkedin.com", "greenhouse.io", "lever.co", "ashbyhq.com"],
            contents={"text": {"max_characters": 600}},
        )
        results = []
        for item in response.results:
            results.append({
                "title": getattr(item, "title", ""),
                "url": getattr(item, "url", ""),
                "text": (getattr(item, "text", "") or "")[:600],
            })
        return {"source": "hiring_signals", "data": results}
    except Exception as e:
        return {"source": "hiring_signals", "data": [], "error": str(e)}


def exa_person_content(person_name: str, company_name: str) -> dict:
    """Content the target person has written or been quoted in."""
    if not person_name:
        return {"source": "person_content", "data": [], "skipped": True}
    try:
        response = exa.search(
            f'"{person_name}" "{company_name}"',
            num_results=5,
            contents={"text": {"max_characters": 800}},
        )
        results = []
        for item in response.results:
            results.append({
                "title": getattr(item, "title", ""),
                "url": getattr(item, "url", ""),
                "text": (getattr(item, "text", "") or "")[:800],
            })
        return {"source": "person_content", "data": results}
    except Exception as e:
        return {"source": "person_content", "data": [], "error": str(e)}


# ---------------------------------------------------------------------------
# Claude synthesis
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a call prep analyst for an SDR. Given raw research data about a company and optionally a specific person, produce a concise call brief.

Output EXACTLY this format (fill in every field, use "Not found" if data is missing):

CALL BRIEF: [Company Name]
==============================
WHAT THEY DO: [1-2 sentences — what the company does, who they sell to]
SIZE: [employees, revenue if known]
INDUSTRY: [vertical]
HQ: [location]
FOUNDED: [year if known]

RECENT TRIGGERS (last 90 days):
- [Most important: funding, product launch, new hire, partnership, expansion]
- [Second signal if available]
- [Third signal if available]

TECH STACK & TOOLS:
- [Any tools, platforms, or technologies mentioned in job postings or website]

HIRING SIGNALS:
- [What roles they're hiring for — indicates what they're building/scaling]

PERSON: [Name, Title if provided — otherwise skip this section]
- Background: [previous companies, how long in role, education if notable]
- Their own words: [quote or paraphrase from their content if found]
- LinkedIn/social presence: [any notable posts, talks, publications]

COMPETITIVE LANDSCAPE:
- [Key competitors or alternatives in their space]

PAIN ANGLES (top 3 things that might keep leadership up at night):
1. [Based on their stage, industry, and signals]
2. [Second angle]
3. [Third angle]

CONVERSATION STARTERS:
- [Specific opener based on strongest signal — reference something concrete]
- [Backup opener based on second signal]

RAW SIGNAL QUALITY: [HIGH/MEDIUM/LOW — how much real data did we find?]
"""


def synthesize(research_results: list, domain: str, person_name: str) -> str:
    """Feed all research into Claude for synthesis."""
    research_text = ""
    for r in research_results:
        source = r.get("source", "unknown")
        data = r.get("data", {})
        research_text += f"\n--- {source.upper()} ---\n"
        if isinstance(data, dict):
            for key, val in data.items():
                research_text += f"[{key}]: {val[:2000] if isinstance(val, str) else val}\n"
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    research_text += json.dumps(item, indent=1)[:1500] + "\n"
                else:
                    research_text += str(item)[:500] + "\n"
        else:
            research_text += str(data)[:2000] + "\n"

    user_msg = f"Company domain: {domain}\n"
    if person_name:
        user_msg += f"Target person: {person_name}\n"
    user_msg += f"\nRAW RESEARCH DATA:\n{research_text}"

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_msg}],
    )
    return response.content[0].text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(url_or_domain: str, person_name: str = None):
    start = time.time()
    domain = normalize_domain(url_or_domain)
    # Guess company name from domain (strip TLD)
    company_name = domain.split(".")[0].upper()

    print(f"\n  Researching {domain}...", end="", flush=True)

    # Fire all 5 research prompts in parallel
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(scrape_website, domain): "website",
            pool.submit(exa_company_news, domain, company_name): "company_news",
            pool.submit(exa_broader_news, company_name): "broader_news",
            pool.submit(exa_hiring_signals, domain, company_name): "hiring",
            pool.submit(exa_person_content, person_name, company_name): "person",
        }
        results = []
        for future in as_completed(futures):
            label = futures[future]
            try:
                result = future.result()
                results.append(result)
                print(f" [{label}]", end="", flush=True)
            except Exception as e:
                print(f" [{label}:ERR]", end="", flush=True)
                results.append({"source": label, "data": [], "error": str(e)})

    research_time = time.time() - start
    print(f"\n  Research done in {research_time:.1f}s. Synthesizing...", flush=True)

    # Synthesize with Claude
    brief = synthesize(results, domain, person_name)

    total_time = time.time() - start
    print(f"  Total time: {total_time:.1f}s\n")
    print(brief)
    print()

    # Save to file
    output_dir = os.path.join(os.path.dirname(__file__), "call_briefs")
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{domain.replace('.', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w") as f:
        f.write(brief)
    print(f"  Saved to {filepath}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="30-second account research agent for SDR call prep")
    parser.add_argument("url", help="Company website URL or domain (e.g., mntn.com)")
    parser.add_argument("--person", "-p", help="Target person name (e.g., 'Andrew Celaya')")
    args = parser.parse_args()

    run(args.url, args.person)
