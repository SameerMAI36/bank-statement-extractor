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
#  DCU (DIGITAL FEDERAL CREDIT UNION) VISA CREDIT CARD EXTRACTOR
# ══════════════════════════════════════════════════════════════════════════════

def is_dcu(pages):
    return any(re.search(r'digital\s+federal\s+credit\s+union|DCU\s+VISA', p, re.I) for p in pages)


def parse_dcu(pages):
    """
    DCU Visa Credit Card statement layout:
      Page 1: Holder name + address (top), card number, account summary,
              transactions (purchases in left col, credits in right col)
      Page 2: Legal / terms (skip)
      Page 3: Transactions continued, fees, interest, APR table
    """
    p1        = pages[0] if pages else ""
    all_text  = "\n".join(pages)
    lines     = [l.strip() for l in p1.splitlines() if l.strip()]

    result = {"bank_name": "Digital Federal Credit Union (DCU)"}

    # ── Statement type
    result["statement_type"] = "Visa Credit Card"

    # ── Account holder — line index 1 (after "Visa Credit Card Bill")
    result["account_holder"] = lines[1] if len(lines) > 1 else None

    # ── Mailing address — lines 2, 3 (apt), 4 (city state zip)
    raw_addr = []
    for line in lines[2:6]:
        if re.match(r'^[A-Z0-9]', line) and not re.search(r'CARD\s+NUMBER|Summary|XXXX', line):
            raw_addr.append(line)
        else:
            break

    street, apt_suite, city, state, zipcode = None, None, None, None, None
    if raw_addr:
        # Check if last addr line is City ST ZIP
        csz = re.match(r'^([A-Za-z\s]+)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)', raw_addr[-1])
        if csz:
            city, state, zipcode = csz.group(1).strip().title(), csz.group(2), csz.group(3)
            street_lines = raw_addr[:-1]
        else:
            street_lines = raw_addr

        if len(street_lines) >= 2:
            street    = street_lines[0].title()
            apt_suite = street_lines[1].title()
        elif len(street_lines) == 1:
            street = street_lines[0].title()

    result["mailing_address"] = {
        "street":       street,
        "apt_suite":    apt_suite,
        "city":         city,
        "state":        state,
        "zip":          zipcode,
        "full_address": ", ".join(filter(None, [
            apt_suite, street,
            f"{city} {state} {zipcode}" if city else None
        ]))
    }

    # ── Card / account identifiers
    m = re.search(r'(XXXX-XXXX-XXXX-\d{4})', all_text)
    result["card_number"]  = m.group(1) if m else None

    m = re.search(r'DCU\s+VISA\s+LN#\s*(\d+)', all_text, re.I)
    result["loan_number"]  = m.group(1) if m else None

    m = re.search(r'Member\s+Number\s+(\d+)', all_text, re.I)
    result["member_number"] = m.group(1) if m else None

    # ── Summary fields
    def fm(pat, text=p1):
        hit = re.search(pat, text, re.I)
        return hit.group(1).strip() if hit else None

    result["starting_balance"]    = fm(r'Starting Balance\s+([\d,]+\.\d{2})')
    result["new_balance"]         = fm(r'New Balance\s+([\d,]+\.\d{2})')
    result["payments"]            = fm(r'Payments\s+(-[\d,]+\.\d{2})')
    result["other_credits"]       = fm(r'Other Credits\s+(-[\d,]+\.\d{2})')
    result["purchases"]           = fm(r'Purchases\s+([\d,]+\.\d{2})')
    result["cash_advances"]       = fm(r'Cash Advances\s+([\d,]+\.\d{2})')
    result["other_debits"]        = fm(r'Other Debits\s+([\d,]+\.\d{2})')
    result["fees_charged"]        = fm(r'Fees Charged\s+([\d,]+\.\d{2})')
    result["interest_charged"]    = fm(r'Interest Charged\s+([\d,]+\.\d{2})')
    result["credit_limit"]        = fm(r'Credit Limit\s+([\d,]+\.\d{2})')
    result["available_credit"]    = fm(r'Available Credit\s+([\d,]+\.\d{2})')
    result["minimum_payment_due"] = fm(r'Minimum Payment Due\s+([\d,]+\.\d{2})')
    result["past_due_amount"]     = fm(r'Past Due Amount\s+([\d,]+\.\d{2})')
    result["due_date"]            = fm(r'Due Date\s+(\d{2}/\d{2}/\d{2})')
    result["statement_date"]      = fm(r'Statement Closing Date\s+(\d{2}/\d{2}/\d{2})')
    result["days_in_period"]      = fm(r'Days in Period\s+(\d+)')

    # ── APR from interest calculation table (page 3)
    m = re.search(r'STD PURCHASE\s+[\d,\.]+\s+[\d\.]+%\s+([\d\.]+)\(', all_text, re.I)
    result["apr"] = f"{m.group(1)}%" if m else None

    # ── Total fees/interest YTD
    result["total_fees_ytd"]     = fm(r'TOTAL FEES CHARGED IN \d{4}\s+([\d,]+\.\d{2})', all_text)
    result["total_interest_ytd"] = fm(r'TOTAL INTEREST CHARGED IN \d{4}\s+([\d,]+\.\d{2})', all_text)

    # ── Transactions
    result["transactions"] = parse_dcu_transactions(pages)

    return result


