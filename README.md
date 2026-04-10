# 📬 Financial OS — AI-Powered Gmail Finance Tracker

> A local-first, privacy-preserving financial intelligence system that automatically ingests your Gmail inbox, extracts structured transaction data using a local LLM, and lets you query your entire financial history in plain English.

---

## ✨ What It Does

- **Continuously monitors your Gmail inbox** — polls every 15 minutes for new emails
- **Extracts financial transactions** (amount, merchant, debit/credit, date) using a locally-running **Llama 3** model
- **Stores structured data** in a SQLite database for precise totals, date filtering, and comparisons
- **Visual dashboard** — interactive charts and transaction browser at `localhost:8000`
- **Natural language queries** powered by SQL generation:
  - *"How much did I spend on Zomato this month?"*
  - *"List all my mutual fund SIPs"*
  - *"What was my biggest expense in January?"*
  - *"Did I receive my salary this month?"*

---

## 🏗️ Architecture

```
Gmail API
    │
    ▼
read_mail.py          ← Fetches emails in pages, checkpoints after every page
    │
    ▼
process_mail.py       ← Two-tier classification → Llama 3 extraction
    │   ├─ Tier 1: ₹ symbol fast-path (instant)
    │   ├─ Tier 2: Llama 3 classifier (YES/NO, num_ctx=1024)
    │   └─ Tier 3: Llama 3 full extractor (num_ctx=16384)
    │
    ▼
sql_store.py          → SQLite (amount, merchant, type, date, sender, notes)

query.py              ← Natural language → SQL → summarised answer
api.py                ← FastAPI backend — REST API + embedded sync daemon + serves static/
static/index.html     ← Frontend dashboard (charts + transaction browser)
```

### Single-Model, Three-Role Design

| Role | Model | Context | Purpose |
|---|---|---|---|
| **Fast Classifier** | `llama3` | 1,024 tokens | YES/NO — is this a real transaction? |
| **Full Extractor** | `llama3` | 16,384 tokens | Extract amount, merchant, type, notes |
| **Query Engine** | `llama3` | 8,192 tokens | Natural language → SQL → answer |

> `nomic-embed-text` and ChromaDB have been fully removed. The system is SQL-only for speed and simplicity.

---

## 🚀 Setup

