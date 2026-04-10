"""
Categorizes transactions using Llama 3.
Stores results in a separate `transaction_categories` table — the main
`transactions` schema is never touched.
"""

import json
import re
import sqlite3

from langchain_ollama import ChatOllama

DB_FILE = "finance_os.db"

CATEGORIES = [
    "Food & Dining",
    "Transport",
    "Investments",
    "Shopping",
    "Utilities & Bills",
    "Entertainment",
    "Health & Medical",
    "Travel",
    "Education",
    "Transfers",
    "Income",
    "Other",
]

_llm = ChatOllama(model="llama3", num_ctx=4096, temperature=0.0)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _init_categories_table():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transaction_categories (
            transaction_id INTEGER PRIMARY KEY,
            category       TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _save_mapping(mapping: dict):
    """mapping: {transaction_id (int): category (str)}"""
    if not mapping:
        return
    conn = sqlite3.connect(DB_FILE)
    conn.executemany(
        "INSERT OR REPLACE INTO transaction_categories (transaction_id, category) VALUES (?, ?)",
        list(mapping.items()),
    )
    conn.commit()
    conn.close()


def _uncategorized_ids() -> list[int]:
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("""
        SELECT t.id FROM transactions t
        LEFT JOIN transaction_categories c ON t.id = c.transaction_id
        WHERE c.transaction_id IS NULL
    """).fetchall()
    conn.close()
    return [r[0] for r in rows]


# ── Llama categorization ──────────────────────────────────────────────────────

def _build_prompt(batch: list[dict]) -> str:
    categories_str = ", ".join(CATEGORIES)
    lines = "\n".join(
        f"{t['id']}|{t['merchant']}|{t['notes']}|{t['type']}|{t['amount']}"
        for t in batch
    )
    return f"""You are a personal finance categorizer for Indian transactions.
Assign each transaction exactly one category from this list:
{categories_str}

Category guide:
- Food & Dining     : Zomato, Swiggy, restaurants, groceries, cafes
- Transport         : Uber, Ola, auto, metro, fuel, parking, bus, train tickets
- Investments       : mutual funds, SIP, Zerodha, Groww, stocks, gold bonds, ETF
- Shopping          : Amazon, Flipkart, Myntra, clothing, electronics, retail stores
- Utilities & Bills : electricity, water, gas, mobile recharge, broadband, DTH
- Entertainment     : Netflix, Spotify, Prime, movies, games, OTT subscriptions
- Health & Medical  : pharmacy, hospital, doctor, medical, health insurance
- Travel            : flights, hotels, MakeMyTrip, Airbnb, holiday bookings
- Education         : courses, books, Udemy, college/school fees, coaching
- Transfers         : peer-to-peer UPI, bank transfer to a person (not a business)
- Income            : salary, freelance payment, cashback, refund, interest received
- Other             : anything that does not clearly fit the above

Input format per line: id|merchant|notes|type|amount
Output: a single JSON object mapping each id (as a string key) to its category.
No explanation, no markdown — raw JSON only.

Transactions:
{lines}

JSON:"""


def _parse_response(resp: str, batch: list[dict]) -> dict:
    """Extract {{id: category}} from Llama response, validate categories."""
    match = re.search(r'\{.*\}', resp, re.DOTALL)
    if not match:
        return {}
    try:
        raw = re.sub(r'//.*', '', match.group(0))
        mapping = json.loads(raw)
        valid = {}
        for k, v in mapping.items():
            try:
                tid = int(k)
                cat = v.strip() if isinstance(v, str) else ""
                # Accept exact match or case-insensitive prefix match
                matched = next((c for c in CATEGORIES if c.lower() == cat.lower()), None)
                if not matched:
                    matched = next((c for c in CATEGORIES if c.lower().startswith(cat.lower()[:6])), "Other")
                valid[tid] = matched
            except (ValueError, TypeError):
                pass
        return valid
    except Exception:
        return {}


def categorize_single(merchant: str, notes: str, trans_type: str, amount: float) -> str:
    """Categorize one transaction. Used during live email ingestion."""
    categories_str = ", ".join(CATEGORIES)
    prompt = f"""Categorize this Indian financial transaction into exactly one of:
{categories_str}

Merchant : {merchant}
Notes    : {notes}
Type     : {trans_type}  (debit=money spent, credit=money received)
Amount   : ₹{amount}

Reply with ONLY the category name — nothing else."""
    try:
        resp = _llm.invoke(prompt).content.strip()
        matched = next((c for c in CATEGORIES if c.lower() == resp.lower()), None)
        if not matched:
            matched = next((c for c in CATEGORIES if c.lower() in resp.lower()), "Other")
        return matched
    except Exception:
        return "Other"


# ── Backfill ──────────────────────────────────────────────────────────────────

def run_backfill(batch_size: int = 20, log_fn=print) -> int:
    """
    Categorize all transactions not yet in transaction_categories.
    Returns the number of newly categorized rows.
    """
    _init_categories_table()

    ids = _uncategorized_ids()
    if not ids:
        log_fn("All transactions already categorized.")
        return 0

    # Fetch merchant/notes/type/amount for uncategorized rows
    conn = sqlite3.connect(DB_FILE)
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, merchant, notes, type, amount FROM transactions WHERE id IN ({placeholders})",
        ids,
    ).fetchall()
    conn.close()

    transactions = [
        {"id": r[0], "merchant": r[1] or "", "notes": r[2] or "", "type": r[3] or "", "amount": r[4] or 0}
        for r in rows
    ]

    log_fn(f"Categorizing {len(transactions)} transactions in batches of {batch_size}...")
    total_done = 0

    for i in range(0, len(transactions), batch_size):
        batch = transactions[i: i + batch_size]
        batch_num = i // batch_size + 1
        try:
            prompt = _build_prompt(batch)
            resp = _llm.invoke(prompt).content.strip()
            mapping = _parse_response(resp, batch)

            # Fall back to "Other" for any IDs Llama missed
            for t in batch:
                if t["id"] not in mapping:
                    mapping[t["id"]] = "Other"

            _save_mapping(mapping)
            total_done += len(mapping)
            log_fn(f"  Batch {batch_num}: {len(mapping)}/{len(batch)} categorized")
        except Exception as e:
            log_fn(f"  Batch {batch_num}: error — {e}")

    log_fn(f"Backfill complete. {total_done} transactions categorized.")
    return total_done
