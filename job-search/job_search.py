#!/usr/bin/env python3
"""
GTM Engineering Job Search — searches across job boards via Serper API,
scores results against Lauren's background, fetches top hits for deep scoring,
and outputs ranked matches. Supports --daily for cron/notification mode.
"""

import requests
import json
import os
import re
import hashlib
import sys
import time
from datetime import datetime
from html.parser import HTMLParser

import subprocess

from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.env"))

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL = "https://google.serper.dev/search"
EMAIL_TO = "lauren@kalmgtm.com"

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "job_output")
SEEN_FILE = os.path.join(OUTPUT_DIR, "seen_jobs.json")
DAILY_LOG = os.path.join(OUTPUT_DIR, "daily_digest.md")

# --- Search queries: full-time GTM eng roles ---
QUERIES_FULLTIME = [
    '"GTM engineer" remote United States',
    '"GTM engineering" hiring remote US',
    '"go-to-market engineer" remote US hiring',
    '"outbound engineer" OR "growth engineer" B2B startup United States',
    '"Clay engineer" OR "enrichment engineer" hiring remote US',
    '"Claude Code" OR "AI outbound" engineer hiring US remote',
    'site:clay.com/job-board GTM engineer',
    'site:getcargo.ai/jobs GTM',
    'site:gtm-engineer-jobs.com remote',
    'site:linkedin.com/jobs "GTM engineer" remote United States',
    'site:linkedin.com/jobs "outbound engineer" OR "growth engineer" remote US',
    'site:wellfound.com "GTM engineer" remote',
    'site:wellfound.com "growth engineer" OR "outbound engineer"',
    'site:lever.co "GTM engineer"',
    'site:greenhouse.io "GTM engineer"',
    'site:ashbyhq.com "GTM engineer"',
]

# --- Search queries: contract / fractional / agency ---
QUERIES_CONTRACT = [
    # Fractional GTM
    '"fractional GTM" OR "fractional go-to-market" remote',
    '"fractional growth" OR "fractional outbound" B2B',
    '"fractional sales" engineer OR automation startup',
    # Contract GTM work
    '"contract" "GTM engineer" OR "outbound engineer" remote',
    '"contract" Clay OR enrichment OR outbound automation',
    # Freelance / agency
    '"freelance" "GTM" OR "outbound" OR "enrichment" engineer',
    '"GTM agency" OR "outbound agency" hiring OR partner',
    '"outbound as a service" OR "pipeline as a service"',
    # Platforms with contract GTM work
    'site:upwork.com "GTM engineer" OR "Clay" OR "outbound automation"',
    'site:toptal.com GTM OR "outbound engineer" OR "growth engineer"',
    'site:wellfound.com "GTM engineer" contract OR fractional',
    # LinkedIn contract/fractional
    'site:linkedin.com/jobs "fractional" "GTM" OR "outbound" OR "growth" remote',
    'site:linkedin.com/jobs "contract" "GTM engineer" OR "outbound engineer" remote',
    # Agencies looking for GTM talent
    '"revenue agency" OR "growth agency" hiring "GTM engineer" OR "outbound"',
    '"Clay expert" OR "Clay specialist" freelance OR contract OR agency',
    # Startup advisory / embedded roles
    '"embedded" "GTM" OR "outbound" startup remote',
    '"GTM consultant" OR "outbound consultant" B2B startup',
    # --- Agency career pages (direct) ---
    'site:thekiln.com careers OR hiring OR join',
    'site:coldiq.com careers OR hiring OR join OR team',
    'site:frontal.com careers OR hiring OR join',
    'site:growthengine.co careers OR hiring OR join',
    'site:growtheginex.com careers OR hiring OR team',
    'site:revpartners.io careers OR hiring OR join',
    'site:understory.agency careers OR hiring OR join',
    'site:devcommx.com careers OR hiring OR join',
    'site:getaob.com careers OR hiring OR jobs',
    'site:gtmbase.io careers OR hiring OR join',
    'site:toplinex.com careers OR hiring OR join',
    'site:punch.cool careers OR hiring OR join',
    # Agency job boards and directories
    '"GTM agency" hiring "Clay" OR "enrichment" 2026',
    '"outbound agency" hiring engineer OR specialist remote 2026',
    '"Clay agency" OR "Clay partner" hiring remote',
    '"Clay certified" hiring OR careers OR join remote',
    'site:clay.com/partners hiring OR careers',
    # Contract marketplaces beyond Upwork
    'site:contra.com "GTM" OR "outbound" OR "Clay"',
    'site:flexjobs.com "GTM engineer" OR "outbound engineer"',
    'site:gun.io "GTM" OR "growth" OR "outbound"',
    'site:pangian.com "GTM" OR "outbound" OR "sales automation"',
    # Clay community contract/fractional
    'site:community.clay.com "hiring" "contract" OR "fractional" OR "part-time"',
    'site:community.clay.com "hiring" "agency" OR "consultant"',
    # Broader agency/fractional searches
    '"GTM engineer" "agency" OR "consulting" OR "services" hiring 2026',
    '"outbound specialist" OR "outbound manager" contract OR fractional remote',
    '"revenue engineer" OR "pipeline engineer" contract OR freelance remote',
    '"Clay workflows" OR "Clay tables" hiring OR contract OR freelance',
    '"cold email" specialist OR engineer contract OR agency remote',
    '"demand gen" engineer OR specialist contract OR fractional remote',
]

