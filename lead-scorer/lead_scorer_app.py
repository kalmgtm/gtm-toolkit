#!/usr/bin/env python3
"""
Lead Scorer Agent — Streamlit UI
Run: streamlit run lead_scorer_app.py
"""

import streamlit as st
import pandas as pd
import time
import os
from datetime import datetime

from lead_scorer import (
    normalize_domain,
    process_batch,
    DEFAULT_ICP,
)

st.set_page_config(page_title="Lead Scorer", layout="wide")

# --- CSS ---
st.markdown("""
<style>
    .stApp { background-color: #0e1117; }
    .subtitle { color: #8b949e; font-size: 16px; margin-bottom: 16px; }
    .stTextInput label, .stTextArea label, .stFileUploader label { color: #8b949e !important; }
    .metric-card {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 16px;
        text-align: center;
    }
    .metric-value {
        font-size: 28px;
        font-weight: 700;
        color: #58a6ff;
    }
    .metric-label {
        font-size: 12px;
        color: #8b949e;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    h1 { color: #e6edf3 !important; }

    /* Score colors in dataframe */
    .score-a { color: #3fb950; font-weight: 700; }
    .score-b { color: #58a6ff; font-weight: 700; }
    .score-c { color: #d29922; font-weight: 700; }
    .score-d { color: #f85149; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

# --- Header ---
st.title("Lead Scorer")
st.markdown('<p class="subtitle">Upload a CSV of company domains. Get them scored, ranked, and enriched against your ICP.</p>', unsafe_allow_html=True)

# --- Inputs ---
col1, col2 = st.columns([1, 1])

with col1:
    uploaded_file = st.file_uploader("Upload CSV (needs a 'domain', 'website', or 'url' column)", type=["csv"])

    manual_domains = st.text_area(
        "Or paste domains (one per line)",
        placeholder="mntn.com\nstripe.com\nramp.com",
        height=120,
    )

with col2:
    icp_input = st.text_area(
        "ICP criteria (edit or replace the default)",
        value=DEFAULT_ICP,
        height=250,
    )

run_button = st.button("Score Leads", type="primary", use_container_width=True)

# --- Process ---
if run_button:
    # Collect domains from file or text input
    domains = []

    if uploaded_file:
        import csv
        import io
        content = uploaded_file.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))

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
                d = normalize_domain(raw)
                if d:
                    domains.append(d)

    if manual_domains.strip():
        for line in manual_domains.strip().split("\n"):
            d = normalize_domain(line.strip())
            if d:
                domains.append(d)

    # Dedupe
    domains = list(dict.fromkeys(domains))

    if not domains:
        st.warning("No domains found. Upload a CSV or paste domains above.")
    else:
        st.markdown(f"**Scoring {len(domains)} companies...**")

        progress_bar = st.progress(0)
        status_text = st.empty()
        total_steps = len(domains) * 2  # research + scoring

        steps_done = {"count": 0}

        def progress_callback(phase, current, total, domain):
            if phase == "research":
                steps_done["count"] = current
            else:
                steps_done["count"] = total + current
            progress_bar.progress(min(steps_done["count"] / total_steps, 1.0))
            status_text.markdown(f"`{phase}` — {domain} ({current}/{total})")

        start = time.time()
        scored = process_batch(domains, icp_input, max_workers=3, progress_callback=progress_callback)
        elapsed = time.time() - start

        progress_bar.progress(1.0)
        status_text.empty()

        # --- Metrics ---
        strong = sum(1 for s in scored if s.get("score", 0) >= 80)
        good = sum(1 for s in scored if 60 <= s.get("score", 0) < 80)
        weak = sum(1 for s in scored if s.get("score", 0) < 60)

        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-value">{len(domains)}</div>
                <div class="metric-label">Companies</div>
            </div>""", unsafe_allow_html=True)
        with m2:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-value">{elapsed:.0f}s</div>
                <div class="metric-label">Total Time</div>
            </div>""", unsafe_allow_html=True)
        with m3:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-value" style="color: #3fb950;">{strong}</div>
                <div class="metric-label">Strong Fit</div>
            </div>""", unsafe_allow_html=True)
        with m4:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-value" style="color: #58a6ff;">{good}</div>
                <div class="metric-label">Good Fit</div>
            </div>""", unsafe_allow_html=True)
        with m5:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-value" style="color: #f85149;">{weak}</div>
                <div class="metric-label">Weak / DQ</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("---")

        # --- Results table ---
        display_fields = ["score", "grade", "verdict", "company_name", "domain",
                          "what_they_do", "industry", "estimated_size", "top_signal", "reason"]

        df = pd.DataFrame(scored)
        # Only show columns that exist
        display_cols = [c for c in display_fields if c in df.columns]
        df = df[display_cols]

        def color_score(val):
            try:
                v = int(val)
            except (ValueError, TypeError):
                return ""
            if v >= 80:
                return "color: #3fb950; font-weight: 700"
            elif v >= 60:
                return "color: #58a6ff; font-weight: 700"
            elif v >= 50:
                return "color: #d29922; font-weight: 700"
            else:
                return "color: #f85149; font-weight: 700"

        def color_verdict(val):
            if val == "STRONG FIT":
                return "color: #3fb950; font-weight: 700"
            elif val == "GOOD FIT":
                return "color: #58a6ff; font-weight: 700"
            elif val == "WEAK FIT":
                return "color: #d29922; font-weight: 700"
            else:
                return "color: #f85149; font-weight: 700"

        styled = df.style.map(color_score, subset=["score"] if "score" in df.columns else [])
        if "verdict" in df.columns:
            styled = styled.map(color_verdict, subset=["verdict"])

        st.dataframe(styled, use_container_width=True, height=400)

        # --- Download ---
        csv_data = df.to_csv(index=False)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            label="Download Scored CSV",
            data=csv_data,
            file_name=f"scored_leads_{timestamp}.csv",
            mime="text/csv",
        )
