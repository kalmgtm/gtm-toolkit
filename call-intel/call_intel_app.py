#!/usr/bin/env python3
"""
Call Intel Agent — Streamlit UI
Run: streamlit run call_intel_app.py
"""

import streamlit as st
import time
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

# Import the engine
from call_intel import (
    normalize_domain,
    scrape_website,
    exa_company_news,
    exa_broader_news,
    exa_hiring_signals,
    exa_person_content,
    synthesize,
)

st.set_page_config(page_title="Call Intel", page_icon="", layout="wide")

# --- Custom CSS ---
st.markdown("""
<style>
    .stApp { background-color: #0e1117; }
    .brief-output {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 24px;
        font-family: 'SF Mono', 'Fira Code', monospace;
        font-size: 14px;
        line-height: 1.6;
        white-space: pre-wrap;
        color: #e6edf3;
    }
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
    .source-tag {
        display: inline-block;
        background-color: #1f6feb;
        color: white;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 12px;
        margin: 2px;
    }
    .source-tag.done {
        background-color: #238636;
    }
    .source-tag.error {
        background-color: #da3633;
    }
    h1 { color: #e6edf3 !important; }
    .stTextInput > div > div > input {
        background-color: #161b22 !important;
        color: #e6edf3 !important;
        border: 1px solid #30363d !important;
    }
    /* Lighter subtitle text */
    .subtitle { color: #8b949e; font-size: 16px; margin-bottom: 16px; }
    /* Lighter input labels */
    .stTextInput label { color: #8b949e !important; }
</style>
""", unsafe_allow_html=True)

# --- Header ---
st.title("Call Intel")
st.markdown('<p class="subtitle">Account research in 30 seconds. Paste a URL, get a call brief.</p>', unsafe_allow_html=True)

# --- Input ---
col1, col2 = st.columns([2, 1])
with col1:
    url_input = st.text_input("Company website", placeholder="mntn.com")
with col2:
    person_input = st.text_input("Target person (optional)", placeholder="Andrew Celaya")

run_button = st.button("Research", type="primary", use_container_width=True)

# --- Run ---
if run_button and url_input:
    domain = normalize_domain(url_input)
    company_name = domain.split(".")[0].upper()
    person_name = person_input.strip() if person_input else None

    # Progress tracking
    status_container = st.container()
    progress_bar = st.progress(0)
    status_text = st.empty()

    start = time.time()
    sources_done = []
    total_sources = 5

    def track_result(future, label, results_list):
        try:
            result = future.result()
            results_list.append(result)
            sources_done.append(label)
            return label, "done"
        except Exception as e:
            results_list.append({"source": label, "data": [], "error": str(e)})
            sources_done.append(label)
            return label, "error"

    status_text.markdown("Gathering intelligence from 5 sources...")

    # Fire all research in parallel
    results = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(scrape_website, domain): "Website",
            pool.submit(exa_company_news, domain, company_name): "Company News",
            pool.submit(exa_broader_news, company_name): "External News",
            pool.submit(exa_hiring_signals, domain, company_name): "Hiring Signals",
            pool.submit(exa_person_content, person_name, company_name): "Person Content",
        }
        for future in as_completed(futures):
            label = futures[future]
            track_result(future, label, results)
            progress_bar.progress(len(sources_done) / total_sources)
            status_text.markdown(f"Completed: {', '.join(sources_done)}")

    research_time = time.time() - start

    # Synthesize
    status_text.markdown("Synthesizing call brief...")
    progress_bar.progress(0.85)

    brief = synthesize(results, domain, person_name)

    total_time = time.time() - start
    progress_bar.progress(1.0)

    # Save to file
    output_dir = os.path.join(os.path.dirname(__file__), "call_briefs")
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{domain.replace('.', '_')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w") as f:
        f.write(brief)

    # Clear progress
    status_text.empty()
    progress_bar.empty()

    # --- Metrics row ---
    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(f"""<div class="metric-card">
            <div class="metric-value">{total_time:.0f}s</div>
            <div class="metric-label">Total Time</div>
        </div>""", unsafe_allow_html=True)
    with m2:
        st.markdown(f"""<div class="metric-card">
            <div class="metric-value">{research_time:.1f}s</div>
            <div class="metric-label">Research</div>
        </div>""", unsafe_allow_html=True)
    with m3:
        st.markdown(f"""<div class="metric-card">
            <div class="metric-value">{total_time - research_time:.1f}s</div>
            <div class="metric-label">Synthesis</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("---")

    # --- Brief output ---
    st.markdown(f'<div class="brief-output">{brief}</div>', unsafe_allow_html=True)

    # --- Download button ---
    st.download_button(
        label="Download Brief",
        data=brief,
        file_name=filename,
        mime="text/plain",
    )

elif run_button and not url_input:
    st.warning("Enter a company URL or domain.")