QUERIES = QUERIES_FULLTIME + QUERIES_CONTRACT

# --- Geo exclusion patterns ---
GEO_EXCLUDE = [
    "european union", "emea", "latin america", "latam", "apac",
    "german speaking", "spanish speaking", "french speaking",
    "india", "philippines", "nigeria", "pakistan", "brazil",
    "bilbao", "berlin", "london", "amsterdam", "paris", "singapore",
    "australia", "new zealand", "south africa", "kenya",
    "eu only", "uk only", "europe only",
]

# --- Scoring keywords (higher weight = stronger signal of fit) ---
STRONG_FIT = [
    "gtm engineer", "gtm engineering", "go-to-market engineer",
    "outbound engineer", "clay", "enrichment", "cold outbound",
    "prospecting", "signal-based", "waterfall", "sequencing",
    "claude", "ai outbound", "pipeline generation",
]

GOOD_FIT = [
    "growth engineer", "revenue engineer", "sales engineer",
    "outbound", "b2b", "startup", "series a", "series b",
    "saas", "demand gen", "lead generation", "scraping",
    "api", "python", "automation", "crm", "hubspot", "salesforce",
    "apollo", "instantly", "smartlead", "lemlist", "remote",
    "founding", "first hire", "build from scratch",
]

# Contract/agency/fractional keywords get STRONG_FIT weight (3 pts each)
CONTRACT_BOOST = [
    "fractional", "contract", "freelance", "agency",
    "embedded", "consultant", "advisory", "part-time",
    "outbound as a service", "pipeline as a service",
    "retainer", "project-based", "client-facing",
    "multiple clients", "consulting", "services",
]

NEGATIVE = [
    "revops", "revenue operations", "salesforce admin", "crm admin",
    "marketing operations", "mops", "pardot", "marketo admin",
    "senior director", "vp of", "chief", "head of",
    "10+ years", "15+ years", "staff engineer",
    "java", "c++", "rust", "kubernetes", "devops", "sre",
    "frontend", "react", "angular", "vue",
    "director level", "principal engineer",
]

EXCLUDED_COMPANIES = [
    "proxon", "proxonai", "blc",
]

# --- Deep scoring: keywords from Lauren's actual stack/experience ---
DEEP_STRONG = [
    "clay", "enrichment pipeline", "waterfall enrichment", "signal-based",
    "cold outbound", "outbound from scratch", "build outbound",
    "python", "api integration", "sequencing", "cold email",
    "prospecting automation", "lead scoring", "data enrichment",
    "ai-powered outbound", "claude", "anthropic",
    "founding gtm", "first gtm", "0 to 1",
]

