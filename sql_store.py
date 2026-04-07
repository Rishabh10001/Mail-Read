import sqlite3
import os

DB_FILE = "finance_os.db" 

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 🟢 RESTORED: Re-added the 'type' column for accurate tracking
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

# 🟢 RESTORED: Function signature now takes 7 arguments (including trans_type)
def insert_transaction(email_id, date, sender, merchant, trans_type, amount, notes):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        # 🟢 RESTORED: SQL query now handles exactly 7 columns
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