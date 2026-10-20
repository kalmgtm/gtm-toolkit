#!/usr/bin/env python3
"""
ICP Enrichment Pipeline — Waterfall enrichment + scoring from a CSV of domains.

Takes a list of company domains, researches each through multiple sources
(website scrape, Exa news, Exa hiring signals), enriches with structured
company data, scores against ICP criteria, and outputs a clean CSV.

Usage:
    python3 enrich.py domains.csv
    python3 enrich.py domains.csv --icp "Series B+ fintech, 100-500 employees, US-based"
    python3 enrich.py domains.csv --output enriched.csv --workers 5
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


def normalize_domain(url_or_domain: str) -> str:
    url_or_domain = url_or_domain.strip()
    if not url_or_domain:
        return ""
    if not url_or_domain.startswith("http"):
        url_or_domain = "https://" + url_or_domain
    parsed = urlparse(url_or_domain)
    domain = parsed.netloc.lower().replace("www.", "")
    return domain


# ---------------------------------------------------------------------------
# Research — waterfall enrichment from multiple sources
# ---------------------------------------------------------------------------

def scrape_website(domain: str) -> dict:
    """Source 1: Direct website scrape."""
    try:
        r = requests.get(f"https://{domain}", headers=HEADERS, timeout=8, allow_redirects=True)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
            meta_desc = ""
            meta_tag = soup.find("meta", attrs={"name": "description"})
            if meta_tag:
                meta_desc = meta_tag.get("content", "")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)[:3000]
            return {"source": "website", "status": "ok", "title": title, "meta": meta_desc, "text": text}
        return {"source": "website", "status": f"http_{r.status_code}"}
    except Exception as e:
        return {"source": "website", "status": "error", "error": str(e)[:100]}


def search_exa_news(domain: str, company_name: str) -> dict:
    """Source 2: Exa company news (last 90 days)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        response = exa.search(
            f'"{company_name}" OR site:{domain}',
            num_results=5,
            start_published_date=cutoff,
            contents={"text": {"max_characters": 500}},
        )
        results = [
            {"title": getattr(r, "title", ""), "text": (getattr(r, "text", "") or "")[:500],
             "date": (getattr(r, "published_date", "") or "")[:10]}
            for r in response.results
        ]
        return {"source": "exa_news", "status": "ok", "count": len(results), "results": results}
    except Exception as e:
        return {"source": "exa_news", "status": "error", "error": str(e)[:100]}


def search_exa_hiring(domain: str, company_name: str) -> dict:
    """Source 3: Exa hiring signals."""
    try:
        response = exa.search(
            f"{company_name} hiring OR careers OR job",
            num_results=3,
            include_domains=[domain, "linkedin.com", "greenhouse.io", "lever.co"],
            contents={"text": {"max_characters": 400}},
        )
        results = [
            {"title": getattr(r, "title", ""), "text": (getattr(r, "text", "") or "")[:400]}
            for r in response.results
        ]
        return {"source": "exa_hiring", "status": "ok", "count": len(results), "results": results}
    except Exception as e:
        return {"source": "exa_hiring", "status": "error", "error": str(e)[:100]}


def research_company(domain: str) -> dict:
    """Run all research sources for a single company."""
    company_name = domain.split(".")[0].capitalize()
    sources = {}

    website = scrape_website(domain)
    sources["website"] = website

    # Pull company name from title if available
    if website.get("title"):
        company_name = website["title"].split("|")[0].split("-")[0].split("—")[0].strip()[:50]

    news = search_exa_news(domain, company_name)
    sources["exa_news"] = news

    hiring = search_exa_hiring(domain, company_name)
    sources["exa_hiring"] = hiring

    # Waterfall summary: which sources returned data
    waterfall = []
    if website.get("status") == "ok":
        waterfall.append("website")
    if news.get("count", 0) > 0:
        waterfall.append(f"exa_news({news['count']})")
    if hiring.get("count", 0) > 0:
        waterfall.append(f"exa_hiring({hiring['count']})")

    return {
        "domain": domain,
        "company_name": company_name,
        "sources": sources,
        "waterfall": waterfall,
    }


# ---------------------------------------------------------------------------
# Enrichment + scoring with Claude
# ---------------------------------------------------------------------------