DEEP_GOOD = [
    "hubspot", "salesforce", "apollo", "instantly", "smartlead", "lemlist",
    "b2b saas", "startup", "series a", "series b", "seed",
    "remote", "united states", "us-based", "us based",
    "scraping", "web scraping", "data collection",
    "automation", "workflow", "pipeline",
    "crm", "revenue", "demand generation", "lead generation",
    "outbound", "inbound", "growth", "go-to-market",
    "healthcare", "insurance", "fintech", "financial services",
    "100-500", "100-1000", "small team",
    "$100k", "$120k", "$130k", "$140k", "$150k", "$160k", "$170k", "$180k", "$190k", "$200k",
    "build from scratch", "stand up outbound", "launch outbound",
]

# Contract/fractional signals get top-tier deep scoring (same as DEEP_STRONG)
DEEP_CONTRACT_BOOST = [
    "fractional", "contract", "freelance", "agency", "embedded",
    "part-time", "project-based", "retainer", "monthly",
    "multiple clients", "client-facing", "consulting",
    "outbound as a service", "pipeline as a service",
    "clay partner", "clay certified", "clay agency",
    "client work", "client campaigns", "manage campaigns",
    "agency team", "agency hiring",
]

DEEP_NEGATIVE = [
    "10+ years", "15+ years", "senior staff", "staff level", "principal",
    "director of", "vp of", "head of engineering",
    "java", "c++", "rust", "go lang", "golang",
    "kubernetes", "docker", "terraform", "devops", "sre", "infrastructure",
    "frontend", "react", "angular", "vue", "css", "html",
    "machine learning engineer", "ml engineer", "data scientist",
    "phd required", "masters required",
    "salesforce admin", "marketo", "pardot",
    "european union", "emea", "latam", "apac", "eu only",
    "german speaking", "french speaking",
    "on-site only", "onsite only", "no remote",
    "internship", "intern ",
    "revops", "revenue operations", "marketing operations",
]


class HTMLTextExtractor(HTMLParser):
    """Strip HTML tags, keep text."""
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def get_text(self):
        return " ".join(self.parts)


def strip_html(html):
    extractor = HTMLTextExtractor()
    try:
        extractor.feed(html)
    except Exception:
        return html
    return extractor.get_text()


def search_serper(query, num=10):
    resp = requests.post(
        SERPER_URL,
        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
        json={"q": query, "num": num},
    )
    resp.raise_for_status()
    return resp.json().get("organic", [])


def result_id(result):
    return hashlib.md5(result["link"].encode()).hexdigest()


def is_excluded_geo(result):
    """Check if result is clearly non-US."""
    text = f"{result.get('title', '')} {result.get('snippet', '')}".lower()
    link = result.get("link", "").lower()
    for pattern in GEO_EXCLUDE:
        if pattern in text or pattern in link:
            return True
    # Non-US LinkedIn country domains
    non_us_domains = ["uk.linkedin", "de.linkedin", "fr.linkedin", "au.linkedin",
                      "in.linkedin", "br.linkedin", "be.linkedin", "nl.linkedin",
                      "es.linkedin", "it.linkedin", "sg.linkedin", "ca.linkedin"]
    for d in non_us_domains:
        if d in link:
            return True
    return False


def is_aggregator_page(result):
    """Detect generic search/listing pages vs actual job postings."""
    link = result.get("link", "").lower()
    title = result.get("title", "").lower()
    # Generic aggregator index pages
    aggregator_patterns = [
        "indeed.com/q-", "ziprecruiter.com/jobs/", "glassdoor.com/job-listing",
        "salary.com", "itjobswatch", "4dayweek.io", "himalayas.app/jobs/full-time",
        "revopsroles.com/categories",
    ]
    for p in aggregator_patterns:
        if p in link:
            return True
    # Blog/article/info pages (not job listings)
    info_patterns = ["what is a gtm", "blog/", "/blog", "youtube.com", "instagram.com",
                     "reddit.com", "facebook.com", "how to become"]
    for p in info_patterns:
        if p in link or p in title:
            return True
    return False