def parse_dcu_transactions(pages):
    """
    DCU transaction rows:  MM/DD  MM/DD  DESCRIPTION  AMOUNT
    A 'CREDIT CARD CREDIT' label on the line before marks the next row as a credit.
    Purchases go in the left (Purchases & Advances) column.
    Credits go in the right (Payments & Credits) column.
    """
    txns        = []
    credit_flag = False
    skip        = re.compile(
        r'^(Transaction|Posting|Date|Purchases|Payments|Fees|Interest|'
        r'TOTAL|Description|Balance\s+Type|STD\s+|THE\s+BALANCE|ADDING|'
        r'OF\s+DAYS|CREDITS\.|PURCHASE|Page|Transactions|Totals|'
        r'\(V\)\s+INDICATES)', re.I)

    txn_pat = re.compile(
        r'^(\d{2}/\d{2})\s+(\d{2}/\d{2})\s+(.+?)\s+([\d,]+\.\d{2})\s*$'
    )

    for page_text in pages:
        for line in page_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if 'CREDIT CARD CREDIT' in line:
                credit_flag = True
                continue
            if skip.match(line):
                continue
            m = txn_pat.match(line)
            if m:
                amount = float(m.group(4).replace(',', ''))
                txn_type = "Credit" if credit_flag else "Purchase"
                txns.append({
                    "Transaction Date": m.group(1),
                    "Posting Date":     m.group(2),
                    "Description":      m.group(3).strip(),
                    "Amount":           m.group(4),
                    "Type":             txn_type,
                })
                credit_flag = False

    return txns


# ══════════════════════════════════════════════════════════════════════════════
#  BANK OF AMERICA EXTRACTOR
# ══════════════════════════════════════════════════════════════════════════════

def is_bofa(pages):
    return any(re.search(r'Bank\s+of\s+America', p, re.I) for p in pages)


