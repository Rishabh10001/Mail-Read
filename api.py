import os
import time
import threading
import sqlite3
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from utils import get_last_run_time
from categorize import _init_categories_table, run_backfill, CATEGORIES

# ── App ───────────────────────────────────────────────────────────────────────
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    _init_categories_table()
    _init_budgets_table()
    _init_custom_categories_table()
    start_sync()
    yield

app = FastAPI(title="Finance OS", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Sync state ────────────────────────────────────────────────────────────────
_sync_thread: Optional[threading.Thread] = None
_sync_running = False
_sync_stop = threading.Event()
_sync_log: list[str] = []


def _log(msg: str):
    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    _sync_log.append(entry)
    print(entry)
    if len(_sync_log) > 200:
        _sync_log.pop(0)


# ── DB helpers ────────────────────────────────────────────────────────────────
DB_FILE = "finance_os.db"


def _db():
    from email.utils import parsedate, mktime_tz, parsedate_tz

    def rfc_ts(date_str):
        try:
            return mktime_tz(parsedate_tz(date_str)) or 0
        except Exception:
            return 0

    def rfc_month(date_str):
        try:
            p = parsedate(date_str)
            return f"{p[0]:04d}-{p[1]:02d}" if p else None
        except Exception:
            return None

    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.create_function("rfc_ts", 1, rfc_ts)
    conn.create_function("rfc_month", 1, rfc_month)
    return conn


# ── Transactions ──────────────────────────────────────────────────────────────
@app.get("/api/transactions")
def get_transactions(
    page: int = 1,
    limit: int = 25,
    type: Optional[str] = None,
    merchant: Optional[str] = None,
    category: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    sort: str = "id",
    order: str = "desc",
):
    from datetime import datetime, timezone

    conn = _db()
    cur = conn.cursor()

    t_where, params = [], []
    if type in ("debit", "credit"):
        t_where.append("t.type = ?")
        params.append(type)
    if merchant:
        t_where.append("LOWER(t.merchant) LIKE ?")
        params.append(f"%{merchant.lower()}%")
    if category:
        t_where.append("COALESCE(c.category, 'Uncategorized') = ?")
        params.append(category)
    if from_date:
        ts = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc).timestamp()
        t_where.append("rfc_ts(t.date) >= ?")
        params.append(ts)
    if to_date:
        ts = datetime.fromisoformat(to_date).replace(hour=23, minute=59, second=59, tzinfo=timezone.utc).timestamp()
        t_where.append("rfc_ts(t.date) <= ?")
        params.append(ts)
    if min_amount is not None:
        t_where.append("t.amount >= ?")
        params.append(min_amount)
    if max_amount is not None:
        t_where.append("t.amount <= ?")
        params.append(max_amount)

    base_from = "FROM transactions t LEFT JOIN transaction_categories c ON t.id = c.transaction_id"
    wc = ("WHERE " + " AND ".join(t_where)) if t_where else ""
    total = cur.execute(f"SELECT COUNT(*) {base_from} {wc}", params).fetchone()[0]

    sort_col = sort if sort in {"id", "date", "amount", "merchant", "type"} else "t.id"
    if sort_col in {"id", "date", "amount", "merchant", "type"}:
        sort_col = f"t.{sort_col}"
    sort_dir = "DESC" if order.lower() == "desc" else "ASC"
    offset = (page - 1) * limit

    rows = cur.execute(
        f"SELECT t.id, t.email_id, t.date, t.sender, t.merchant, t.type, t.amount, t.notes, "
        f"COALESCE(c.category, 'Uncategorized') as category "
        f"{base_from} {wc} ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "transactions": [dict(r) for r in rows],
    }


# ── Stats ─────────────────────────────────────────────────────────────────────
@app.get("/api/stats/summary")
def get_summary():
    conn = _db()
    cur = conn.cursor()
    income = cur.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='credit'"
    ).fetchone()[0]
    expenses = cur.execute(
        "SELECT COALESCE(SUM(amount),0) FROM transactions WHERE type='debit'"
    ).fetchone()[0]
    count = cur.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()
    return {
        "total_income": round(income, 2),
        "total_expenses": round(expenses, 2),
        "net_balance": round(income - expenses, 2),
        "total_transactions": count,
    }