### Prerequisites
- Python 3.11+
- [Ollama](https://ollama.ai) installed and running locally
- A Google Cloud project with the Gmail API enabled

### 1. Clone & Install
```bash
git clone https://github.com/Rishabh10001/Mail-Read.git
cd Mail-Read
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Pull the Local LLM
```bash
ollama pull llama3
```

### 3. Configure Gmail API
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project → Enable the **Gmail API**
3. Create OAuth 2.0 credentials (Desktop app)
4. Download as `credentials.json` and place it in the project root
5. On first run, a browser window will open to authorize access

### 4. Set the Backfill Start Date
By default the system ingests from **Jan 1, 2026**. To change this:
```bash
# Jan 1 2026 = Unix timestamp 1767225600
echo '{"last_run": 1767225600}' > last_run.json
```

### 5. Start the API server
```bash
uvicorn api:app --reload
```

Then open **http://localhost:8000**

The server starts a background sync daemon automatically on startup. It ingests all emails from your configured start date on the first run (may take 30–90 min depending on inbox size and Llama speed), then polls every 2 minutes for new emails.

---

## 💬 Querying Your Finances

```bash
python query.py
```

### Example Queries

```
Ask: How much did I spend on food delivery this month?
→ You spent ₹3,241 on food delivery in April 2026 across 8 transactions
  (Zomato: ₹1,847 | Blinkit: ₹894 | Swiggy: ₹500)

Ask: What is my total mutual fund investment?
→ ₹42,499.88 across 6 SIPs — DSP, Axis, SBI, Quant, Franklin Templeton

Ask: Show my top 5 debits this year
→ [table of top 5 transactions with merchant and amount]
```

---

## 📊 Dashboard

The web dashboard (`uvicorn api:app --reload` → `localhost:8000`) shows:

| Section | Details |
|---|---|
| **Summary Cards** | Total Spent, Total Received, Net Balance, Transaction Count |
| **Monthly Cash Flow** | Bar chart — red (spent) vs green (received) per month |
| **Distribution** | Credit vs Debit donut chart |
| **Top Merchants** | Horizontal bar — top 10 by spend or receipt |
| **Transaction Table** | Search, filter by type, paginated (50 per page) |

---

## 📁 Project Structure

```
.
├── api.py               # FastAPI backend — REST API + sync daemon + static file serving
├── read_mail.py         # Gmail ingestion + per-page checkpointing
├── process_mail.py      # Two-tier AI classification + extraction
├── query.py             # Natural language → SQL query engine
├── sql_store.py         # SQLite schema + transaction storage
├── categorize.py        # AI-powered transaction categorisation
├── utils.py             # Checkpoint read/write helpers
├── static/
│   └── index.html       # Dashboard frontend (Chart.js + vanilla CSS)
├── credentials.json     # Gmail OAuth credentials (not committed)
├── last_run.json        # Checkpoint: timestamp of most recent processed email
├── processed_cache.txt  # Flat cache of all processed Gmail message IDs
├── finance_os.db        # SQLite transaction database
├── rejected_emails.txt  # Log of emails rejected by the classifier
└── .gitignore
```

---

## ⚙️ How Checkpointing Works

The system uses a **two-layer deduplication** system:

1. **`last_run.json`** — Saved after **every Gmail page** (not just at the end of a run). Stores the most recent email timestamp seen. On restart, the query becomes `after:{timestamp}` — fetching only genuinely new emails. Even if interrupted mid-run, the next cycle is fast.

2. **`processed_cache.txt`** — Flat list of every Gmail message ID ever seen. Used as a second guard: even if an email somehow appears in a query, it is skipped instantly if its ID is in this file.

> **Safe to interrupt:** `Ctrl+C` at any time. The next run picks up from the most recently saved checkpoint — no emails are re-processed, no data is lost.

---

## 🔍 Classification Pipeline

Every email goes through a three-stage gate before touching Llama's full context:

```
Email received
    │
    ├─ Has ₹ symbol? ──YES──► Full extraction (Llama 16k)
    │
    └─ NO
         │
         ├─ Llama classifier (1k ctx, YES/NO)
         │       │
         │       ├─ NO  → logged to rejected_emails.txt (cheap rejection)
         │       └─ YES → Full extraction (Llama 16k) → SQL insert
```

**Why no keyword list?** Keywords like "account", "amount", "total" flood through from SaaS and marketing emails (e.g., HubSpot). The Llama classifier understands context and correctly rejects pricing emails, portfolio summaries, and order confirmations.

---

## 🔒 Privacy

**Everything runs 100% locally:**
- Llama 3 runs on your machine via Ollama — no email content leaves your device
- SQLite is a local file (`finance_os.db`)
- Only Gmail API calls go to Google's servers (same as any Gmail client)

---

## 🛠️ Configuration

| Parameter | Location | Default | Description |
|---|---|---|---|
| `POLL_INTERVAL_MINUTES` | `api.py` | `2` | How often the sync daemon checks for new emails |
| `max_workers` | `process_mail.py` | `5` | Parallel threads for LLM processing |
| `maxResults` | `read_mail.py` | `50` | Emails fetched per Gmail API page |
| `num_ctx` (classifier) | `process_mail.py` | `1024` | Context window for YES/NO classifier |
| `num_ctx` (extractor) | `process_mail.py` | `16384` | Context window for full extraction |

---

## 📦 Dependencies

- `langchain-ollama` — Local Llama 3 inference
- `google-api-python-client`, `google-auth-oauthlib` — Gmail API
- `fastapi`, `uvicorn` — API server and dashboard backend
- `pydantic` — Request/response validation
- `sqlite3` (stdlib) — Structured transaction storage

---

## 📄 License

MIT License — free to use and modify for personal use.