def parse_bofa(pages):
    """
    Bank of America layout (multi-statement joint PDF):
      - Two-column header: customer address on left, bank info on right
      - Joint account: two ALL-CAPS holder names
      - Address lines mixed with bank info on same pdfplumber line
      - Transactions: MM/DD/YY  Description  Amount  (no running balance column)
      - Multiple statement periods may be in one PDF
    """
    p1    = pages[0] if pages else ""
    lines = [l.strip() for l in p1.splitlines() if l.strip()]
    full_text = "\n".join(pages)

    result = {"bank_name": "Bank of America"}

    # ── Account holders
    # Line like: "SAMEER RAJASIMHASANA MATAM bankofamerica.com"
    # Second:    "SHRUTHI SHARMA NAGASAMUDRA MATADA"  (clean)
    holders = []
    for line in lines:
        # Strip trailing website / bank noise from end of line
        clean = re.sub(r'\s+(?:bankofamerica\.com|Bank\s+of\s+America.*|P\.O\..*|Tampa.*)$', '', line).strip()
        if re.match(r'^[A-Z]{2,}(?:\s+[A-Z]{2,})+$', clean):
            holders.append(clean)
        if len(holders) == 2:
            break
    result["account_holder"]        = holders[0] if holders else None
    result["co_account_holder"]     = holders[1] if len(holders) > 1 else None

    # ── Mailing address
    # Street line: "1052 ALAMEDA CIR Bank of America, N.A." → extract before bank name
    # City line:   "CORINTH, TX 76210-1743"  (clean, comes 2 lines after street)
    street, city, state, zip_code = None, None, None, None
    for i, line in enumerate(lines):
        # Detect street: starts with number, has ALL-CAPS words, followed by bank noise
        m = re.match(r'^(\d+\s+[A-Z0-9\s]+?)\s+(?:Bank\s+of\s+America|P\.O\.|Tampa)', line)
        if m:
            street = m.group(1).strip().title()
            # City/State/ZIP is typically 2 lines later
            for j in range(i + 1, min(i + 4, len(lines))):
                csz = re.match(r'^([A-Za-z\s]+),\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)', lines[j])
                if csz:
                    city      = csz.group(1).strip().title()
                    state     = csz.group(2)
                    zip_code  = csz.group(3)
                    break
            break

    result["mailing_address"] = {
        "street":       street,
        "city":         city,
        "state":        state,
        "zip":          zip_code,
        "full_address": ", ".join(filter(None, [
            street,
            f"{city} {state} {zip_code}" if city else None
        ]))
    }

    # ── Account number
    m = re.search(r'Account\s+number:\s*([\d\s]+)', p1, re.I)
    result["account_number"] = m.group(1).strip() if m else None

    # ── Account type (from "Your Bank of America Advantage Savings")
    m = re.search(r'Your\s+(Bank\s+of\s+America\s+[\w\s]+?)(?:\n|for\s)', p1, re.I)
    result["account_type"] = m.group(1).strip() if m else "Bank of America Savings"

    # ── Statement period (first period — PDF may contain multiple)
    m = re.search(r'for\s+([\w]+ \d+,\s*\d{4})\s+to\s+([\w]+ \d+,\s*\d{4})', p1, re.I)
    result["statement_period"] = f"{m.group(1)} to {m.group(2)}" if m else None
    result["statement_date"]   = m.group(2) if m else None

    # ── Customer service phone
    m = re.search(r'Customer\s+service:\s*([\d\.]+)', p1, re.I)
    result["inquiry_phone"] = m.group(1) if m else None

    # ── Balances (from account summary block on page 1)
    def bal(pattern):
        hit = re.search(pattern, p1, re.I)
        return f"${hit.group(1)}" if hit else None

    result["opening_balance"] = bal(r'Beginning balance on .+?\$(\d[\d,]+\.\d{2})')
    result["ending_balance"]  = bal(r'Ending balance on .+?\$(\d[\d,]+\.\d{2})')
    result["total_deposits"]  = bal(r'Deposits and other additions\s+([\d,]+\.\d{2})')
    result["service_fees"]    = bal(r'Service fees\s+(-[\d,]+\.\d{2})')
    result["other_subs"]      = bal(r'Other subtractions\s+(-[\d,]+\.\d{2})')

    # ── Interest / APY
    m = re.search(r'Annual Percentage Yield Earned.*?:\s*([\d\.]+%)', p1, re.I)
    result["apy_earned"] = m.group(1) if m else None
    m = re.search(r'Interest Paid Year To Date:\s*\$([\d\.]+)', p1, re.I)
    result["interest_ytd"] = f"${m.group(1)}" if m else None

    # ── All transactions across every page (handles multi-period PDF)
    result["transactions"] = parse_bofa_transactions(pages)

    return result


def parse_bofa_transactions(pages):
    """
    BoA transaction rows: MM/DD/YY  Description  Amount
    Amount is positive for credits, negative for debits.
    Multi-line descriptions (continuation lines like 'CO ID:...') are merged.
    """
    txns = []
    skip = re.compile(
        r'^(Total|Note|Date|Braille|PULL|Page|IMPORTANT|BANK\s+DEPOSIT|'
        r'How\s+to|Deposit\s+agreement|Electronic|Reporting|Direct\s+dep|'
        r'For\s+consumer|For\s+other|©|Bank\s+of\s+America,\s+N\.A\.|'
        r'Deposits\s+and|Withdrawals\s+and|Other\s+subtractions|Service\s+fees|'
        r'ATM\s+and|Beginning|Ending|Annual|Interest\s+Paid)',
        re.I
    )
    txn_pat = re.compile(r'^(\d{2}/\d{2}/\d{2})\s+(.+?)\s+(-?[\d,]+\.\d{2})\s*$')

    for page_text in pages:
        prev_txn = None
        for line in page_text.splitlines():
            line = line.strip()
            if not line or skip.match(line):
                continue
            m = txn_pat.match(line)
            if m:
                if prev_txn:
                    txns.append(prev_txn)
                amount = m.group(3).replace(',', '')
                prev_txn = {
                    "Date":        m.group(1),
                    "Description": m.group(2).strip(),
                    "Amount":      m.group(3),
                    "Type":        "Credit" if float(amount) > 0 else "Debit",
                }
            elif prev_txn and not re.match(r'^\d{2}/\d{2}/\d{2}', line):
                # Continuation line (e.g. "CO ID:9111111103 PPD") — append to description
                # Strip BoA footer noise that bleeds onto continuation lines
                clean_line = re.sub(
                    r'\s*bankofamerica\.com.*$|'
                    r'\s*Braille and Large Print.*$|'
                    r'\s*You can request a copy.*$',
                    '', line, flags=re.I
                ).strip()
                if clean_line and not skip.match(clean_line):
                    prev_txn["Description"] += " " + clean_line
        if prev_txn:
            txns.append(prev_txn)
            prev_txn = None

    # Deduplicate (same date+description+amount may appear from overlapping pages)
    seen = set()
    unique = []
    for t in txns:
        key = (t["Date"], t["Amount"])
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