ENRICHMENT_SYSTEM_PROMPT = """You are a B2B company research analyst. Given raw research data about a company and ICP criteria, extract structured enrichment data and score the company.

Output EXACTLY this JSON format (no markdown, no code fences, just raw JSON):

{
    "company_name": "Official Company Name",
    "domain": "domain.com",
    "one_liner": "What the company does in one sentence",
    "industry": "Primary industry vertical",
    "sub_industry": "More specific category if applicable",
    "employee_estimate": "Best estimate of employee count or range",
    "funding_stage": "Seed/Series A/Series B/etc or 'Unknown' or 'Public' or 'Bootstrapped'",
    "hq_location": "City, State or Country if found",
    "tech_stack_signals": ["any technologies, platforms, or tools mentioned"],
    "recent_news": ["up to 3 key recent developments"],
    "hiring_roles": ["notable open roles if found"],
    "key_people": ["name - title, for up to 3 notable leaders if found"],
    "ai_signals": ["specific evidence of AI/automation deployment"],
    "score": 75,
    "grade": "B",
    "verdict": "STRONG FIT / GOOD FIT / WEAK FIT / DISQUALIFIED",
    "top_signal": "The single strongest reason this company fits or doesn't fit",
    "reason": "One sentence explaining the score"
}

Scoring guide:
- 90-100 (A): Perfect ICP match with strong recent signals
- 80-89 (B+): Strong match, minor gaps
- 70-79 (B): Good match but missing some criteria
- 60-69 (C): Partial match, notable gaps
- 50-59 (D): Weak match, stretch target
- Below 50 (F): Disqualified

Be strict. Most companies should score 50-75. Only clear, signal-backed fits get 80+.
Only include data you can verify from the research provided. Write "Unknown" rather than guessing."""


def enrich_and_score(research: dict, icp_criteria: str) -> dict:
    """Enrich and score a single company using Claude."""
    research_text = json.dumps(research, indent=1, default=str)[:6000]

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=[{"type": "text", "text": ENRICHMENT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"ICP CRITERIA:\n{icp_criteria}\n\nCOMPANY RESEARCH:\n{research_text}"}],
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        result = json.loads(raw)
        result["waterfall"] = " > ".join(research.get("waterfall", []))
        return result
    except json.JSONDecodeError:
        return {
            "company_name": research.get("company_name", "Unknown"),
            "domain": research.get("domain", ""),
            "score": 0, "grade": "ERR", "verdict": "ERROR",
            "reason": "Failed to parse response",
            "waterfall": " > ".join(research.get("waterfall", [])),
        }


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def process_batch(domains: list, icp_criteria: str, max_workers: int = 3) -> list:
    """Research and enrich a batch of domains."""
    results = []
    total = len(domains)

    # Research phase (parallel)
    print(f"\n  Researching {total} companies...\n")
    research_data = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(research_company, d): d for d in domains}
        for i, future in enumerate(as_completed(futures)):
            domain = futures[future]
            try:
                research_data[domain] = future.result()
                waterfall = " > ".join(research_data[domain].get("waterfall", []))
                print(f"  [{i+1:>3}/{total}] {domain:<35} sources: {waterfall}")
            except Exception as e:
                research_data[domain] = {"domain": domain, "error": str(e)}
                print(f"  [{i+1:>3}/{total}] {domain:<35} ERROR: {str(e)[:50]}")

    # Enrichment + scoring phase (sequential for rate limits)
    print(f"\n  Enriching + scoring...\n")
    for i, domain in enumerate(domains):
        research = research_data.get(domain, {"domain": domain})
        try:
            result = enrich_and_score(research, icp_criteria)
            result["domain"] = domain
            results.append(result)
            score = result.get("score", 0)
            grade = result.get("grade", "?")
            name = result.get("company_name", "?")[:24]
            print(f"  [{i+1:>3}/{total}] {name:<25} {score:>3} ({grade})  {result.get('verdict', '?')}")
        except Exception as e:
            results.append({
                "company_name": domain.split(".")[0].capitalize(),
                "domain": domain, "score": 0, "grade": "ERR",
                "verdict": "ERROR", "reason": str(e),
            })
            print(f"  [{i+1:>3}/{total}] {domain:<25} ERROR: {str(e)[:50]}")

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def read_domains_from_csv(filepath: str) -> list:
    """Read domains from a CSV. Looks for domain/website/url column."""
    domains = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        domain_col = None
        for candidate in ["domain", "website", "url", "company_domain", "site"]:
            for h in reader.fieldnames:
                if h.lower().strip() == candidate:
                    domain_col = h
                    break
            if domain_col:
                break
        if not domain_col:
            domain_col = reader.fieldnames[0]

        for row in reader:
            raw = row.get(domain_col, "").strip()
            if raw:
                domain = normalize_domain(raw)
                if domain:
                    domains.append(domain)
    return domains


