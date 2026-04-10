import sqlite3
import os
from email.utils import parsedate_tz, mktime_tz

DB_FILE = "finance_os.db"

# Window within which the same merchant+amount is considered a duplicate (seconds)
DEDUP_WINDOW_SECS = 86400  # 24 hours


def _rfc_ts(date_str):
    """Parse RFC 2822 date string → Unix timestamp (float), or None on failure."""
    try:
        return mktime_tz(parsedate_tz(date_str))
    except Exception:
        return None


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_id TEXT UNIQUE,
            date TEXT,
            sender TEXT,
            merchant TEXT,
            type TEXT,
            amount REAL,
            notes TEXT
        )
    """)
    conn.commit()
    conn.close()


def _is_duplicate(cursor, merchant, amount, ts):
    """Return True if a transaction with the same merchant+amount already
    exists within DEDUP_WINDOW_SECS of the given timestamp."""
    if ts is None:
        return False
    # Fetch all rows with same merchant + amount (case-insensitive)
    rows = cursor.execute(
        "SELECT date FROM transactions WHERE LOWER(merchant)=? AND amount=?",
        (merchant.lower(), amount),
    ).fetchall()
    for (existing_date,) in rows:
        existing_ts = _rfc_ts(existing_date)
        if existing_ts and abs(existing_ts - ts) <= DEDUP_WINDOW_SECS:
            return True
    return False


def insert_transaction(email_id, date, sender, merchant, trans_type, amount, notes):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        # Guard 1: email_id uniqueness (original)
        # Guard 2: near-duplicate check — skip if same merchant+amount within 24h
        ts = _rfc_ts(date)
        if _is_duplicate(cursor, merchant, amount, ts):
            print(f"   ⚠️  Skipped near-duplicate: {merchant} ₹{amount}")
            return

        cursor.execute("""
            INSERT OR IGNORE INTO transactions
            (email_id, date, sender, merchant, type, amount, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (email_id, date, sender, merchant, trans_type, amount, notes))
        conn.commit()
    except Exception as e:
        print(f"   ❌ SQL Error: {e}")
    finally:
        conn.close()

def execute_sql(query):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute(query)
        return cursor.fetchall()
    except Exception as e:
        return f"Error: {e}"
    finally:
        conn.close()

# Initialize on import
init_db()