# ══════════════════════════════════════════════════════════════════════════════
#  EVERBANK-SPECIFIC EXTRACTORS  (multi-page format)
# ══════════════════════════════════════════════════════════════════════════════

def is_everbank(pages):
    return any(re.search(r'EverBank', p, re.I) for p in pages)


def fix_everbank_doubled_text(text):
    """
    EverBank PDFs often have a broken font encoding where characters
    are rendered 2-4x in a row due to overlapping glyphs.
    This function detects and collapses doubled lines.
    Only applied per-line — lines with normal text are left untouched.
    """
    fixed = []
    for line in text.splitlines():
        fixed.append(_fix_doubled_line(line))
    return "\n".join(fixed)


def _fix_doubled_line(line):
    if len(line) < 6:
        return line
    alpha = [c for c in line if c.isalpha()]
    if len(alpha) < 6:
        return line

    # Try repeat factors 2, 3, 4 — pick whichever has highest ratio
    best_factor, best_ratio = 1, 0.0
    for factor in [2, 3, 4]:
        chunks = [alpha[i:i+factor] for i in range(0, len(alpha) - factor + 1, factor)]
        if not chunks:
            continue
        repeated = sum(1 for chunk in chunks
                       if len(set(c.lower() for c in chunk)) == 1)
        ratio = repeated / len(chunks)
        if ratio > best_ratio:
            best_ratio, best_factor = ratio, factor

    if best_ratio < 0.65 or best_factor == 1:
        return line  # Not a doubled line

    # Collapse runs of identical letters by the repeat factor
    result = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch.isalpha():
            j = i
            while j < len(line) and line[j].lower() == ch.lower():
                j += 1
            run = line[i:j]
            keep = max(1, round(len(run) / best_factor))
            upper_chars = [c for c in run if c.isupper()]
            chosen = upper_chars[0] if upper_chars else ch
            result.append(chosen * keep)
            i = j
        else:
            result.append(ch)
            i += 1
    return "".join(result)


