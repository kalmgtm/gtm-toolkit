# GTM Toolkit

Tools for building and running B2B outbound pipeline -- from account scoring to call research to reply handling to job search automation.

Built with Python, Claude API (Anthropic), Exa API, and Streamlit.

## Tools

### Lead Scorer
Score companies against any ICP using website scraping, Exa news search, and hiring signal detection. Claude grades each company 0-100 with a verdict, top signal, and reasoning.

- **Input:** CSV of company domains or paste directly
- **Output:** Ranked, scored CSV with grades (A/B+/B/C/D/F)
- **Speed:** 5 companies in ~63 seconds
- **Run:** `python3 lead-scorer/lead_scorer.py leads.csv --icp "your ICP here"`
- **UI:** `streamlit run lead-scorer/lead_scorer_app.py`

### Call Intel
Pre-call research briefs in under 40 seconds. Runs 5 parallel research prompts (website scrape, company news, broader news, hiring signals, person content) and synthesizes into a single brief.

- **Input:** Company domain + optional person name
- **Output:** Research brief saved to `call_briefs/`
- **Speed:** ~37 seconds per company
- **Run:** `python3 call-intel/call_intel.py mntn.com --person "Andrew Celaya"`
- **UI:** `streamlit run call-intel/call_intel_app.py`

### Reply Classifier
Classifies inbound replies into 10 categories (INTERESTED, REFERRAL, OBJECTION, OOO, BOUNCE, etc.), extracts key details, and generates suggested reply drafts. Priority-sorted.

- **Input:** Single reply (paste) or batch CSV
- **Output:** Classified CSV sorted by priority
- **Accuracy:** 10/10 on test set
- **Run:** `python3 reply-classifier/reply_classifier.py replies.csv`
- **UI:** `streamlit run reply-classifier/reply_classifier_app.py`

### Job Search
Automated GTM engineer job search across 60+ queries. Multi-stage scoring (quick score + deep page analysis), job type classification, and personalized outreach strategy per role.

- **Input:** Runs automatically against configured queries
- **Output:** Ranked results with scores, reasons, and "lead with" recommendations
- **Run:** `python3 job-search/job_search.py` (add `--daily` for email digest)

## Setup

```bash
git clone https://github.com/kalmgtm/gtm-toolkit.git
cd gtm-toolkit
cp .env.example .env
# Add your API keys to .env
pip install requests anthropic exa-py streamlit beautifulsoup4 python-dotenv
```

## Stack
- **AI:** Claude API (Anthropic) for scoring, synthesis, classification, and email generation
- **Research:** Exa API for real-time company news, hiring signals, and person content
- **Search:** Serper API for job board queries
- **UI:** Streamlit for interactive dashboards
- **Language:** Python