def score_result(result):
    """Score a search result against Lauren's background."""
    text = f"{result.get('title', '')} {result.get('snippet', '')}".lower()
    score = 0
    reasons = []

    for kw in STRONG_FIT:
        if kw in text:
            score += 3
            reasons.append(f"+3 {kw}")
    for kw in GOOD_FIT:
        if kw in text:
            score += 1
            reasons.append(f"+1 {kw}")
    for kw in CONTRACT_BOOST:
        if kw in text:
            score += 3
            reasons.append(f"+3 {kw}")
    for kw in NEGATIVE:
        if kw in text:
            score -= 2
            reasons.append(f"-2 {kw}")
    for co in EXCLUDED_COMPANIES:
        if co in text:
            score -= 100
            reasons.append(f"-100 excluded: {co}")

    # Boost direct ATS listings
    direct_signals = ["lever.co", "greenhouse.io", "ashbyhq.com", "jobs.lever",
                      "boards.greenhouse", "wellfound.com/jobs/"]
    for sig in direct_signals:
        if sig in result.get("link", ""):
            score += 2
            reasons.append(f"+2 direct: {sig}")

    return score, reasons


def fetch_job_page(url):
    """Fetch a job page and extract text content. Returns text or None."""
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        })
        if resp.status_code != 200:
            return None
        text = strip_html(resp.text)
        # Collapse whitespace
        text = re.sub(r'\s+', ' ', text).strip()
        # Take first 5000 chars (enough for job description)
        return text[:5000]
    except Exception:
        return None


def deep_score(text):
    """Score full job description text against Lauren's detailed profile."""
    text_lower = text.lower()
    score = 0
    reasons = []

    for kw in DEEP_STRONG:
        if kw in text_lower:
            score += 5
            reasons.append(f"+5 {kw}")
    for kw in DEEP_GOOD:
        if kw in text_lower:
            score += 2
            reasons.append(f"+2 {kw}")
    for kw in DEEP_CONTRACT_BOOST:
        if kw in text_lower:
            score += 5
            reasons.append(f"+5 {kw}")
    for kw in DEEP_NEGATIVE:
        if kw in text_lower:
            score -= 3
            reasons.append(f"-3 {kw}")

    # Salary extraction
    salary_match = re.findall(r'\$(\d{2,3})[,.]?(\d{3})', text)
    if salary_match:
        salaries = [int(m[0] + m[1]) for m in salary_match]
        max_salary = max(salaries)
        if max_salary >= 150000:
            score += 5
            reasons.append(f"+5 salary ${max_salary:,}")
        elif max_salary >= 120000:
            score += 3
            reasons.append(f"+3 salary ${max_salary:,}")
        elif max_salary < 60000:
            score -= 3
            reasons.append(f"-3 very low salary ${max_salary:,}")

    # Years of experience check
    yoe_match = re.findall(r'(\d+)\+?\s*years', text_lower)
    if yoe_match:
        max_yoe = max(int(y) for y in yoe_match)
        if max_yoe >= 10:
            score -= 5
            reasons.append(f"-5 requires {max_yoe}+ years")
        elif max_yoe >= 7:
            score -= 2
            reasons.append(f"-2 requires {max_yoe}+ years")

    return score, reasons


def extract_company_from_title(title):
    """Try to extract company name from job listing title."""
    patterns = [
        r'at\s+([A-Z][A-Za-z0-9\s&.]+?)(?:\s*[-—|]|\s*$)',  # "GTM Engineer at Company"
        r'[-—|]\s*([A-Z][A-Za-z0-9\s&.]+?)(?:\s*[-—|]|\s*$)',  # "Title - Company"
    ]
    for p in patterns:
        m = re.search(p, title)
        if m:
            return m.group(1).strip()
    return None


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return json.load(f)
    return {}


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)


def classify_job_type(item):
    """Tag a result as full-time, contract, fractional, or agency."""
    text = f"{item['title']} {item['snippet']}".lower()
    all_reasons = " ".join(item.get("reasons", []) + item.get("deep_reasons", []))
    combined = f"{text} {all_reasons}"

    if any(kw in combined for kw in ["fractional", "part-time", "advisory", "embedded"]):
        return "fractional"
    if any(kw in combined for kw in ["contract", "freelance", "project-based", "retainer"]):
        return "contract"
    if any(kw in combined for kw in ["agency", "client-facing", "multiple clients", "as a service"]):
        return "agency"
    return "full-time"


