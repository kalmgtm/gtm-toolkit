# ICP Enrichment Pipeline

Waterfall enrichment + ICP scoring from a CSV of company domains.

Researches each company through multiple sources (website scrape, Exa news, Exa hiring signals), extracts structured enrichment data using Claude, scores against your ICP criteria, and outputs a clean CSV.

## How it works

```
CSV of domains
    |
    v
[Website Scrape] --> company description, meta, page text
    |
    v
[Exa News Search] --> funding, product launches, partnerships (last 90 days)
    |
    v
[Exa Hiring Signals] --> open roles, team growth indicators
    |
    v
[Claude Enrichment + Scoring] --> structured company data + ICP score
    |
    v
Enriched CSV: name, industry, size, funding, tech stack, key people,
              AI signals, hiring roles, news, score, grade, verdict
```

## Usage

```bash
# From a CSV
python3 enrich.py domains.csv

# With custom ICP
python3 enrich.py domains.csv --icp "Series B+ fintech, 100-500 employees, US-based"

# Comma-separated domains (no CSV needed)
python3 enrich.py "ramp.com,brex.com,anduril.com"

# Full output with JSON
python3 enrich.py domains.csv --output results.csv --json --workers 5
```

## Output columns

| Column | Description |
|---|---|
| score | 0-100 ICP fit score |
| grade | A / B+ / B / C / D / F |
| verdict | STRONG FIT / GOOD FIT / WEAK FIT / DISQUALIFIED |
| company_name | Official company name |
| domain | Company domain |
| one_liner | What the company does |
| industry | Primary vertical |
| sub_industry | More specific category |
| employee_estimate | Headcount or range |
| funding_stage | Seed / Series A / B / C / Public / Bootstrapped |
| hq_location | City, State or Country |
| tech_stack_signals | Technologies and platforms detected |
| recent_news | Key developments from last 90 days |
| hiring_roles | Notable open positions |
| key_people | Leadership names + titles |
| ai_signals | Evidence of AI/automation deployment |
| top_signal | Single strongest ICP fit signal |
| reason | Score explanation |
| waterfall | Which sources returned data |

## Setup

```bash
pip install requests anthropic exa-py beautifulsoup4 python-dotenv
cp ../.env.example ../.env
# Add your API keys
```

## Stack

- **Research:** Website scraping + Exa API (news + hiring signals)
- **Enrichment + Scoring:** Claude Sonnet 4.6 with prompt caching
- **Speed:** ~12 seconds per company (8 companies in ~95 seconds)
