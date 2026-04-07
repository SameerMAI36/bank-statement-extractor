"""
app.py — Bank Statement Extractor (Streamlit Web App)
──────────────────────────────────────────────────────
Supports EverBank multi-page PDF format:
  Page 1: Account holder, address, account number, ending balance summary
  Page 2: Transaction history (Additions / Subtractions / Balance)
  Page 3: Legal/disclaimer (ignored)

Also supports generic bank statement formats (Chase, WF, BofA etc.)

Run:  streamlit run app.py
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

st.markdown("""
<style>
    .field-card {
        background: white;
        border-radius: 10px;
        padding: 14px 18px;
        margin: 5px 0;
        border-left: 4px solid #2563eb;
        box-shadow: 0 1px 3px rgba(0,0,0,0.07);
    }
    .field-label { font-size: 11px; color: #64748b; font-weight: 700;
                   text-transform: uppercase; letter-spacing: 0.05em; }
    .field-value { font-size: 15px; color: #1e293b; font-weight: 500; margin-top: 3px; }
    .balance-card {
        background: linear-gradient(135deg, #1e40af, #2563eb);
        border-radius: 12px; padding: 16px 20px;
        color: white; text-align: center; margin: 4px 0;
    }
    .balance-label { font-size: 11px; opacity: 0.8; text-transform: uppercase; letter-spacing: 0.06em; }
    .balance-value { font-size: 20px; font-weight: 700; margin-top: 4px; }
    .section-header { font-size: 12px; font-weight: 700; color: #475569;
        text-transform: uppercase; letter-spacing: 0.08em; margin: 18px 0 6px 0; }
    .warn-card { background:#fef9c3; border-left:4px solid #eab308;
        border-radius:8px; padding:10px 14px; font-size:13px; color:#713f12; margin:8px 0; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  TEXT EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def extract_pages_direct(pdf_path):
    """Return list of text strings, one per page."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            t = page.extract_text(x_tolerance=3, y_tolerance=3) or ""
            pages.append(t)
    return pages


def extract_pages_ocr(pdf_path):
    """OCR fallback for scanned PDFs — returns list of text per page."""
    try:
        import pytesseract
        from PIL import Image, ImageEnhance, ImageFilter
    except ImportError:
        return []
    pages = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["pdftoppm", "-jpeg", "-r", "200", pdf_path, os.path.join(tmp, "page")],
            check=True, capture_output=True
        )
        for img_path in sorted(Path(tmp).glob("page-*.jpg")):
            img = Image.open(img_path).convert("L")
            img = ImageEnhance.Contrast(img).enhance(2.0)
            img = img.filter(ImageFilter.SHARPEN)
            pages.append(pytesseract.image_to_string(img, config="--psm 6"))
    return pages


def get_pages(pdf_path):
    pages = extract_pages_direct(pdf_path)
    full  = "\n".join(pages)
    if len(full.strip()) >= 50:
        return pages, "Direct text (pdfplumber)"
    pages = extract_pages_ocr(pdf_path)
    return pages, "OCR fallback (pytesseract)"


# ══════════════════════════════════════════════════════════════════════════════
#  SHARED ADDRESS PARSER
# ══════════════════════════════════════════════════════════════════════════════

def parse_address_block(lines, holder_idx):
    """
    Extract and parse a structured mailing address starting from
    the line immediately after the account holder name.

    Handles:
      1-line  : 748 BAROSSA VALLEY DR NW  (street only, rare)
      2-line  : 748 BAROSSA VALLEY DR NW        (street)
                CONCORD NC 28027-8019            (city state zip)
      3-line  : APT 4B                           (apt/suite)
                123 MAIN STREET                  (street)
                NEW YORK NY 10001-2345           (city state zip)

    Returns dict with keys:
      apt_suite, street, city, state, zip, full_address
    """
    addr_lines = []
    stop_patterns = re.compile(
        r'^(January|February|March|April|May|June|July|August|'
        r'September|October|November|December|\d{1,2}[/\-]'
        r'|Statement|Page|Days|Inquir|Direct|Summary|Balance|EverBank)',
        re.I
    )

    for j in range(holder_idx + 1, min(holder_idx + 6, len(lines))):
        line = lines[j]
        if stop_patterns.match(line):
            break
        if re.match(r'^\d{3}[\.\-]\d{3}', line):   # phone number
            break
        if line:
            addr_lines.append(line)

    if not addr_lines:
        return {}

    result = {}

    # ── Parse City State ZIP from last line
    last = addr_lines[-1]
    csz  = re.match(r'^(.+?)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)\s*$', last)
    if csz:
        result["city"]   = csz.group(1).strip().title()
        result["state"]  = csz.group(2)
        result["zip"]    = csz.group(3)
        street_lines     = addr_lines[:-1]
    else:
        street_lines     = addr_lines

    # ── Parse street (and apt/suite if 2 lines)
    if len(street_lines) >= 2:
        result["apt_suite"] = street_lines[0].title()
        result["street"]    = street_lines[1].title()
    elif len(street_lines) == 1:
        result["street"]    = street_lines[0].title()

    # ── Compose full address
    parts = []
    if result.get("apt_suite"): parts.append(result["apt_suite"])
    if result.get("street"):    parts.append(result["street"])
    city_line = " ".join(filter(None, [
        result.get("city"), result.get("state"), result.get("zip")
    ]))
    if city_line: parts.append(city_line)
    result["full_address"] = ", ".join(parts)

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  EVERBANK-SPECIFIC EXTRACTORS  (multi-page format)
# ══════════════════════════════════════════════════════════════════════════════

def is_everbank(pages):
    return any(re.search(r'EverBank', p, re.I) for p in pages)


def parse_everbank(pages):
    """
    EverBank layout:
      Page 1 — header, address, account summary table
      Page 2 — transaction detail per account section
      Page 3 — legal disclaimer (skip)
    """
    p1 = pages[0] if len(pages) > 0 else ""
    p2 = pages[1] if len(pages) > 1 else ""

    result = {}

    # ── Bank name
    result["bank_name"] = "EverBank"

    # ── Statement reference number (appears next to "Statement of Account")
    m = re.search(r'Statement\s+of\s+Account\s*\n?\s*(\d{7,15})', p1, re.I)
    result["statement_reference_no"] = m.group(1) if m else None

    # ── Account holder — first ALL-CAPS line block in page 1
    holder = None
    for line in p1.splitlines():
        line = line.strip()
        # Match full name in ALL CAPS (2+ words, letters and spaces only)
        if re.match(r'^[A-Z]{2,}(?:\s+[A-Z]{2,})+$', line):
            holder = line
            break
    result["account_holder"] = holder

    # ── Mailing address — lines right after the holder name (fully parsed)
    lines = [l.strip() for l in p1.splitlines() if l.strip()]
    result["mailing_address"] = {}
    for i, line in enumerate(lines):
        if line == holder:
            result["mailing_address"] = parse_address_block(lines, i)
            break

    # ── Statement date
    m = re.search(
        r'((?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+\d{1,2},\s*\d{4})',
        p1, re.I)
    result["statement_date"] = m.group(1) if m else None

    # ── Statement period days
    m = re.search(r'Days\s+in\s+stmt\s+period[:\s]*(\d+)', p1, re.I)
    result["stmt_period_days"] = m.group(1) if m else None

    # ── Inquiry phone
    m = re.search(r'(\d{3}[\.\-]\d{3}[\.\-]\d{4})', p1)
    result["inquiry_phone"] = m.group(1) if m else None

    # ── Bank address
    m = re.search(
        r'EverBank\s*\n\s*(\d+[^\n]+)\n\s*([A-Za-z\s]+,\s*[A-Z]{2}\s*\d{5}(?:-\d{4})?)',
        p1, re.I)
    result["bank_address"] = f"{m.group(1).strip()}, {m.group(2).strip()}" if m else None

    # ── Summary table from page 1:
    #    Account | Number | Ending Balance
    accounts = []
    row_pat  = re.compile(r'^(.+?)\s+(\d{7,15})\s+\$?([\d,]+\.\d{1,2})\s*$')
    for line in p1.splitlines():
        m = row_pat.match(line.strip())
        if not m:
            continue
        acct_type = m.group(1).strip()
        if re.search(r'\b(account|number|balance)\b', acct_type, re.I) and len(acct_type.split()) <= 4:
            continue
        raw_bal = m.group(3)
        if len(raw_bal.split('.')[-1]) == 1:
            raw_bal += "0"
        accounts.append({
            "account_type":   acct_type,
            "account_number": m.group(2),
            "ending_balance": "$" + raw_bal,
        })
    result["accounts"] = accounts
    result["account_type"]   = accounts[0]["account_type"]   if accounts else None
    result["account_number"] = accounts[0]["account_number"] if accounts else None
    result["ending_balance"] = accounts[0]["ending_balance"] if accounts else None

    # ── Transactions from page 2
    #    Format: MM-DD  Description  [Additions]  [Subtractions]  Balance
    result["transactions"] = parse_everbank_transactions(p2)

    # ── Interest / APY details from page 2
    result["interest_details"] = parse_everbank_interest(p2)

    return result


def parse_everbank_transactions(text):
    """
    EverBank page 2 transaction format:
      12-31   Beginning balance                              $0.19
      03-12   # Internal Transfer Cr  FR ACC 01870899974   1,360.26          1,360.45
      03-31   # Interest Credit                                1.49           1,361.94
    Columns: Date | Description | Additions | Subtractions | Balance
    """
    txns    = []
    # Date pattern MM-DD
    row_pat = re.compile(
        r'^(\d{2}-\d{2})\s+'          # Date MM-DD
        r'(.+?)'                       # Description
        r'(?:\s+([\d,]+\.\d{2}))?'    # Additions (optional)
        r'(?:\s+([\d,]+\.\d{2}))?'    # Subtractions (optional)
        r'\s+([\d,]+\.\d{2})\s*$',    # Balance
        re.MULTILINE
    )
    skip = re.compile(r'^(Date|Description|Additions|Subtractions|Balance)', re.I)

    for m in row_pat.finditer(text):
        desc = m.group(2).strip()
        if skip.match(desc):
            continue
        txns.append({
            "Date":          m.group(1),
            "Description":   desc,
            "Additions":     "$" + m.group(3) if m.group(3) else "",
            "Subtractions":  "$" + m.group(4) if m.group(4) else "",
            "Balance":       "$" + m.group(5),
        })
    return txns


def parse_everbank_interest(text):
    """Extract APY / interest summary block from page 2."""
    info = {}
    patterns = {
        "interest_paid_ytd":     r'Interest\s+paid\s+YTD\s+\$?([\d,]+\.\d{2})',
        "apy_earned":            r'Annual\s+percentage\s+yield\s+earned\s+([\d\.]+%)',
        "interest_bearing_days": r'Interest[- ]bearing\s+days\s+(\d+)',
        "average_balance_apy":   r'Average\s+balance\s+for\s+APY\s+\$?([\d,]+\.\d{2})',
        "interest_earned":       r'Interest\s+earned\s+\$?([\d,]+\.\d{2})',
    }
    for key, pat in patterns.items():
        m = re.search(pat, text, re.I)
        info[key] = m.group(1) if m else None
    return info


# ══════════════════════════════════════════════════════════════════════════════
#  GENERIC EXTRACTOR (non-EverBank)
# ══════════════════════════════════════════════════════════════════════════════

def parse_generic(pages):
    text = "\n".join(pages)

    known_banks = ['Chase', 'Wells Fargo', 'Bank of America', 'Citibank',
                   'First National Bank', 'TD Bank', 'US Bank', 'Capital One',
                   'PNC Bank', 'Regions Bank', 'SunTrust']

    def get_bank():
        for name in known_banks:
            if re.search(re.escape(name), text, re.I):
                return name
        for line in text.splitlines()[:5]:
            l = line.strip()
            if l and not re.match(r'^\d', l):
                return l
        return "Unknown"

    def fm(pat, grp=1):
        m = re.search(pat, text, re.I)
        return m.group(grp).strip() if m else None

    def get_balances():
        b = {"opening_balance": None, "total_credits": None,
             "total_debits": None,   "closing_balance": None}
        m = re.search(
            r'\$([\d,]+\.\d{2})\s+\+\s*\$([\d,]+\.\d{2})\s+-\s*\$([\d,]+\.\d{2})\s+\$([\d,]+\.\d{2})',
            text)
        if m:
            b.update({"opening_balance": f"${m.group(1)}", "total_credits": f"${m.group(2)}",
                      "total_debits": f"${m.group(3)}",   "closing_balance": f"${m.group(4)}"})
        return b

    def get_transactions():
        MONTHS  = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
        txns    = []
        SKIP    = {'Opening Balance', 'Closing Balance', 'Date', 'Description'}
        row_pat = re.compile(
            rf'^({MONTHS})\s+(\d{{1,2}})\s+(.+?)'
            r'(?:\s+([A-Z]{2,3}\d{6,}))?'
            r'(?:\s+\$([\d,]+\.\d{2}))?'
            r'(?:\s+\$([\d,]+\.\d{2}))?'
            r'\s+\$([\d,]+\.\d{2})\s*$', re.MULTILINE)
        credit_kw = re.compile(r'deposit|credit|salary|refund|interest|transfer\s+in', re.I)
        for m in row_pat.finditer(text):
            desc = m.group(3).strip()
            if desc in SKIP:
                continue
            ref  = m.group(4) or ""
            amt1, amt2 = m.group(5), m.group(6)
            if ref and credit_kw.search(desc):
                debit, credit = "", (f"${amt1}" if amt1 else "")
            else:
                debit, credit = (f"${amt1}" if amt1 else ""), (f"${amt2}" if amt2 else "")
            txns.append({
                "Date": f"{m.group(1)} {m.group(2).zfill(2)}",
                "Description": desc, "Reference": ref,
                "Debit": debit, "Credit": credit, "Balance": f"${m.group(7)}"
            })
        return txns

    holder_m = re.search(r'Account\s+Holder:\s*(.+?)\s+(?:Statement\s+Period|Statement\s+Date)', text, re.I)
    holder   = holder_m.group(1).strip() if holder_m else None
    if not holder:
        for line in text.splitlines():
            if re.match(r'^[A-Z]{2,}(?:\s+[A-Z]{2,})+$', line.strip()):
                holder = line.strip()
                break

    # ── Mailing address: try labelled fields first, then block after holder name
    mailing_address = {}
    labelled = re.search(r'Address[:\s]+(.+?)(?:\n|City|$)', text, re.I)
    if labelled:
        street = labelled.group(1).strip()
        city_m = re.search(r'City[:\s]+(.+?)\s+State[:\s]+([A-Z]{2})\s+ZIP[:\s]+(\d{5}(?:-\d{4})?)', text, re.I)
        if city_m:
            mailing_address = {
                "street":       street,
                "city":         city_m.group(1).strip(),
                "state":        city_m.group(2),
                "zip":          city_m.group(3),
                "full_address": f"{street}, {city_m.group(1).strip()} {city_m.group(2)} {city_m.group(3)}"
            }
    elif holder:
        all_lines = [l.strip() for l in text.splitlines() if l.strip()]
        for i, line in enumerate(all_lines):
            if line == holder:
                mailing_address = parse_address_block(all_lines, i)
                break

    return {
        "bank_name":        get_bank(),
        "account_holder":   holder,
        "mailing_address":  mailing_address,
        "account_number":   fm(r'Account\s+(?:Number|No\.?|#):\s*([*\dXx\-\s]{5,20}?)\s+(?:[A-Z]|\n)') or
                            fm(r'(?:\*{4}|X{4})[-\s]?(?:\*{4}|X{4})[-\s]?\d{4}'),
        "account_type":     fm(r'Account\s+Type:\s*(.+?)\s+(?:Branch|Currency|$)'),
        "routing_number":   fm(r'Routing\s+(?:Number|No\.?):\s*(\d{9})'),
        "statement_date":   fm(r'Statement\s+Date:\s*((?:January|February|March|April|May|June|'
                               r'July|August|September|October|November|December)\s+\d{1,2},\s*\d{4})'),
        "statement_period": fm(r'Statement\s+Period:\s*([\w]+ \d{1,2},?\s*\d{4}\s*[–\-]\s*[\w]+ \d{1,2},?\s*\d{4})'),
        **get_balances(),
        "transactions":     get_transactions(),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  MASTER EXTRACT
# ══════════════════════════════════════════════════════════════════════════════

def extract(pdf_path):
    pages, method = get_pages(pdf_path)
    if is_everbank(pages):
        details = parse_everbank(pages)
        fmt     = "EverBank"
    else:
        details = parse_generic(pages)
        fmt     = "Generic"
    return details, method, fmt, "\n\n--- PAGE BREAK ---\n\n".join(pages)


# ══════════════════════════════════════════════════════════════════════════════
#  UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def field(label, value):
    v = value or "—"
    st.markdown(f"""<div class="field-card">
        <div class="field-label">{label}</div>
        <div class="field-value">{v}</div></div>""", unsafe_allow_html=True)


def bal_card(label, value):
    v = value or "—"
    st.markdown(f"""<div class="balance-card">
        <div class="balance-label">{label}</div>
        <div class="balance-value">{v}</div></div>""", unsafe_allow_html=True)


def section(label):
    st.markdown(f'<div class="section-header">{label}</div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  STREAMLIT UI
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("# 🏦 Bank Statement Extractor")
st.markdown("Upload a PDF bank statement to instantly extract account info, balances, and transactions.")
st.divider()

uploaded = st.file_uploader("📂 Upload Bank Statement PDF", type=["pdf"],
                             help="Supports EverBank multi-page PDFs and generic bank statement formats")

if not uploaded:
    st.info("👆 Upload a PDF to get started. Your file is processed in memory and never stored.")
    st.stop()

with st.spinner("🔍 Extracting data from PDF..."):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded.read())
        tmp_path = tmp.name
    try:
        details, method, fmt, raw_text = extract(tmp_path)
    finally:
        os.unlink(tmp_path)

st.success(f"✅ Done — Format detected: **{fmt}** | Extraction: **{method}**")

# ── EverBank Layout ───────────────────────────────────────────────────────────
if fmt == "EverBank":
    col_l, col_r = st.columns(2, gap="large")

    with col_l:
        section("🏦 Bank & Account Info")
        field("Bank Name",           details.get("bank_name"))
        field("Statement Ref No",    details.get("statement_reference_no"))
        field("Account Holder",      details.get("account_holder"))

        section("📮 Mailing Address")
        addr = details.get("mailing_address", {})
        if addr.get("apt_suite"): field("Apt / Suite",      addr.get("apt_suite"))
        field("Street",              addr.get("street"))
        field("City",                addr.get("city"))
        c1, c2 = st.columns(2)
        with c1: field("State", addr.get("state"))
        with c2: field("ZIP",   addr.get("zip"))
        if addr.get("full_address"):
            field("Full Address",    addr.get("full_address"))

        field("Inquiry Phone",       details.get("inquiry_phone"))
        field("Bank Address",        details.get("bank_address"))

    with col_r:
        section("📅 Statement Info")
        field("Statement Date",      details.get("statement_date"))
        field("Period (days)",       details.get("stmt_period_days"))

        section("💰 Account Summary")
        accounts = details.get("accounts", [])
        if accounts:
            for acct in accounts:
                a1, a2 = st.columns(2)
                with a1:
                    field("Account Type",   acct["account_type"])
                    field("Account Number", acct["account_number"])
                with a2:
                    bal_card("Ending Balance", acct["ending_balance"])

        # Interest details
        idet = details.get("interest_details", {})
        if any(idet.values()):
            section("📈 Interest Details")
            i1, i2 = st.columns(2)
            with i1:
                field("Interest Paid YTD",    idet.get("interest_paid_ytd"))
                field("APY Earned",           idet.get("apy_earned"))
                field("Interest-Bearing Days",idet.get("interest_bearing_days"))
            with i2:
                field("Avg Balance for APY",  idet.get("average_balance_apy"))
                field("Interest Earned",      idet.get("interest_earned"))

    # Transactions
    st.divider()
    txns = details.get("transactions", [])
    section(f"📋 Transaction History ({len(txns)} transactions)")
    if txns:
        import pandas as pd
        df = pd.DataFrame(txns)
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={
                         "Date":         st.column_config.TextColumn("Date",          width="small"),
                         "Description":  st.column_config.TextColumn("Description",   width="large"),
                         "Additions":    st.column_config.TextColumn("Additions",     width="small"),
                         "Subtractions": st.column_config.TextColumn("Subtractions",  width="small"),
                         "Balance":      st.column_config.TextColumn("Balance",       width="small"),
                     })
    else:
        st.warning("No transactions found — the PDF may use a layout variation not yet handled.")

# ── Generic Layout ────────────────────────────────────────────────────────────
else:
    col_l, col_r = st.columns(2, gap="large")
    with col_l:
        section("🏦 Bank & Account Info")
        field("Bank Name",       details.get("bank_name"))
        field("Account Holder",  details.get("account_holder"))
        field("Account Number",  details.get("account_number"))
        field("Account Type",    details.get("account_type"))
        field("Routing Number",  details.get("routing_number"))

        section("📮 Mailing Address")
        addr = details.get("mailing_address", {})
        if addr:
            if addr.get("apt_suite"): field("Apt / Suite", addr.get("apt_suite"))
            field("Street",   addr.get("street"))
            field("City",     addr.get("city"))
            c1, c2 = st.columns(2)
            with c1: field("State", addr.get("state"))
            with c2: field("ZIP",   addr.get("zip"))
            if addr.get("full_address"):
                field("Full Address", addr.get("full_address"))
        else:
            st.markdown('<div class="warn-card">⚠️ No address found in this PDF.</div>',
                        unsafe_allow_html=True)
    with col_r:
        section("📅 Statement Info")
        field("Statement Date",   details.get("statement_date"))
        field("Statement Period", details.get("statement_period"))
        section("💰 Balance Summary")
        b1, b2 = st.columns(2)
        with b1:
            bal_card("Opening Balance", details.get("opening_balance"))
        with b2:
            bal_card("Closing Balance", details.get("closing_balance"))
        b3, b4 = st.columns(2)
        with b3:
            bal_card("Total Credits", details.get("total_credits"))
        with b4:
            bal_card("Total Debits",  details.get("total_debits"))

    st.divider()
    txns = details.get("transactions", [])
    section(f"📋 Transaction History ({len(txns)} transactions)")
    if txns:
        import pandas as pd
        st.dataframe(pd.DataFrame(txns), use_container_width=True, hide_index=True)
    else:
        st.warning("No transactions found.")

# ── Downloads ─────────────────────────────────────────────────────────────────
st.divider()
section("⬇️ Download Results")
d1, d2 = st.columns(2)
with d1:
    st.download_button("📥 Download JSON", json.dumps(details, indent=2),
                       file_name=uploaded.name.replace(".pdf", "_extracted.json"),
                       mime="application/json", use_container_width=True)
with d2:
    txns = details.get("transactions", [])
    if txns:
        import pandas as pd
        st.download_button("📥 Download Transactions CSV",
                           pd.DataFrame(txns).to_csv(index=False),
                           file_name=uploaded.name.replace(".pdf", "_transactions.csv"),
                           mime="text/csv", use_container_width=True)

with st.expander("🔍 View raw extracted text (debug)"):
    st.code(raw_text, language="text")