@app.get("/api/stats/months")
def get_available_months():
    from email.utils import parsedate
    conn = _db()
    rows = conn.execute("SELECT date FROM transactions").fetchall()
    conn.close()
    months = set()
    for row in rows:
        try:
            p = parsedate(row[0])
            if p:
                months.add(f"{p[0]:04d}-{p[1]:02d}")
        except Exception:
            pass
    return {"months": sorted(months, reverse=True)}


@app.get("/api/stats/thismonth")
def get_thismonth(month: Optional[str] = None):
    import calendar
    from email.utils import parsedate
    from datetime import datetime, timedelta

    now = datetime.now()

    if month:
        year, mon = map(int, month.split("-"))
        target = now.replace(year=year, month=mon, day=1)
    else:
        target = now.replace(day=1)

    this_m   = (target.year, target.month)
    prev_dt  = (target - timedelta(days=1))
    last_m   = (prev_dt.year, prev_dt.month)

    conn = _db()
    rows = conn.execute("SELECT date, type, amount, merchant FROM transactions").fetchall()
    conn.close()

    buckets = {
        this_m: {"spend": 0, "income": 0, "debits": []},
        last_m: {"spend": 0, "income": 0, "debits": []},
    }
    for row in rows:
        try:
            p = parsedate(row[0])
            if not p:
                continue
            key = (p[0], p[1])
            if key not in buckets:
                continue
            amt = row[2]
            if row[1] == "debit":
                buckets[key]["spend"] = round(buckets[key]["spend"] + amt, 2)
                buckets[key]["debits"].append({"merchant": row[3], "amount": amt})
            elif row[1] == "credit":
                buckets[key]["income"] = round(buckets[key]["income"] + amt, 2)
        except Exception:
            pass

    this = buckets[this_m]
    last = buckets[last_m]

    def delta(curr, prev):
        if prev == 0:
            return None
        return round((curr - prev) / prev * 100, 1)

    biggest = max(this["debits"], key=lambda x: x["amount"]) if this["debits"] else None
    avg     = round(this["spend"] / len(this["debits"]), 2) if this["debits"] else 0

    is_current = (target.year == now.year and target.month == now.month)
    projection = None
    if is_current and this["debits"]:
        days_in      = calendar.monthrange(now.year, now.month)[1]
        days_elapsed = now.day
        daily_rate   = this["spend"] / days_elapsed if days_elapsed else 0
        projected    = round(daily_rate * days_in, 2)
        projection   = {
            "days_elapsed":    days_elapsed,
            "days_in_month":   days_in,
            "pct_elapsed":     round(days_elapsed / days_in * 100, 1),
            "daily_rate":      round(daily_rate, 2),
            "projected_spend": projected,
            "remaining_spend": round(daily_rate * (days_in - days_elapsed), 2),
        }

    return {
        "this_month":     target.strftime("%b %Y"),
        "last_month":     prev_dt.strftime("%b %Y"),
        "spend":          this["spend"],
        "income":         this["income"],
        "last_spend":     last["spend"],
        "last_income":    last["income"],
        "spend_delta":    delta(this["spend"],   last["spend"]),
        "income_delta":   delta(this["income"],  last["income"]),
        "avg_debit":      avg,
        "tx_count":       len(this["debits"]),
        "biggest":        biggest,
        "is_current_month": is_current,
        "projection":     projection,
    }


@app.get("/api/merchants")
def get_merchant_list():
    conn = _db()
    rows = conn.execute(
        "SELECT DISTINCT merchant FROM transactions "
        "WHERE merchant IS NOT NULL AND merchant != '' "
        "ORDER BY LOWER(merchant) ASC"
    ).fetchall()
    conn.close()
    return {"merchants": [r[0] for r in rows]}


