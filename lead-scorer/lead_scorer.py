#!/usr/bin/env python3
"""
Lead Scorer Agent — Score and rank companies against ICP criteria.

Usage:
    python3 lead_scorer.py leads.csv
    python3 lead_scorer.py leads.csv --icp "Series A-C SaaS companies, 50-500 employees, using AI in production"
    python3 lead_scorer.py leads.csv --output scored_leads.csv
"""

import sys
import os
import csv
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

DEFAULT_ICP = """Score each company on how well it matches this ICP:
- B2B company actively deploying AI or automation in production (not just exploring)
- 50-1000 employees (big enough for complexity, too small for internal tooling)
- Regulated or complex industry preferred: financial services, healthcare, insurance, legal, defense, logistics, manufacturing
- Recent growth signals: funding, product launches, leadership hires, partnerships
- Hiring for technical roles (engineering, data, AI) indicates active building

Disqualify:
- IT services/consulting companies (they deploy for clients, not themselves)
- Pure dev tooling or infrastructure companies
- Companies with no evidence of AI/automation deployment
- Companies under 20 employees (too early)
"""


# ---------------------------------------------------------------------------
# Research functions (per company)
# ---------------------------------------------------------------------------

def normalize_domain(url_or_domain: str) -> str:
    url_or_domain = url_or_domain.strip()
    if not url_or_domain:
        return ""
    if not url_or_domain.startswith("http"):
        url_or_domain = "https://" + url_or_domain
    parsed = urlparse(url_or_domain)
    domain = parsed.netloc.lower().replace("www.", "")
    return domain


def research_company(domain: str) -> dict:
    """Run all research for a single company. Returns raw data."""
    company_name = domain.split(".")[0].upper()
    research = {"domain": domain, "company_name": company_name}

    # 1. Scrape website
    try:
        r = requests.get(f"https://{domain}", headers=HEADERS, timeout=8, allow_redirects=True)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)[:2000]
            research["website"] = {"title": title, "text": text}
        else:
            research["website"] = {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        research["website"] = {"error": str(e)}

    # 2. Exa: recent news + signals
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        response = exa.search(
            f'"{company_name}" OR site:{domain}',
            num_results=5,
            start_published_date=cutoff,
            contents={"text": {"max_characters": 500}},
        )
        research["news"] = [
            {
                "title": getattr(item, "title", ""),
                "text": (getattr(item, "text", "") or "")[:500],
                "date": (getattr(item, "published_date", "") or "")[:10],
            }
            for item in response.results
        ]
    except Exception as e:
        research["news"] = {"error": str(e)}

    # 3. Exa: hiring signals
    try:
        response = exa.search(
            f"{company_name} hiring OR careers OR job",
            num_results=3,
            include_domains=[domain, "linkedin.com", "greenhouse.io", "lever.co"],
            contents={"text": {"max_characters": 400}},
        )
        research["hiring"] = [
            {
                "title": getattr(item, "title", ""),
                "text": (getattr(item, "text", "") or "")[:400],
            }
            for item in response.results
        ]
    except Exception as e:
        research["hiring"] = {"error": str(e)}

    return research


# ---------------------------------------------------------------------------
# Scoring with Claude
# ---------------------------------------------------------------------------

SCORING_SYSTEM_PROMPT = """You are a lead scoring analyst. Given research data about a company and ICP criteria, score the company.

Output EXACTLY this JSON format (no markdown, no code fences, just raw JSON):

{
    "company_name": "Company Name",
    "domain": "domain.com",
    "score": 85,
    "grade": "A",
    "what_they_do": "One sentence description",
    "industry": "Industry vertical",
    "estimated_size": "employee count or range if known",
    "top_signal": "The single strongest signal for why they're a fit",
    "signals": ["signal 1", "signal 2", "signal 3"],
    "red_flags": ["red flag 1 if any"],
    "verdict": "STRONG FIT / GOOD FIT / WEAK FIT / DISQUALIFIED",
    "reason": "One sentence explaining the score"
}

Scoring guide:
- 90-100 (A): Perfect ICP match with strong recent signals
- 80-89 (B+): Strong match, minor gaps
- 70-79 (B): Good match but missing some criteria
- 60-69 (C): Partial match, notable gaps
- 50-59 (D): Weak match, stretch target
- Below 50 (F): Disqualified — does not fit ICP

Be strict. Most companies should score 50-75. Only clear, signal-backed fits get 80+."""


def score_company(research: dict, icp_criteria: str) -> dict:
    """Score a single company using Claude."""
    research_text = json.dumps(research, indent=1, default=str)[:4000]

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=[{"type": "text", "text": SCORING_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"ICP CRITERIA:\n{icp_criteria}\n\nCOMPANY RESEARCH:\n{research_text}"}],
    )

    raw = response.content[0].text.strip()
    # Handle potential markdown fences
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {
            "company_name": research.get("company_name", "Unknown"),
            "domain": research.get("domain", ""),
            "score": 0,
            "grade": "ERR",
            "verdict": "ERROR",
            "reason": "Failed to parse scoring response",
            "raw_response": raw[:500],
        }


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def process_batch(domains: list, icp_criteria: str, max_workers: int = 3, progress_callback=None) -> list:
    """Research and score a batch of domains."""
    scored = []
    total = len(domains)

    # Research phase (parallel)
    research_results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(research_company, d): d for d in domains}
        for i, future in enumerate(as_completed(futures)):
            domain = futures[future]
            try:
                research_results[domain] = future.result()
            except Exception as e:
                research_results[domain] = {"domain": domain, "error": str(e)}
            if progress_callback:
                progress_callback("research", i + 1, total, domain)

    # Scoring phase (sequential to respect rate limits, but fast)
    for i, domain in enumerate(domains):
        research = research_results.get(domain, {"domain": domain})
        try:
            result = score_company(research, icp_criteria)
            result["domain"] = domain
            scored.append(result)
        except Exception as e:
            scored.append({
                "company_name": domain.split(".")[0].capitalize(),
                "domain": domain,
                "score": 0,
                "grade": "ERR",
                "verdict": "ERROR",
                "reason": str(e),
            })
        if progress_callback:
            progress_callback("scoring", i + 1, total, domain)

    # Sort by score descending
    scored.sort(key=lambda x: x.get("score", 0), reverse=True)
    return scored


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def read_domains_from_csv(filepath: str) -> list:
    """Read domains from a CSV. Looks for 'domain', 'website', or 'url' column."""
    domains = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        headers = [h.lower().strip() for h in reader.fieldnames]

        # Find the domain column
        domain_col = None
        for candidate in ["domain", "website", "url", "company_domain", "site"]:
            for h in reader.fieldnames:
                if h.lower().strip() == candidate:
                    domain_col = h
                    break
            if domain_col:
                break

        if not domain_col:
            # Fall back to first column
            domain_col = reader.fieldnames[0]

        for row in reader:
            raw = row.get(domain_col, "").strip()
            if raw:
                domain = normalize_domain(raw)
                if domain:
                    domains.append(domain)
    return domains