def _extract_everbank_address(p1_raw, holder_name):
    """
    EverBank page 1 is a two-column layout that pdfplumber merges into one line:
      '748 BAROSSA VALLEY DR NW  Days in stmt period: 90'
      'CONCORD NC 28027-8019  (0 )'
    Strip the right-column content and extract clean address components.
    """
    RIGHT_COL = re.compile(
        r'\s+(?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December|Days\s+in|Page\s+\d|\(\d\s*\))',
        re.I
    )
    addr_lines = []
    found_holder = False

    for line in p1_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        clean = RIGHT_COL.split(line)[0].strip()
        if holder_name and holder_name.upper() in line.upper():
            found_holder = True
            continue
        if found_holder:
            if re.match(r'^(Page|Direct|EverBank|888|301|Jacksonville|Account|Summary)', clean, re.I):
                break
            if clean:
                addr_lines.append(clean)

    street, apt_suite, city, state, zipcode = None, None, None, None, None
    for line in addr_lines:
        csz = re.match(r'^([A-Za-z\s]+)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)', line)
        if csz:
            city, state, zipcode = csz.group(1).strip().title(), csz.group(2), csz.group(3)
        elif re.match(r'^\d+\s+[A-Z]', line):
            # Only use line if it's not a doubled/garbled line
            alpha = [c for c in line if c.isalpha()]
            if alpha:
                pairs = sum(1 for i in range(0, len(alpha)-1, 2)
                            if alpha[i].lower() == alpha[i+1].lower())
                if pairs / max(len(alpha)//2, 1) < 0.5:
                    street = line.title()

    return {
        "street":       street,
        "apt_suite":    apt_suite,
        "city":         city,
        "state":        state,
        "zip":          zipcode,
        "full_address": ", ".join(filter(None, [
            street, f"{city} {state} {zipcode}" if city else None
        ]))
    }


def parse_everbank(pages):
    """
    EverBank layout:
      Page 1 — header, address, account summary table
               NOTE: Page 1 often has broken font encoding (chars doubled/tripled)
               Use page 2 for clean account holder name and statement date.
      Page 2 — transaction detail per account section (CLEAN text)
      Page 3 — legal disclaimer (skip)
    """
    p1_raw = pages[0] if len(pages) > 0 else ""
    p2     = pages[1] if len(pages) > 1 else ""

    # Apply per-line dedup fix to page 1
    p1 = fix_everbank_doubled_text(p1_raw)

    result = {}
    result["bank_name"] = "EverBank"

    # ── Statement reference number
    # Try page 1 first, then page 2 (clean)
    m = re.search(r'Statement\s+of\s+Account\s*\n?\s*(\d{7,15})', p1, re.I)
    if not m:
        m = re.search(r'\b(\d{10})\b', p2)  # bare account number on page 2
    result["statement_reference_no"] = m.group(1) if m else None

    # ── Account holder
    # Page 2 has a CLEAN title-case name right after the account number line
    # e.g. "3403437034\nKrishnakumar Muthuselvan\nPage 2"
    holder = None
    p2_lines = [l.strip() for l in p2.splitlines() if l.strip()]
    for i, line in enumerate(p2_lines):
        if re.match(r'^\d{7,15}$', line) and i + 1 < len(p2_lines):
            candidate = p2_lines[i + 1]
            # Title-case name, not "Page N" or a date
            if re.match(r'^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+$', candidate):
                holder = candidate.upper()  # normalise to ALL-CAPS
                break

    # Fallback: page 1 fixed text
    if not holder:
        for line in p1.splitlines():
            line = line.strip()
            if re.match(r'^[A-Z]{2,}(?:\s+[A-Z]{2,})+$', line):
                holder = line
                break

    result["account_holder"] = holder

    # ── Mailing address — EverBank page 1 is two-column layout
    # pdfplumber merges both columns: "748 BAROSSA VALLEY DR NW  Days in stmt period: 90"
    # Use dedicated extractor that strips right-column noise
    result["mailing_address"] = _extract_everbank_address(p1_raw, holder or "")

    # ── Statement date — prefer page 2 (clean), fallback page 1
    m = re.search(
        r'((?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+\d{1,2},\s*\d{4})',
        p2 + "\n" + p1, re.I)
    result["statement_date"] = m.group(1) if m else None

    # ── Statement period days
    m = re.search(r'Days\s+in\s+stmt\s+period[:\s]*(\d+)', p1 + p2, re.I)
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
    if is_dcu(pages):
        details = parse_dcu(pages)
        fmt     = "DCU"
    elif is_bofa(pages):
        details = parse_bofa(pages)
        fmt     = "Bank of America"
    elif is_everbank(pages):
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

# ── DCU Credit Card Layout ────────────────────────────────────────────────────
if fmt == "DCU":
    col_l, col_r = st.columns(2, gap="large")

    with col_l:
        section("💳 Card & Account Info")
        field("Bank Name",          details.get("bank_name"))
        field("Statement Type",     details.get("statement_type"))
        field("Card Number",        details.get("card_number"))
        field("Loan Number",        details.get("loan_number"))
        field("Member Number",      details.get("member_number"))
        field("Account Holder",     details.get("account_holder"))

        section("📮 Mailing Address")
        addr = details.get("mailing_address", {})
        if addr.get("apt_suite"): field("Apt / Suite", addr.get("apt_suite"))
        field("Street",   addr.get("street"))
        field("City",     addr.get("city"))
        c1, c2 = st.columns(2)
        with c1: field("State", addr.get("state"))
        with c2: field("ZIP",   addr.get("zip"))
        if addr.get("full_address"):
            field("Full Address", addr.get("full_address"))

    with col_r:
        section("📅 Statement Info")
        field("Statement Closing Date", details.get("statement_date"))
        field("Due Date",               details.get("due_date"))
        field("Days in Period",         details.get("days_in_period"))
        field("APR",                    details.get("apr"))

        section("💰 Account Summary")
        b1, b2 = st.columns(2)
        with b1: bal_card("Starting Balance", details.get("starting_balance"))
        with b2: bal_card("New Balance",      details.get("new_balance"))

        b3, b4 = st.columns(2)
        with b3: bal_card("Credit Limit",     details.get("credit_limit"))
        with b4: bal_card("Available Credit", details.get("available_credit"))

        section("📊 Activity Summary")
        field("Purchases",           details.get("purchases"))
        field("Payments",            details.get("payments"))
        field("Other Credits",       details.get("other_credits"))
        field("Other Debits",        details.get("other_debits"))
        field("Fees Charged",        details.get("fees_charged"))
        field("Interest Charged",    details.get("interest_charged"))

        section("⚠️ Payment Info")
        field("Minimum Payment Due", details.get("minimum_payment_due"))
        field("Past Due Amount",     details.get("past_due_amount"))
        field("Total Fees YTD",      details.get("total_fees_ytd"))
        field("Total Interest YTD",  details.get("total_interest_ytd"))

    st.divider()
    txns = details.get("transactions", [])
    section(f"📋 Transactions ({len(txns)} total)")
    if txns:
        import pandas as pd
        df = pd.DataFrame(txns)
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={
                         "Transaction Date": st.column_config.TextColumn("Txn Date",    width="small"),
                         "Posting Date":     st.column_config.TextColumn("Posted",      width="small"),
                         "Description":      st.column_config.TextColumn("Description", width="large"),
                         "Amount":           st.column_config.TextColumn("Amount",      width="small"),
                         "Type":             st.column_config.TextColumn("Type",        width="small"),
                     })
    else:
        st.warning("No transactions found.")