@app.get("/api/stats/recent")
def get_recent(limit: int = 8, month: Optional[str] = None):
    conn = _db()
    if month:
        rows = conn.execute(
            "SELECT date, merchant, type, amount, notes FROM transactions "
            "WHERE rfc_month(date)=? ORDER BY rfc_ts(date) DESC LIMIT ?",
            (month, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT date, merchant, type, amount, notes FROM transactions "
            "ORDER BY rfc_ts(date) DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return {"transactions": [dict(r) for r in rows]}


@app.get("/api/stats/monthly")
def get_monthly():
    from email.utils import parsedate

    conn = _db()
    rows = conn.execute(
        "SELECT date, type, amount FROM transactions WHERE date != '' AND date IS NOT NULL"
    ).fetchall()
    conn.close()

    monthly: dict[str, dict] = {}
    for row in rows:
        try:
            parsed = parsedate(row[0])
            if not parsed:
                continue
            m = f"{parsed[0]:04d}-{parsed[1]:02d}"
            if m not in monthly:
                monthly[m] = {"month": m, "debit": 0.0, "credit": 0.0}
            t = row[1]
            if t in ("debit", "credit"):
                monthly[m][t] = round(monthly[m][t] + row[2], 2)
        except Exception:
            pass

    return {"monthly": sorted(monthly.values(), key=lambda x: x["month"])[-24:]}


@app.get("/api/stats/merchants")
def get_top_merchants(limit: int = 10, month: Optional[str] = None):
    conn = _db()
    cur = conn.cursor()
    if month:
        rows = cur.execute(
            "SELECT merchant, SUM(amount) as total, COUNT(*) as count "
            "FROM transactions "
            "WHERE type='debit' AND merchant != '' AND merchant IS NOT NULL AND rfc_month(date)=? "
            "GROUP BY LOWER(merchant) ORDER BY total DESC LIMIT ?",
            (month, limit),
        ).fetchall()
    else:
        rows = cur.execute(
            "SELECT merchant, SUM(amount) as total, COUNT(*) as count "
            "FROM transactions "
            "WHERE type='debit' AND merchant != '' AND merchant IS NOT NULL "
            "GROUP BY LOWER(merchant) ORDER BY total DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return {"merchants": [dict(r) for r in rows]}


# ── Categories ───────────────────────────────────────────────────────────────

_categorize_running = False
_categorize_log: list[str] = []


def _init_custom_categories_table():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS custom_categories (
            name TEXT PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()


def _all_categories() -> list[str]:
    conn = sqlite3.connect(DB_FILE)
    custom = [r[0] for r in conn.execute("SELECT name FROM custom_categories ORDER BY name").fetchall()]
    conn.close()
    return CATEGORIES + custom


@app.get("/api/categories")
def get_category_list():
    return {"categories": _all_categories()}


class CategoryCreate(BaseModel):
    name: str


@app.post("/api/categories")
def add_category(body: CategoryCreate):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    if name in CATEGORIES:
        raise HTTPException(status_code=400, detail="Built-in category already exists")
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR IGNORE INTO custom_categories (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()
    return {"status": "ok", "categories": _all_categories()}


@app.delete("/api/categories/{name}")
def delete_category(name: str):
    if name in CATEGORIES:
        raise HTTPException(status_code=400, detail="Cannot delete built-in categories")
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM custom_categories WHERE name = ?", (name,))
    conn.commit()
    conn.close()
    return {"status": "ok", "categories": _all_categories()}


class CategoryUpdate(BaseModel):
    category: str


@app.patch("/api/transactions/{tx_id}/category")
def update_transaction_category(tx_id: int, body: CategoryUpdate):
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "INSERT OR REPLACE INTO transaction_categories (transaction_id, category) VALUES (?, ?)",
        (tx_id, body.category),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/api/stats/categories")
def get_category_stats(month: Optional[str] = None):
    conn = _db()
    if month:
        rows = conn.execute(
            "SELECT COALESCE(c.category, 'Uncategorized') as category, "
            "SUM(t.amount) as total, COUNT(*) as count "
            "FROM transactions t "
            "LEFT JOIN transaction_categories c ON t.id = c.transaction_id "
            "WHERE t.type='debit' AND rfc_month(t.date)=? "
            "GROUP BY category ORDER BY total DESC",
            (month,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT COALESCE(c.category, 'Uncategorized') as category, "
            "SUM(t.amount) as total, COUNT(*) as count "
            "FROM transactions t "
            "LEFT JOIN transaction_categories c ON t.id = c.transaction_id "
            "WHERE t.type='debit' "
            "GROUP BY category ORDER BY total DESC",
        ).fetchall()
    conn.close()
    return {"categories": [dict(r) for r in rows]}


@app.get("/api/categorize/status")
def categorize_status():
    conn = _db()
    total = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    done = conn.execute("SELECT COUNT(*) FROM transaction_categories").fetchone()[0]
    conn.close()
    return {
        "running": _categorize_running,
        "total": total,
        "categorized": done,
        "pending": total - done,
        "log": _categorize_log[-30:],
    }


@app.get("/api/deduplicate/preview")
def dedup_preview():
    """Return groups of duplicate transactions without deleting anything."""
    from sql_store import _rfc_ts, DEDUP_WINDOW_SECS
    conn = _db()
    rows = conn.execute(
        "SELECT id, email_id, date, merchant, amount, notes FROM transactions ORDER BY id"
    ).fetchall()
    conn.close()

    seen: list[dict] = []
    duplicate_ids: list[int] = []
    groups: list[dict] = []

    for row in rows:
        rid, email_id, date, merchant, amount, notes = row
        ts = _rfc_ts(date)
        found = False
        for s in seen:
            if (s["merchant"].lower() == (merchant or "").lower()
                    and s["amount"] == amount
                    and ts is not None
                    and s["ts"] is not None
                    and abs(s["ts"] - ts) <= DEDUP_WINDOW_SECS):
                duplicate_ids.append(rid)
                s["dupes"].append({"id": rid, "date": date, "email_id": email_id})
                found = True
                break
        if not found:
            entry = {"id": rid, "merchant": merchant, "amount": amount,
                     "date": date, "ts": ts, "dupes": []}
            seen.append(entry)

    groups = [{"keep": {"id": s["id"], "merchant": s["merchant"],
                        "amount": s["amount"], "date": s["date"]},
               "remove": s["dupes"]}
              for s in seen if s["dupes"]]

    return {"duplicate_count": len(duplicate_ids), "groups": groups}


@app.post("/api/deduplicate/run")
def dedup_run():
    """Delete duplicate transactions, keeping the earliest (lowest id) in each group."""
    from sql_store import _rfc_ts, DEDUP_WINDOW_SECS
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute(
        "SELECT id, date, merchant, amount FROM transactions ORDER BY id"
    ).fetchall()

    seen: list[dict] = []
    to_delete: list[int] = []

    for rid, date, merchant, amount in rows:
        ts = _rfc_ts(date)
        found = False
        for s in seen:
            if (s["merchant"].lower() == (merchant or "").lower()
                    and s["amount"] == amount
                    and ts is not None
                    and s["ts"] is not None
                    and abs(s["ts"] - ts) <= DEDUP_WINDOW_SECS):
                to_delete.append(rid)
                found = True
                break
        if not found:
            seen.append({"merchant": merchant, "amount": amount, "ts": ts})

    if to_delete:
        conn.execute(
            f"DELETE FROM transactions WHERE id IN ({','.join('?'*len(to_delete))})",
            to_delete,
        )
        # Also remove their category entries
        conn.execute(
            f"DELETE FROM transaction_categories WHERE transaction_id IN ({','.join('?'*len(to_delete))})",
            to_delete,
        )
        conn.commit()

    conn.close()
    return {"removed": len(to_delete)}


@app.post("/api/categorize/start")
def start_categorize():
    global _categorize_running

    if _categorize_running:
        return {"status": "already_running"}

    _categorize_running = True
    _categorize_log.clear()

    def _log_cat(msg: str):
        ts = time.strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        _categorize_log.append(entry)
        print(entry)

    def _worker():
        global _categorize_running
        try:
            run_backfill(batch_size=20, log_fn=_log_cat)
        except Exception as e:
            _log_cat(f"Error: {e}")
        finally:
            _categorize_running = False

    threading.Thread(target=_worker, daemon=True).start()
    return {"status": "started"}


# ── Budgets ───────────────────────────────────────────────────────────────────

def _init_budgets_table():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            category      TEXT PRIMARY KEY,
            monthly_limit REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


class BudgetItem(BaseModel):
    category: str
    monthly_limit: float


class BudgetPayload(BaseModel):
    budgets: list[BudgetItem]


@app.get("/api/budgets")
def get_budgets():
    conn = sqlite3.connect(DB_FILE)
    rows = conn.execute("SELECT category, monthly_limit FROM budgets").fetchall()
    conn.close()
    return {"budgets": [{"category": r[0], "monthly_limit": r[1]} for r in rows]}


@app.post("/api/budgets")
def save_budgets(payload: BudgetPayload):
    conn = sqlite3.connect(DB_FILE)
    for item in payload.budgets:
        if item.monthly_limit > 0:
            conn.execute(
                "INSERT OR REPLACE INTO budgets (category, monthly_limit) VALUES (?, ?)",
                (item.category, item.monthly_limit),
            )
        else:
            # limit = 0 means "remove budget for this category"
            conn.execute("DELETE FROM budgets WHERE category = ?", (item.category,))
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.get("/api/budgets/status")
def get_budget_status(month: Optional[str] = None):
    from datetime import datetime

    if not month:
        month = datetime.now().strftime("%Y-%m")

    # Actual spend per category for the month
    conn = _db()
    spend_rows = conn.execute(
        "SELECT COALESCE(c.category, 'Uncategorized') as category, "
        "SUM(t.amount) as spent, COUNT(*) as count "
        "FROM transactions t "
        "LEFT JOIN transaction_categories c ON t.id = c.transaction_id "
        "WHERE t.type='debit' AND rfc_month(t.date)=? "
        "GROUP BY category",
        (month,),
    ).fetchall()
    conn.close()

    spend_map = {r[0]: {"spent": round(r[1], 2), "count": r[2]} for r in spend_rows}

    # Budgets
    conn2 = sqlite3.connect(DB_FILE)
    budget_rows = conn2.execute("SELECT category, monthly_limit FROM budgets").fetchall()
    conn2.close()
    budget_map = {r[0]: r[1] for r in budget_rows}

    # Merge: all categories that have a budget OR have spend
    all_cats = sorted(set(list(budget_map.keys()) + list(spend_map.keys())))

    items = []
    for cat in all_cats:
        limit  = budget_map.get(cat, 0)
        spent  = spend_map.get(cat, {}).get("spent", 0)
        count  = spend_map.get(cat, {}).get("count", 0)
        pct    = round(spent / limit * 100, 1) if limit > 0 else None
        remaining = round(limit - spent, 2) if limit > 0 else None
        if pct is None:
            status = "no_budget"
        elif pct >= 100:
            status = "over"
        elif pct >= 80:
            status = "warning"
        else:
            status = "ok"
        items.append({
            "category": cat,
            "budget":    round(limit, 2),
            "spent":     spent,
            "count":     count,
            "pct":       pct,
            "remaining": remaining,
            "status":    status,
        })

    # Sort: over → warning → ok → no_budget, then by spent desc
    order = {"over": 0, "warning": 1, "ok": 2, "no_budget": 3}
    items.sort(key=lambda x: (order[x["status"]], -(x["spent"] or 0)))

    total_budgeted = sum(b for b in budget_map.values())
    total_spent    = sum(s["spent"] for s in spend_map.values())
    over_count     = sum(1 for it in items if it["status"] == "over")

    return {
        "month":          month,
        "total_budgeted": round(total_budgeted, 2),
        "total_spent":    round(total_spent, 2),
        "over_count":     over_count,
        "items":          items,
    }


# ── NL Query ──────────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):
    message: str


@app.post("/api/query")
def nl_query(body: QueryRequest):
    import query as q_module
    result = q_module.ask(body.message)
    return result or {"sql": "", "answer": "No matching transactions found."}


@app.post("/api/query/reset")
def reset_history():
    import query as q_module
    q_module.chat_history.clear()
    return {"status": "ok"}


# ── Sync ──────────────────────────────────────────────────────────────────────
@app.get("/api/sync/status")
def sync_status():
    last_run = get_last_run_time()
    return {
        "running": _sync_running,
        "last_run": last_run,
        "last_run_str": time.ctime(last_run) if last_run else None,
        "log": _sync_log[-50:],
    }


@app.post("/api/sync/start")
def start_sync():
    global _sync_thread, _sync_running

    if _sync_running:
        return {"status": "already_running"}

    _sync_stop.clear()
    _sync_running = True

    def _worker():
        global _sync_running
        from read_mail import fetch_and_ingest

        while not _sync_stop.is_set():
            last = get_last_run_time()
            _log(f"Sync starting (after: {time.ctime(last) if last else 'Jan 2026 cutoff'})")
            try:
                fetch_and_ingest(after=last)
                _log("Sync cycle complete.")
            except Exception as e:
                _log(f"Sync error: {e}")

            # Sleep 2 min in 1s ticks so stop is responsive
            for _ in range(2 * 60):
                if _sync_stop.is_set():
                    break
                time.sleep(1)

        _sync_running = False
        _log("Sync stopped.")

    _sync_thread = threading.Thread(target=_worker, daemon=True)
    _sync_thread.start()
    return {"status": "started"}


@app.post("/api/sync/stop")
def stop_sync():
    _sync_stop.set()
    return {"status": "stopping"}


# ── Static files ──────────────────────────────────────────────────────────────
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def root():
    return FileResponse("static/index.html")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=False)