# --- Outreach insight engine ---
# Maps detected signals to what Lauren should lead with in her application/outreach.
# Based on her actual experience: BLC ($5.2M pipeline, $400/mo), Proxon (YC, $150K/4mo),
# Kalm GTM ($35K closed), Clay/Claude Code stack, healthcare/insurance verticals.

INSIGHT_RULES = [
    # (trigger keywords in reasons/text, insight text)
    (["clay"], "Lead with Clay. You've built production Clay tables with waterfall enrichment, not just taken the certification. Mention specific table architectures you've built."),
    (["claude", "anthropic", "ai outbound", "ai-powered"], "Lead with Claude Code as your build tool. You shipped Proxon's entire enrichment pipeline with it. Most applicants list AI as a buzzword -- you build with it daily."),
    (["enrichment", "waterfall", "data enrichment"], "Lead with enrichment pipeline experience. BLC and Proxon both ran multi-source waterfall enrichment. Mention the signal scoring + template system you built."),
    (["cold outbound", "cold email", "outbound from scratch", "build outbound", "stand up outbound", "launch outbound"], "Lead with building outbound from zero. BLC: $5.2M in pipeline from scratch on $400/mo. That's the proof point -- cost efficiency at scale."),
    (["founding", "first gtm", "first hire", "0 to 1"], "Lead with 0-to-1 experience. You've done this three times (BLC, Proxon, Kalm). Emphasize you don't need an existing playbook -- you build the playbook."),
    (["pipeline generation", "pipeline"], "Lead with pipeline numbers. $5.2M at BLC (3 years, $400/mo budget). $150K at Proxon (4 months, from scratch). Concrete, verifiable."),
    (["prospecting", "signal-based", "scoring", "lead scoring"], "Lead with signal-based prospecting. You built a scoring system that routes leads by signal quality, not just title/industry. Mention the Exa + Claude scoring pipeline."),
    (["healthcare", "insurance", "financial services", "fintech", "regulated"], "Lead with regulated vertical experience. Proxon sold into insurance/healthcare/financial services. You know compliance-sensitive buyers."),
    (["fractional", "contract", "freelance", "agency", "consultant", "retainer", "embedded"], "Lead with the Kalm GTM offer. You already run a $2,500/30-day outbound test as a service. Position this as low-risk: they get a working outbound system in 30 days or they walk."),
    (["hubspot"], "Mention HubSpot -- you've built sequences and workflows in it across multiple engagements."),
    (["apollo", "instantly", "smartlead", "lemlist", "sequencing"], "Mention your sending infra experience. You've run multi-sender setups with domain warming and deliverability optimization."),
    (["automation", "workflow"], "Lead with automation. Everything you build is designed to run without you babysitting it -- templates for 80% of contacts, Claude only for high-signal leads."),
    (["python", "api", "scraping"], "Lead with technical chops. Python + API integrations + scraping. You're not a no-code-only operator -- you can build custom collectors and enrichment scripts."),
    (["startup", "series a", "series b", "seed"], "Emphasize startup DNA. Every role you've had was early-stage or scrappy. You don't need a big team or budget to produce."),
    (["salary $1"], "Salary looks strong. Don't negotiate down -- your pipeline numbers justify top of range."),
]


def generate_outreach_insight(item):
    """Generate personalized outreach advice based on why this job matched."""
    all_reasons = " ".join(item.get("reasons", []) + item.get("deep_reasons", []))
    text = f"{item['title']} {item['snippet']}".lower()
    combined = f"{all_reasons} {text}"

    insights = []
    seen_insights = set()
    for triggers, insight in INSIGHT_RULES:
        if any(t in combined for t in triggers):
            # Dedupe by first 30 chars
            key = insight[:30]
            if key not in seen_insights:
                insights.append(insight)
                seen_insights.add(key)

    if not insights:
        insights.append("Generic GTM engineer role. Lead with the BLC numbers ($5.2M pipeline, $400/mo budget) and your Clay + Claude Code stack.")

    return insights[:4]  # max 4 insights per job


