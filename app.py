"""
app.py — Bank Statement Extractor (Streamlit Web App)
──────────────────────────────────────────────────────
Upload a PDF bank statement and instantly extract all key fields.
Supports both digital PDFs (text-based) and scanned PDFs (OCR fallback).

Run locally:
    streamlit run app.py

Deploy to Streamlit Cloud:
    Push this folder to GitHub → connect at share.streamlit.io
"""

import re
import json
import tempfile
import os
import subprocess
from pathlib import Path

import streamlit as st
import pdfplumber

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Bank Statement Extractor",
    page_icon="🏦",
    layout="wide",
)

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main { background-color: #f8fafc; }
    .stMetric { background: white; border-radius: 10px; padding: 10px; border: 1px solid #e2e8f0; }
    .field-card {
        background: white;
        border-radius: 10px;
        padding: 16px 20px;
        margin: 6px 0;
        border-left: 4px solid #2563eb;
        box-shadow: 0 1px 3px rgba(0,0,0,0.07);
    }
    .field-label { font-size: 11px; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
    .field-value { font-size: 16px; color: #1e293b; font-weight: 500; margin-top: 2px; }
    .section-header {
        font-size: 13px; font-weight: 700; color: #475569;
        text-transform: uppercase; letter-spacing: 0.08em;
        margin: 20px 0 8px 0;
    }
    .balance-card {
        background: linear-gradient(135deg, #1e40af, #2563eb);
        border-radius: 12px;
        padding: 18px 22px;
        color: white;
        text-align: center;
    }
    .balance-label { font-size: 11px; opacity: 0.8; text-transform: uppercase; letter-spacing: 0.06em; }
    .balance-value { font-size: 22px; font-weight: 700; margin-top: 4px; }
    .txn-table { font-size: 13px; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  EXTRACTION LOGIC (same as extract_bank_statement_pdf.py)
# ══════════════════════════════════════════════════════════════════════════════

def extract_text_direct(pdf_path):
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3)
            if t:
                pages.append(t)
    return "\n".join(pages)


def extract_text_ocr(pdf_path):
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageFilter
    except ImportError:
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftoppm", "-jpeg", "-r", "200", pdf_path, os.path.join(tmp, "page")],
            check=True, capture_output=True
        )
        pages = []
        for img_path in sorted(Path(tmp).glob("page-*.jpg")):
            img = Image.open(img_path).convert("L")
            img = ImageEnhance.Contrast(img).enhance(2.0)
            img = img.filter(ImageFilter.SHARPEN)
            pages.append(pytesseract.image_to_string(img, config="--psm 6"))
    return "\n".join(pages)


def get_text(pdf_path):
    text = extract_text_direct(pdf_path)
    if len(text.strip()) >= 50:
        return text, "Direct text (pdfplumber)"
    return extract_text_ocr(pdf_path), "OCR fallback (pytesseract)"


def get_bank_name(text):
    known = ['EverBank', 'Chase', 'Wells Fargo', 'Bank of America', 'Citibank',
             'First National Bank', 'TD Bank', 'US Bank', 'Capital One',
             'PNC Bank', 'Regions Bank', 'SunTrust']
    for name in known:
        if re.search(re.escape(name), text, re.I):
            return name
    for line in text.splitlines()[:5]:
        line = line.strip()
        if line and not re.match(r'^\d', line):
            return line
    return "Unknown"


def get_account_holder(text):
    m = re.search(r'Account\s+Holder:\s*(.+?)\s+(?:Statement\s+Period|Statement\s+Date)', text, re.I)
    if m:
        return m.group(1).strip()
    for line in text.splitlines():
        if re.match(r'^[A-Z][A-Z\s\.]{5,}$', line.strip()):
            return line.strip()
    return None


def get_account_number(text):
    m = re.search(r'Account\s+(?:Number|No\.?|#):\s*([*\dXx\-\s]{5,20}?)\s+(?:[A-Z]|\n)', text, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r'(?:\*{4}|X{4})[-\s]?(?:\*{4}|X{4})[-\s]?\d{4}', text)
    if m:
        return m.group(0)
    m = re.search(r'\b\d{7,18}\b', text)
    return m.group(0) if m else None


def get_account_type(text):
    m = re.search(r'Account\s+Type:\s*(.+?)\s+(?:Branch|Currency|$)', text, re.I | re.M)
    return m.group(1).strip() if m else None


def get_routing_number(text):
    m = re.search(r'Routing\s+(?:Number|No\.?):\s*(\d{9})', text, re.I)
    return m.group(1) if m else None


def get_statement_date(text):
    m = re.search(
        r'Statement\s+Date:\s*'
        r'((?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+\d{1,2},\s*\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        text, re.I)
    return m.group(1).strip() if m else None


def get_statement_period(text):
    m = re.search(
        r'Statement\s+Period:\s*'
        r'([\w]+ \d{1,2},?\s*\d{4}\s*[–\-]\s*[\w]+ \d{1,2},?\s*\d{4})',
        text, re.I)
    return m.group(1).strip() if m else None


def get_balances(text):
    b = {"opening_balance": None, "total_credits": None,
         "total_debits": None,   "closing_balance": None}
    m = re.search(
        r'\$([\d,]+\.\d{2})\s+\+\s*\$([\d,]+\.\d{2})\s+-\s*\$([\d,]+\.\d{2})\s+\$([\d,]+\.\d{2})',
        text)
    if m:
        b["opening_balance"] = f"${m.group(1)}"
        b["total_credits"]   = f"${m.group(2)}"
        b["total_debits"]    = f"${m.group(3)}"
        b["closing_balance"] = f"${m.group(4)}"
        return b
    def amt(pat):
        hit = re.search(pat, text, re.I | re.S)
        return f"${hit.group(1)}" if hit else None
    b["opening_balance"] = amt(r'Opening\s+Balance[\s\S]{0,30}?\$([\d,]+\.\d{2})')
    b["closing_balance"] = amt(r'Closing\s+Balance[\s\S]{0,30}?\$([\d,]+\.\d{2})')
    b["total_credits"]   = amt(r'Total\s+Credits?[\s\S]{0,20}?\$([\d,]+\.\d{2})')
    b["total_debits"]    = amt(r'Total\s+Debits?[\s\S]{0,20}?\$([\d,]+\.\d{2})')
    return b


def get_transactions(text):
    MONTHS = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
    txns   = []
    SKIP   = {'Opening Balance', 'Closing Balance', 'Date', 'Description'}
    row_pat = re.compile(
        rf'^({MONTHS})\s+(\d{{1,2}})\s+'
        r'(.+?)'
        r'(?:\s+([A-Z]{2,3}\d{6,}))?'
        r'(?:\s+\$([\d,]+\.\d{2}))?'
        r'(?:\s+\$([\d,]+\.\d{2}))?'
        r'\s+\$([\d,]+\.\d{2})\s*$',
        re.MULTILINE
    )
    credit_kw = re.compile(r'deposit|credit|salary|refund|interest|transfer\s+in', re.I)
    for m in row_pat.finditer(text):
        desc = m.group(3).strip()
        if desc in SKIP:
            continue
        ref  = m.group(4) or ""
        amt1 = m.group(5)
        amt2 = m.group(6)
        if ref:
            if credit_kw.search(desc):
                debit, credit = "", (f"${amt1}" if amt1 else "")
            else:
                debit, credit = (f"${amt1}" if amt1 else ""), (f"${amt2}" if amt2 else "")
        else:
            debit, credit = "", ""
        txns.append({
            "Date":        f"{m.group(1)} {m.group(2).zfill(2)}",
            "Description": desc,
            "Reference":   ref,
            "Debit":       debit,
            "Credit":      credit,
            "Balance":     f"${m.group(7)}",
        })
    return txns


def extract(pdf_path):
    text, method = get_text(pdf_path)
    balances     = get_balances(text)
    txns         = get_transactions(text)
    details = {
        "bank_name":        get_bank_name(text),
        "account_holder":   get_account_holder(text),
        "account_number":   get_account_number(text),
        "account_type":     get_account_type(text),
        "routing_number":   get_routing_number(text),
        "statement_date":   get_statement_date(text),
        "statement_period": get_statement_period(text),
        **balances,
        "transactions":     txns,
    }
    return details, method, text


# ══════════════════════════════════════════════════════════════════════════════
#  STREAMLIT UI
# ══════════════════════════════════════════════════════════════════════════════

def field_card(label, value):
    val = value if value else "—"
    st.markdown(f"""
    <div class="field-card">
        <div class="field-label">{label}</div>
        <div class="field-value">{val}</div>
    </div>""", unsafe_allow_html=True)


def balance_card(label, value):
    val = value if value else "—"
    st.markdown(f"""
    <div class="balance-card">
        <div class="balance-label">{label}</div>
        <div class="balance-value">{val}</div>
    </div>""", unsafe_allow_html=True)


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("# 🏦 Bank Statement Extractor")
st.markdown("Upload a PDF bank statement to instantly extract all key fields and transactions.")
st.divider()

# ── Upload ────────────────────────────────────────────────────────────────────
uploaded = st.file_uploader(
    "📂 Upload Bank Statement PDF",
    type=["pdf"],
    help="Supports both digital PDFs and scanned/image PDFs (OCR)"
)

if not uploaded:
    st.info("👆 Upload a PDF to get started. Your file is processed locally and never stored.")
    st.stop()

# ── Process ───────────────────────────────────────────────────────────────────
with st.spinner("🔍 Extracting data from PDF..."):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded.read())
        tmp_path = tmp.name

    try:
        details, method, raw_text = extract(tmp_path)
    finally:
        os.unlink(tmp_path)

st.success(f"✅ Extraction complete — method used: **{method}**")

# ── Layout: two columns ───────────────────────────────────────────────────────
col_left, col_right = st.columns([1, 1], gap="large")

with col_left:
    st.markdown('<div class="section-header">🏦 Bank & Account Info</div>', unsafe_allow_html=True)
    field_card("Bank Name",       details["bank_name"])
    field_card("Account Holder",  details["account_holder"])
    field_card("Account Number",  details["account_number"])
    field_card("Account Type",    details["account_type"])
    field_card("Routing Number",  details["routing_number"])

with col_right:
    st.markdown('<div class="section-header">📅 Statement Info</div>', unsafe_allow_html=True)
    field_card("Statement Date",   details["statement_date"])
    field_card("Statement Period", details["statement_period"])

    st.markdown('<div class="section-header">💰 Balance Summary</div>', unsafe_allow_html=True)
    b1, b2 = st.columns(2)
    with b1:
        balance_card("Opening Balance", details["opening_balance"])
    with b2:
        balance_card("Closing Balance", details["closing_balance"])

    b3, b4 = st.columns(2)
    with b3:
        balance_card("Total Credits", details["total_credits"])
    with b4:
        balance_card("Total Debits",  details["total_debits"])

# ── Transactions ──────────────────────────────────────────────────────────────
st.divider()
txns = details["transactions"]
st.markdown(f'<div class="section-header">📋 Transaction History ({len(txns)} transactions)</div>',
            unsafe_allow_html=True)

if txns:
    import pandas as pd
    df = pd.DataFrame(txns)
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={
                     "Date":        st.column_config.TextColumn("Date",        width="small"),
                     "Description": st.column_config.TextColumn("Description", width="large"),
                     "Reference":   st.column_config.TextColumn("Reference",   width="medium"),
                     "Debit":       st.column_config.TextColumn("Debit",       width="small"),
                     "Credit":      st.column_config.TextColumn("Credit",      width="small"),
                     "Balance":     st.column_config.TextColumn("Balance",     width="small"),
                 })
else:
    st.warning("No transactions found.")

# ── Downloads ─────────────────────────────────────────────────────────────────
st.divider()
st.markdown("### ⬇️ Download Results")

dl1, dl2 = st.columns(2)

with dl1:
    json_out = {k: v for k, v in details.items()}
    st.download_button(
        label="📥 Download JSON",
        data=json.dumps(json_out, indent=2),
        file_name=f"{uploaded.name.replace('.pdf', '')}_extracted.json",
        mime="application/json",
        use_container_width=True,
    )

with dl2:
    if txns:
        import pandas as pd
        csv_data = pd.DataFrame(txns).to_csv(index=False)
        st.download_button(
            label="📥 Download Transactions CSV",
            data=csv_data,
            file_name=f"{uploaded.name.replace('.pdf', '')}_transactions.csv",
            mime="text/csv",
            use_container_width=True,
        )

# ── Raw text expander ─────────────────────────────────────────────────────────
with st.expander("🔍 View raw extracted text (debug)"):
    st.code(raw_text, language="text")