def write_results_csv(results: list, filepath: str):
    """Write enriched + scored results to CSV."""
    if not results:
        return
    fields = [
        "score", "grade", "verdict", "company_name", "domain", "one_liner",
        "industry", "sub_industry", "employee_estimate", "funding_stage",
        "hq_location", "tech_stack_signals", "recent_news", "hiring_roles",
        "key_people", "ai_signals", "top_signal", "reason", "waterfall",
    ]
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = {}
            for field in fields:
                val = r.get(field, "")
                if isinstance(val, list):
                    val = " | ".join(str(v) for v in val)
                row[field] = val
            writer.writerow(row)


def write_results_json(results: list, filepath: str):
    """Write full results as JSON for inspection."""
    with open(filepath, "w") as f:
        json.dump(results, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Enrich and score company domains against ICP criteria")
    parser.add_argument("input", help="CSV file with company domains, or comma-separated domains")
    parser.add_argument("--icp", help="Custom ICP criteria (otherwise uses default)")
    parser.add_argument("--output", "-o", help="Output CSV path")
    parser.add_argument("--json", action="store_true", help="Also output full JSON results")
    parser.add_argument("--workers", "-w", type=int, default=3, help="Parallel research workers (default: 3)")
    args = parser.parse_args()

    icp = args.icp or DEFAULT_ICP

    # Read domains from CSV or parse comma-separated
    if os.path.isfile(args.input):
        domains = read_domains_from_csv(args.input)
        base_name = os.path.splitext(os.path.basename(args.input))[0]
    else:
        domains = [normalize_domain(d) for d in args.input.split(",") if d.strip()]
        base_name = "enriched"

    domains = [d for d in domains if d]
    if not domains:
        print("No domains found.")
        sys.exit(1)

    print(f"\n  ICP Enrichment Pipeline")
    print(f"  {'='*50}")
    print(f"  Domains: {len(domains)}")
    print(f"  Sources: website scrape > exa news > exa hiring")
    print(f"  Scorer:  claude sonnet 4.6")

    start = time.time()
    results = process_batch(domains, icp, max_workers=args.workers)
    elapsed = time.time() - start

    # Summary table
    print(f"\n  {'='*100}")
    print(f"  RESULTS — {len(results)} companies, {elapsed:.1f}s")
    print(f"  {'='*100}")
    print(f"  {'SCORE':<6} {'GRD':<4} {'VERDICT':<14} {'COMPANY':<25} {'INDUSTRY':<20} {'SIZE':<12} {'WATERFALL'}")
    print(f"  {'─'*6} {'─'*4} {'─'*14} {'─'*25} {'─'*20} {'─'*12} {'─'*30}")
    for r in results:
        print(f"  {r.get('score', 0):<6} {r.get('grade', '?'):<4} {r.get('verdict', '?'):<14} "
              f"{r.get('company_name', '?')[:24]:<25} {r.get('industry', '?')[:19]:<20} "
              f"{str(r.get('employee_estimate', '?'))[:11]:<12} {r.get('waterfall', '')}")

    # Stats
    strong = len([r for r in results if r.get("score", 0) >= 80])
    good = len([r for r in results if 60 <= r.get("score", 0) < 80])
    weak = len([r for r in results if r.get("score", 0) < 60])
    print(f"\n  Strong (80+): {strong}  |  Good (60-79): {good}  |  Weak (<60): {weak}")
    print(f"  Time: {elapsed:.1f}s  |  Avg: {elapsed/len(results):.1f}s per company")

    # Write outputs
    output_csv = args.output or f"enriched_{base_name}.csv"
    write_results_csv(results, output_csv)
    print(f"\n  CSV: {output_csv}")

    if args.json:
        output_json = output_csv.replace(".csv", ".json")
        write_results_json(results, output_json)
        print(f"  JSON: {output_json}")

    print()


if __name__ == "__main__":
    main()