def build_email_html(scored, new_count):
    """Build an HTML email with job type tags and outreach insights."""
    new_high = [s for s in scored if s["new"] and s["total_score"] >= 10]
    new_good = [s for s in scored if s["new"] and 5 <= s["total_score"] < 10]
    new_mid = [s for s in scored if s["new"] and 3 <= s["total_score"] < 5]
    date_str = datetime.now().strftime("%b %d, %Y")

    if not new_high and not new_good and not new_mid:
        return None

    type_colors = {
        "full-time": "#6f42c1",
        "contract": "#0969da",
        "fractional": "#1a7f37",
        "agency": "#bf8700",
    }

    def job_card(item):
        company = item["company"] or "—"
        jtype = classify_job_type(item)
        type_color = type_colors.get(jtype, "#666")
        deep_tag = f' (deep: {item["deep_score"]:+d})' if item["deep_score"] is not None else ""
        insights = generate_outreach_insight(item)

        top_reasons = item["reasons"][:2] + item["deep_reasons"][:3]
        reasons_str = ", ".join(top_reasons)

        insight_html = "".join(
            f'<li style="margin-bottom:4px">{ins}</li>' for ins in insights
        )

        return (
            f'<div style="border:1px solid #e1e4e8;border-radius:8px;padding:16px;margin-bottom:16px">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">'
            f'<span style="font-size:18px;font-weight:bold">{company}</span>'
            f'<span style="background:{type_color};color:white;padding:2px 10px;border-radius:12px;font-size:12px;font-weight:600">{jtype.upper()}</span>'
            f'</div>'
            f'<div style="margin-bottom:6px"><a href="{item["link"]}" style="color:#0969da;text-decoration:none;font-size:14px">{item["title"][:80]}</a></div>'
            f'<div style="font-size:13px;color:#666;margin-bottom:8px">Score: {item["total_score"]}{deep_tag} | {reasons_str}</div>'
            f'<div style="font-size:13px;color:#57606a;margin-bottom:6px">{item["snippet"][:200]}</div>'
            f'<div style="background:#f6f8fa;border-radius:6px;padding:10px;margin-top:10px">'
            f'<div style="font-size:12px;font-weight:600;color:#1a7f37;margin-bottom:4px">LEAD WITH:</div>'
            f'<ul style="margin:0;padding-left:18px;font-size:13px;color:#24292f">{insight_html}</ul>'
            f'</div>'
            f'</div>'
        )

    sections = []

    # Count by type across all new results
    all_new = new_high + new_good + new_mid
    ft_count = len([s for s in all_new if classify_job_type(s) == "full-time"])
    ct_count = len([s for s in all_new if classify_job_type(s) in ("contract", "fractional", "agency")])

    if new_high:
        cards = "".join(job_card(s) for s in new_high)
        sections.append(f'<h3 style="color:#1a7f37;border-bottom:2px solid #1a7f37;padding-bottom:6px">Strong matches ({len(new_high)})</h3>{cards}')

    if new_good:
        cards = "".join(job_card(s) for s in new_good)
        sections.append(f'<h3 style="color:#0969da;border-bottom:2px solid #0969da;padding-bottom:6px">Good matches ({len(new_good)})</h3>{cards}')

    if new_mid:
        cards = "".join(job_card(s) for s in new_mid)
        sections.append(f'<h3 style="color:#666;border-bottom:2px solid #ddd;padding-bottom:6px">Worth a look ({len(new_mid)})</h3>{cards}')

    body = "".join(sections)
    return (
        f'<div style="font-family:-apple-system,Helvetica,Arial,sans-serif;max-width:700px;margin:0 auto;padding:20px">'
        f'<h2 style="margin-bottom:4px">GTM Job Search — {date_str}</h2>'
        f'<p style="color:#57606a;margin-top:0">{new_count} new results. '
        f'{ft_count} full-time, {ct_count} contract/fractional/agency. '
        f'{len(new_high)} strong, {len(new_good)} good, {len(new_mid)} worth a look.</p>'
        f'{body}'
        f'<p style="margin-top:24px;font-size:12px;color:#999">Automated by job_search.py</p>'
        f'</div>'
    )


