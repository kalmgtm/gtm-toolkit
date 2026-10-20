#!/usr/bin/env python3
"""
Reply Classifier Agent — Streamlit UI
Run: streamlit run reply_classifier_app.py
"""

import streamlit as st
import pandas as pd
import time
import json
from datetime import datetime
from collections import Counter

from reply_classifier import classify_reply, classify_batch, CATEGORIES

st.set_page_config(page_title="Reply Classifier", layout="wide")

# --- CSS ---
st.markdown("""
<style>
    .stApp { background-color: #0e1117; }
    .subtitle { color: #8b949e; font-size: 16px; margin-bottom: 16px; }
    .stTextInput label, .stTextArea label, .stFileUploader label { color: #8b949e !important; }
    .stRadio label { color: #8b949e !important; }
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
    .category-tag {
        display: inline-block;
        padding: 4px 12px;
        border-radius: 4px;
        font-weight: 700;
        font-size: 14px;
    }
    .cat-interested { background-color: #238636; color: white; }
    .cat-referral { background-color: #1f6feb; color: white; }
    .cat-question { background-color: #58a6ff; color: white; }
    .cat-objection { background-color: #d29922; color: white; }
    .cat-timing { background-color: #6e40c9; color: white; }
    .cat-not-interested { background-color: #da3633; color: white; }
    .cat-ooo { background-color: #484f58; color: white; }
    .cat-bounce { background-color: #484f58; color: white; }
    .cat-unsubscribe { background-color: #da3633; color: white; }
    .cat-noise { background-color: #30363d; color: #8b949e; }
    .result-card {
        background-color: #161b22;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 20px;
        margin-bottom: 12px;
    }
    .result-action {
        background-color: #0d1117;
        border-left: 3px solid #58a6ff;
        padding: 8px 12px;
        margin-top: 8px;
        font-size: 13px;
        color: #8b949e;
    }
    .suggested-reply {
        background-color: #0d1117;
        border-left: 3px solid #238636;
        padding: 8px 12px;
        margin-top: 8px;
        font-size: 13px;
        color: #c9d1d9;
    }
</style>
""", unsafe_allow_html=True)

# --- Header ---
st.title("Reply Classifier")
st.markdown('<p class="subtitle">Paste a reply or upload a CSV. Get instant classification, priority routing, and suggested next action.</p>', unsafe_allow_html=True)

# --- Mode selector ---
mode = st.radio("Mode", ["Single Reply", "Batch (CSV)"], horizontal=True)

CAT_COLORS = {
    "INTERESTED": "interested",
    "INTERESTED - TIMING": "timing",
    "REFERRAL": "referral",
    "QUESTION": "question",
    "OBJECTION": "objection",
    "NOT INTERESTED": "not-interested",
    "OOO": "ooo",
    "BOUNCE": "bounce",
    "UNSUBSCRIBE": "unsubscribe",
    "NOISE": "noise",
}