def write_results_csv(scored: list, filepath: str):
    """Write scored results to CSV."""
    if not scored:
        return
    fields = ["score", "grade", "verdict", "company_name", "domain", "what_they_do",
              "industry", "estimated_size", "top_signal", "reason"]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(scored)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Score and rank leads against ICP criteria")
    parser.add_argument("csv_file", help="CSV file with company domains")
    parser.add_argument("--icp", help="Custom ICP criteria (otherwise uses default)")
    parser.add_argument("--output", "-o", help="Output CSV path (default: scored_<input>.csv)")
    parser.add_argument("--workers", "-w", type=int, default=3, help="Parallel workers (default: 3)")
    args = parser.parse_args()

    icp = args.icp or DEFAULT_ICP

    # Read domains
    domains = read_domains_from_csv(args.csv_file)
    if not domains:
        print("No domains found in CSV.")
        sys.exit(1)

    print(f"\n  Scoring {len(domains)} companies against ICP...\n")

    def progress(phase, current, total, domain):
        bar = "#" * int(current / total * 20)
        print(f"  [{phase:>8}] [{bar:<20}] {current}/{total} — {domain}", flush=True)

    start = time.time()
    scored = process_batch(domains, icp, max_workers=args.workers, progress_callback=progress)
    elapsed = time.time() - start

    # Print summary
    print(f"\n  Done in {elapsed:.1f}s\n")
    print(f"  {'SCORE':<6} {'GRADE':<6} {'VERDICT':<14} {'COMPANY':<25} {'DOMAIN':<30} {'TOP SIGNAL'}")
    print(f"  {'─'*6} {'─'*6} {'─'*14} {'─'*25} {'─'*30} {'─'*40}")
    for r in scored:
        print(f"  {r.get('score', 0):<6} {r.get('grade', '?'):<6} {r.get('verdict', '?'):<14} "
              f"{r.get('company_name', '?')[:24]:<25} {r.get('domain', '?')[:29]:<30} "
              f"{r.get('top_signal', '—')[:40]}")

    # Write output
    output_path = args.output or f"scored_{os.path.basename(args.csv_file)}"
    write_results_csv(scored, output_path)
    print(f"\n  Saved to {output_path}\n")


if __name__ == "__main__":
    main()