def send_email_via_mail_app(subject, html_body):
    """Send email via macOS Mail.app using osascript."""
    # Escape for AppleScript
    escaped_body = html_body.replace('\\', '\\\\').replace('"', '\\"')
    escaped_subject = subject.replace('"', '\\"')

    script = f'''
    tell application "Mail"
        set newMessage to make new outgoing message with properties {{subject:"{escaped_subject}", visible:false}}
        tell newMessage
            set html content to "{escaped_body}"
            make new to recipient at end of to recipients with properties {{address:"{EMAIL_TO}"}}
            send
        end tell
    end tell
    '''
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"Email sent to {EMAIL_TO}")
            return True
        else:
            print(f"Email failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"Email error: {e}")
        return False


def run_search(show_all=False, daily=False, deep=True):
    """Run all queries, dedupe, score, optionally deep-score top hits."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    seen = load_seen()

    all_results = {}
    credits_used = 0

    print(f"Running {len(QUERIES)} searches...\n")
    for q in QUERIES:
        try:
            results = search_serper(q, num=10)
            credits_used += 1
            for r in results:
                rid = result_id(r)
                if rid not in all_results:
                    all_results[rid] = r
        except Exception as e:
            print(f"  Error on query '{q[:50]}...': {e}")

    print(f"Found {len(all_results)} unique results ({credits_used} API credits used)")

    # Filter: remove non-US, aggregator pages
    filtered = {}
    geo_removed = 0
    agg_removed = 0
    for rid, r in all_results.items():
        if is_excluded_geo(r):
            geo_removed += 1
            continue
        if is_aggregator_page(r):
            agg_removed += 1
            continue
        filtered[rid] = r

    print(f"Filtered: {geo_removed} non-US, {agg_removed} aggregator/article pages")
    print(f"Remaining: {len(filtered)} actual job listings\n")

    # Phase 1: quick score
    scored = []
    new_count = 0
    for rid, r in filtered.items():
        score, reasons = score_result(r)
        is_new = rid not in seen
        if is_new:
            new_count += 1
        scored.append({
            "id": rid,
            "title": r.get("title", ""),
            "link": r.get("link", ""),
            "snippet": r.get("snippet", ""),
            "date": r.get("date", ""),
            "score": score,
            "reasons": reasons,
            "new": is_new,
            "company": extract_company_from_title(r.get("title", "")),
            "deep_score": None,
            "deep_reasons": [],
            "total_score": score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    # Phase 2: deep score top hits (score >= 3, max 15 pages)
    if deep:
        top_hits = [s for s in scored if s["score"] >= 3][:15]
        if top_hits:
            print(f"Deep scoring {len(top_hits)} top results...\n")
            for item in top_hits:
                link = item["link"]
                # Skip known aggregator job boards (we want actual company pages)
                skip_domains = ["clay.com/job-board", "getcargo.ai/jobs", "gtm-engineer-jobs.com/Jobs",
                                "gtm-engineer-jobs.com/$"]
                if any(d in link for d in skip_domains):
                    continue

                page_text = fetch_job_page(link)
                if page_text:
                    ds, dr = deep_score(page_text)
                    item["deep_score"] = ds
                    item["deep_reasons"] = dr
                    item["total_score"] = item["score"] + ds
                    print(f"  {item['company'] or item['title'][:30]}: deep={ds:+d} (total={item['total_score']})")
                else:
                    print(f"  {item['company'] or item['title'][:30]}: could not fetch")
                time.sleep(0.3)  # polite crawling

            # Re-sort by total score
            scored.sort(key=lambda x: x["total_score"], reverse=True)
            print()

    # Display
    print(f"{'NEW ' if new_count else ''}{new_count} new results\n")
    print("=" * 80)
    print("TOP MATCHES")
    print("=" * 80)

    displayed = 0
    digest_lines = [f"# Job Search Digest — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]

    for item in scored:
        if not show_all and item["total_score"] < 3:
            continue
        tag = " [NEW]" if item["new"] else ""
        deep_tag = f" (deep: {item['deep_score']:+d})" if item["deep_score"] is not None else ""
        jtype = classify_job_type(item)
        type_tag = f" [{jtype.upper()}]"

        print(f"\n  Total: {item['total_score']}{tag}{type_tag}{deep_tag}")
        if item["company"]:
            print(f"  Company: {item['company']}")
        print(f"  Title: {item['title']}")
        print(f"  Link:  {item['link']}")
        print(f"  Snippet: {item['snippet'][:150]}")
        if item["date"]:
            print(f"  Date: {item['date']}")
        quick_reasons = [r for r in item["reasons"][:3]]
        deep_reasons = [r for r in item["deep_reasons"][:5]]
        all_reasons = quick_reasons + deep_reasons
        print(f"  Why: {', '.join(all_reasons)}")

        # Outreach insights
        insights = generate_outreach_insight(item)
        print(f"  Lead with:")
        for ins in insights:
            print(f"    -> {ins}")
        displayed += 1

        # Build digest
        if item["new"]:
            digest_lines.append(f"- **{item['company'] or 'Unknown'}** [{jtype}] — [{item['title']}]({item['link']}) — Score: {item['total_score']}")

    if displayed == 0:
        print("\n  No results scored >= 3. Run with --all to see everything.")

    # Borderline
    borderline = [x for x in scored if 1 <= x["total_score"] < 3]
    if borderline and not daily:
        print(f"\n{'=' * 80}")
        print(f"BORDERLINE ({len(borderline)} results, total_score 1-2)")
        print("=" * 80)
        for item in borderline[:10]:
            tag = " [NEW]" if item["new"] else ""
            print(f"\n  Score: {item['total_score']}{tag}")
            print(f"  Title: {item['title']}")
            print(f"  Link:  {item['link']}")

    # Mark all as seen
    for item in scored:
        if item["id"] not in seen:
            seen[item["id"]] = {
                "title": item["title"],
                "link": item["link"],
                "company": item["company"],
                "job_type": classify_job_type(item),
                "first_seen": datetime.now().isoformat(),
                "score": item["score"],
                "deep_score": item["deep_score"],
                "total_score": item["total_score"],
            }
    save_seen(seen)

    # Save full results
    output_file = os.path.join(OUTPUT_DIR, f"search_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    with open(output_file, "w") as f:
        json.dump(scored, f, indent=2)
    print(f"\nFull results saved to {output_file}")

    # Daily digest
    new_high = [s for s in scored if s["new"] and s["total_score"] >= 5]
    new_mid = [s for s in scored if s["new"] and 3 <= s["total_score"] < 5]

    if daily:
        if new_high or new_mid:
            digest_lines.insert(1, f"\n**{len(new_high)} strong matches, {len(new_mid)} decent matches**\n")
        else:
            digest_lines.insert(1, "\nNo new matches today.\n")
        with open(DAILY_LOG, "w") as f:
            f.write("\n".join(digest_lines))
        print(f"\nDaily digest saved to {DAILY_LOG}")

        # Send email
        html = build_email_html(scored, new_count)
        if html:
            date_str = datetime.now().strftime("%b %d")
            send_email_via_mail_app(f"GTM jobs — {date_str}", html)
        else:
            print("No new matches worth emailing.")

    # Summary
    all_typed = [(classify_job_type(s), s) for s in scored if s["total_score"] >= 3]
    ft = len([t for t, _ in all_typed if t == "full-time"])
    ct = len([t for t, _ in all_typed if t in ("contract", "fractional", "agency")])
    print(f"\n--- Summary ---")
    print(f"Total results: {len(scored)} (filtered from {len(all_results)})")
    print(f"New this run: {new_count}")
    print(f"Score >= 5: {len([s for s in scored if s['total_score'] >= 5])}")
    print(f"Score 3-4: {len([s for s in scored if 3 <= s['total_score'] < 5])}")
    print(f"By type (score >= 3): {ft} full-time, {ct} contract/fractional/agency")
    print(f"API credits used: {credits_used}")

    return scored


if __name__ == "__main__":
    show_all = "--all" in sys.argv
    daily = "--daily" in sys.argv
    no_deep = "--shallow" in sys.argv
    run_search(show_all=show_all, daily=daily, deep=not no_deep)