if mode == "Single Reply":
    col1, col2 = st.columns([2, 1])

    with col1:
        reply_text = st.text_area(
            "Paste the reply",
            placeholder='e.g. "Hey, this is interesting — can you send me more info? We\'re evaluating tools like this for Q1."',
            height=150,
        )
    with col2:
        sender_name = st.text_input("Sender name (optional)", placeholder="John Smith")
        company = st.text_input("Company (optional)", placeholder="Acme Corp")
        original_email = st.text_area("Original outbound (optional)", placeholder="Your original email...", height=68)

    classify_button = st.button("Classify", type="primary", use_container_width=True)

    if classify_button and reply_text.strip():
        with st.spinner("Classifying..."):
            start = time.time()
            result = classify_reply(
                reply_text=reply_text,
                original_email=original_email if original_email else None,
                sender_name=sender_name if sender_name else None,
                company=company if company else None,
            )
            elapsed = time.time() - start

        cat = result.get("category", "NOISE")
        cat_class = CAT_COLORS.get(cat, "noise")
        confidence = result.get("confidence", 0)
        sentiment = result.get("sentiment", "neutral")

        # Metrics
        m1, m2, m3 = st.columns(3)
        with m1:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-value">{elapsed:.1f}s</div>
                <div class="metric-label">Time</div>
            </div>""", unsafe_allow_html=True)
        with m2:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-value">{confidence:.0%}</div>
                <div class="metric-label">Confidence</div>
            </div>""", unsafe_allow_html=True)
        with m3:
            st.markdown(f"""<div class="metric-card">
                <div class="metric-value" style="font-size: 20px;">P{result.get('priority', '?')}</div>
                <div class="metric-label">Priority</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("---")

        # Result card
        st.markdown(f"""<div class="result-card">
            <span class="category-tag cat-{cat_class}">{cat}</span>
            &nbsp;&nbsp;<span style="color: #8b949e;">Sentiment: {sentiment}</span>
            <h4 style="color: #e6edf3; margin-top: 12px;">{result.get('summary', '')}</h4>
            <div class="result-action"><strong>Action:</strong> {result.get('action', result.get('default_action', 'N/A'))}</div>
        </div>""", unsafe_allow_html=True)

        # Suggested reply
        suggested = result.get("suggested_reply")
        if suggested:
            st.markdown(f"""<div class="suggested-reply"><strong>Suggested reply:</strong><br>{suggested}</div>""", unsafe_allow_html=True)

        # Key details
        details = result.get("key_details", {})
        if details and any(v for v in details.values() if v):
            st.markdown("**Key Details:**")
            for k, v in details.items():
                if v:
                    st.markdown(f"- **{k.replace('_', ' ').title()}:** {v}")

    elif classify_button:
        st.warning("Paste a reply to classify.")

else:
    # Batch mode
    col1, col2 = st.columns([1, 1])

    with col1:
        uploaded_file = st.file_uploader(
            "Upload CSV (needs 'reply_text', 'reply', 'body', or 'message' column)",
            type=["csv"],
        )
    with col2:
        st.markdown("""
        **Optional columns:**
        - `sender_name` or `name` — who sent it
        - `company` — their company
        - `original_email` — your outbound email
        """)

    batch_button = st.button("Classify All", type="primary", use_container_width=True)

    if batch_button and uploaded_file:
        import csv
        import io

        content = uploaded_file.read().decode("utf-8")
        reader = csv.DictReader(io.StringIO(content))

        # Find columns
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

        name_col = company_col = original_col = None
        for h in reader.fieldnames:
            hl = h.lower().strip()
            if hl in ("sender_name", "name", "from", "sender"):
                name_col = h
            elif hl in ("company", "company_name", "organization"):
                company_col = h
            elif hl in ("original_email", "original", "outbound"):
                original_col = h

        replies = []
        for row in reader:
            reply_text = row.get(text_col, "").strip()
            if reply_text:
                replies.append({
                    "reply_text": reply_text,
                    "sender_name": row.get(name_col, "") if name_col else "",
                    "company": row.get(company_col, "") if company_col else "",
                    "original_email": row.get(original_col, "") if original_col else "",
                })

        if not replies:
            st.warning("No replies found in CSV.")
        else:
            st.markdown(f"**Classifying {len(replies)} replies...**")
            progress_bar = st.progress(0)
            status_text = st.empty()

            def progress_callback(current, total, name):
                progress_bar.progress(current / total)
                status_text.markdown(f"Classifying {current}/{total} — {name}")

            start = time.time()
            results = classify_batch(replies, progress_callback=progress_callback)
            elapsed = time.time() - start

            progress_bar.progress(1.0)
            status_text.empty()

            # Category counts
            counts = Counter(r.get("category", "?") for r in results)
            interested = counts.get("INTERESTED", 0) + counts.get("INTERESTED - TIMING", 0) + counts.get("REFERRAL", 0)
            actionable = interested + counts.get("QUESTION", 0) + counts.get("OBJECTION", 0)
            ignore = counts.get("NOISE", 0) + counts.get("OOO", 0) + counts.get("BOUNCE", 0)

            # Metrics
            m1, m2, m3, m4, m5 = st.columns(5)
            with m1:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-value">{len(replies)}</div>
                    <div class="metric-label">Replies</div>
                </div>""", unsafe_allow_html=True)
            with m2:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-value">{elapsed:.0f}s</div>
                    <div class="metric-label">Total Time</div>
                </div>""", unsafe_allow_html=True)
            with m3:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-value" style="color: #3fb950;">{interested}</div>
                    <div class="metric-label">Interested</div>
                </div>""", unsafe_allow_html=True)
            with m4:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-value" style="color: #58a6ff;">{actionable}</div>
                    <div class="metric-label">Actionable</div>
                </div>""", unsafe_allow_html=True)
            with m5:
                st.markdown(f"""<div class="metric-card">
                    <div class="metric-value" style="color: #484f58;">{ignore}</div>
                    <div class="metric-label">Ignore</div>
                </div>""", unsafe_allow_html=True)

            st.markdown("---")

            # Results table
            display_fields = ["priority", "category", "confidence", "sentiment",
                              "sender_name", "company", "summary", "action", "reply_text"]
            df = pd.DataFrame(results)
            display_cols = [c for c in display_fields if c in df.columns]
            df = df[display_cols]

            def color_category(val):
                colors = {
                    "INTERESTED": "#3fb950",
                    "INTERESTED - TIMING": "#6e40c9",
                    "REFERRAL": "#1f6feb",
                    "QUESTION": "#58a6ff",
                    "OBJECTION": "#d29922",
                    "NOT INTERESTED": "#da3633",
                    "UNSUBSCRIBE": "#da3633",
                    "OOO": "#484f58",
                    "BOUNCE": "#484f58",
                    "NOISE": "#30363d",
                }
                color = colors.get(val, "#30363d")
                return f"color: {color}; font-weight: 700"

            styled = df.style
            if "category" in df.columns:
                styled = styled.map(color_category, subset=["category"])

            st.dataframe(styled, use_container_width=True, height=400)

            # Download
            csv_data = df.to_csv(index=False)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            st.download_button(
                label="Download Classified CSV",
                data=csv_data,
                file_name=f"classified_replies_{timestamp}.csv",
                mime="text/csv",
            )

    elif batch_button:
        st.warning("Upload a CSV first.")