# ── Bank of America Layout ────────────────────────────────────────────────────
elif fmt == "Bank of America":
    col_l, col_r = st.columns(2, gap="large")

    with col_l:
        section("🏦 Bank & Account Info")
        field("Bank Name",          details.get("bank_name"))
        field("Account Type",       details.get("account_type"))
        field("Account Number",     details.get("account_number"))
        field("Primary Holder",     details.get("account_holder"))
        field("Co-Account Holder",  details.get("co_account_holder"))
        field("Customer Service",   details.get("inquiry_phone"))

        section("📮 Mailing Address")
        addr = details.get("mailing_address", {})
        if addr:
            field("Street",   addr.get("street"))
            field("City",     addr.get("city"))
            c1, c2 = st.columns(2)
            with c1: field("State", addr.get("state"))
            with c2: field("ZIP",   addr.get("zip"))
            if addr.get("full_address"):
                field("Full Address", addr.get("full_address"))
        else:
            st.markdown('<div class="warn-card">⚠️ No address found.</div>', unsafe_allow_html=True)

    with col_r:
        section("📅 Statement Info")
        field("Statement Period",   details.get("statement_period"))
        field("Statement Date",     details.get("statement_date"))
        field("APY Earned",         details.get("apy_earned"))
        field("Interest YTD",       details.get("interest_ytd"))

        section("💰 Account Summary")
        b1, b2 = st.columns(2)
        with b1: bal_card("Opening Balance", details.get("opening_balance"))
        with b2: bal_card("Ending Balance",  details.get("ending_balance"))
        b3, b4 = st.columns(2)
        with b3: bal_card("Total Deposits",  details.get("total_deposits"))
        with b4: bal_card("Service Fees",    details.get("service_fees"))
        if details.get("other_subs"):
            field("Other Subtractions", details.get("other_subs"))

    st.divider()
    txns = details.get("transactions", [])
    section(f"📋 Transaction History ({len(txns)} transactions across all statement periods)")
    if txns:
        import pandas as pd
        df = pd.DataFrame(txns)
        st.dataframe(df, use_container_width=True, hide_index=True,
                     column_config={
                         "Date":        st.column_config.TextColumn("Date",         width="small"),
                         "Description": st.column_config.TextColumn("Description",  width="large"),
                         "Amount":      st.column_config.TextColumn("Amount",       width="small"),
                         "Type":        st.column_config.TextColumn("Type",         width="small"),
                     })
    else:
        st.warning("No transactions found.")

# ── EverBank Layout ───────────────────────────────────────────────────────────
elif fmt == "EverBank":
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
