# 📬 Financial OS — AI-Powered Gmail Finance Tracker

> A local-first, privacy-preserving financial intelligence system that automatically ingests your Gmail inbox, extracts structured transaction data using a local LLM, and lets you query your entire financial history in plain English.

---

## ✨ What It Does

- **Ingests your Gmail inbox** via the Gmail API and processes every email
- **Extracts financial transactions** (amount, merchant, type, date) using a locally-running **Llama 3** model
- **Stores structured data** in a SQLite database for precise math & totals
- **Stores semantic embeddings** in ChromaDB for unstructured, natural language queries
- **Lets you ask questions** like a personal finance assistant:
  - *"How much did I spend on Zomato last month?"*
  - *"List all my mutual fund investments"*
  - *"What was the biggest debit transaction in March?"*
  - *"Did I receive my salary?"*

---

## 🏗️ Architecture

```
Gmail API
    │
    ▼
read_mail.py          ← Fetches emails in batches with smart checkpointing
    │
    ▼
process_mail.py       ← Cosine similarity pre-filter → Llama 3 extraction
    │
    ├──► sql_store.py     → SQLite (structured: amount, merchant, type, date)
    └──► vector_store.py  → ChromaDB (semantic: full email text embeddings)
    
query.py              ← 3-way intent router (SQL / RAG / Hybrid) + chat memory
test.py               ← Entry point: runs sync then opens chat interface
```

### Two-Brain Design
| Brain | Engine | Best For |
|---|---|---|
| **SQL** | SQLite | Totals, counts, date ranges, comparisons |
| **Vector RAG** | ChromaDB + Llama 3 | Email content, summaries, general questions |
| **Hybrid** | Both | Insights (*"Why am I spending so much on X?"*) |

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

### 4. Run
```bash
python test.py
```

On the **first run**, it will:
1. Open a browser for Gmail OAuth authorization
2. Begin syncing your entire inbox (this takes time — Llama 3 runs locally)
3. Drop you into an interactive chat when sync is complete

On **subsequent runs**, it resumes exactly where it left off thanks to checkpointing.

---

## 💬 Example Queries

```
Ask about your finances: How much did I spend on food delivery this month?
🧮 Querying Finance DB...
🧠 ANSWER: You spent ₹3,241 on food delivery in April 2026 across 8 transactions
           (Zomato: ₹1,847 | Blinkit: ₹894 | Swiggy: ₹500).

Ask about your finances: What is my total investment in mutual funds?
🧠 ANSWER: Your total mutual fund investments amount to ₹42,499.88 across 6 SIPs
           including DSP, Quant, Axis, Franklin Templeton, and SBI funds.
```

---

## 📁 Project Structure

```
.
├── test.py                  # Main entry point
├── read_mail.py             # Gmail ingestion + checkpointing engine
├── process_mail.py          # AI extraction pipeline (filter → Llama 3)
├── query.py                 # Smart query router + chat interface
├── sql_store.py             # SQLite schema + transaction storage
├── vector_store.py          # ChromaDB setup + semantic search
├── utils.py                 # Checkpoint helpers
├── classes/
│   └── ollama_embedding.py  # Custom Ollama embedding wrapper for LangChain
├── requirements.txt
└── .gitignore
```

---

## ⚙️ How Checkpointing Works

The system uses a **two-layer checkpoint system** to ensure zero data loss:

1. **`processed_cache.txt`** — A flat cache of every Gmail message ID ever processed. On restart, emails in this list are instantly skipped (no LLM call needed).
2. **`resume_oldest.json`** — Tracks the oldest email timestamp reached during a historical backfill. On restart, this anchors the Gmail API query to `before:TIMESTAMP`, skipping all recently-processed pages entirely.
3. **`last_run.json`** — Written only after a **complete** sync finishes. Used as the `after:` filter on incremental daily runs.

This means you can safely `Ctrl+C` at any point and restart — no emails will be re-processed and no progress will be lost.

---

## 🔒 Privacy

**Everything runs 100% locally:**
- Llama 3 runs on your machine via Ollama — no data leaves your device
- ChromaDB is a local folder (`chroma_db/`)
- SQLite is a local file (`finance_os.db`)
- Only Gmail API calls go to Google's servers (same as any Gmail client)

---

## 🛠️ Configuration

Key thresholds in `process_mail.py`:

| Parameter | Default | Description |
|---|---|---|
| `SIMILARITY_THRESHOLD` | `0.45` | Cosine similarity cutoff for pre-filtering non-financial emails |
| `max_workers` | `5` | Parallel threads for LLM processing |
| `maxResults` | `50` | Emails fetched per Gmail API page |

---

## 📦 Dependencies

- `langchain`, `langchain-ollama` — LLM orchestration
- `chromadb` — Local vector database
- `google-api-python-client` — Gmail API client
- `sqlite3` (stdlib) — Structured transaction storage
- `sentence-transformers` — Cosine similarity pre-filter

---

## 📄 License

MIT License — free to use and modify for personal use.
