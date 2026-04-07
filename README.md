# 🏦 Bank Statement Extractor — Web App

A Streamlit web app that extracts structured data from any bank statement PDF.
Upload a PDF → instantly see account info, balances, and full transaction history.

---

## 📁 Project Structure

```
bank_statement_app/
├── app.py               ← Main Streamlit app
├── requirements.txt     ← Python dependencies
├── packages.txt         ← System dependencies (for Streamlit Cloud)
└── README.md
```

---

## 🚀 Option 1 — Run Locally (fastest)

```bash
# 1. Install system dependencies (Linux/Mac)
sudo apt install tesseract-ocr poppler-utils    # Linux
brew install tesseract poppler                  # Mac

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Run the app
streamlit run app.py
```

App opens automatically at: **http://localhost:8501**

Share on your local network: **http://<your-ip>:8501**

---

## ☁️ Option 2 — Deploy to Streamlit Cloud (free, public URL)

1. Push this folder to a **GitHub repo**
2. Go to **https://share.streamlit.io**
3. Click **"New app"** → select your repo → set `app.py` as the main file
4. Click **Deploy**

Streamlit Cloud automatically reads `requirements.txt` and `packages.txt`.
You get a public URL like: `https://yourname-bank-extractor.streamlit.app`

**No server setup. No Docker. Free.**

---

## 🐳 Option 3 — Docker (for internal servers / behind firewall)

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py .
EXPOSE 8501

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true"]
```

```bash
# Build and run
docker build -t bank-extractor .
docker run -p 8501:8501 bank-extractor
```

Access at: **http://<server-ip>:8501**

---

## 🔒 Security Notes

- PDFs are processed in-memory and never saved to disk
- No database — nothing is stored between sessions
- For internal use, run behind a VPN or add Streamlit's built-in authentication

---

## 📦 Supported PDF Types

| PDF Type          | Method Used        | Notes                        |
|-------------------|--------------------|------------------------------|
| Digital/text PDF  | pdfplumber (fast)  | Most bank statements         |
| Scanned/image PDF | OCR (pytesseract)  | Slower, requires tesseract   |

---

## 🏦 Supported Banks (regex-matched)

EverBank, Chase, Wells Fargo, Bank of America, Citibank, First National Bank,
TD Bank, US Bank, Capital One, PNC Bank, Regions Bank, SunTrust

_Add more by editing the `get_bank_name()` function in `app.py`._
