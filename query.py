from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from sql_store import execute_sql
from datetime import datetime, timedelta

# ── LLM ──────────────────────────────────────────────────────────────────────
llm = ChatOllama(
    model="llama3",
    num_ctx=8192,
    temperature=0.1,
    top_p=0.9,
)

# ── Chat History ──────────────────────────────────────────────────────────────
chat_history = []
MAX_HISTORY = 6

def _history_str():
    if not chat_history:
        return "None"
    lines = []
    for msg in chat_history[-MAX_HISTORY:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"{role}: {msg['content']}")
    return "\n".join(lines)

def _update_history(question, answer):
    chat_history.append({"role": "user", "content": question})
    chat_history.append({"role": "assistant", "content": answer})
    if len(chat_history) > MAX_HISTORY * 2:
        del chat_history[:2]


# ── SQL Agent ─────────────────────────────────────────────────────────────────
def ask(query):
    print("\n🧮 Querying Finance DB...")

    now = datetime.now()
    this_month = now.strftime("%b %Y")           # e.g. "Apr 2026"
    last_month = (now.replace(day=1) - timedelta(days=1)).strftime("%b %Y")  # e.g. "Mar 2026"
    this_year  = now.strftime("%Y")

    sql_prompt = ChatPromptTemplate.from_template("""You are an expert SQLite query writer for a personal finance database.

Today's date: {today}
This month: {this_month} | Last month: {last_month} | This year: {this_year}

Database: transactions
Columns:
  - id        INTEGER  (auto primary key)
  - email_id  TEXT     (unique Gmail message ID)
  - date      TEXT     (RFC 2822 format, e.g. 'Thu, 09 Apr 2026 12:39:57 +0530')
  - sender    TEXT     (email sender address)
  - merchant  TEXT     (merchant, payee, or payer name)
  - type      TEXT     (EXACTLY 'credit' for money received, 'debit' for money spent)
  - amount    REAL     (transaction amount in Indian Rupees)
  - notes     TEXT     (brief description or context)

CRITICAL date filtering rules (dates are RFC 2822, NOT ISO format):
- This month ({this_month}):  date LIKE '% {this_month}%'
- Last month ({last_month}):  date LIKE '% {last_month}%'
- This year ({this_year}):    date LIKE '%{this_year}%'
- NEVER use strftime() or ISO patterns like '2026-04%' — they will NOT match.
- Always use LIKE with the 3-letter month abbreviation and 4-digit year.

Other rules:
- Write ONLY raw SQLite SQL. No markdown, no explanation, no backticks.
- 'credit' = inbound (money received / salary / refund).
- 'debit'  = outbound (money spent / bill payment).
- Always use SUM(amount) for totals, COUNT(*) for counts.
- Use LOWER(merchant) LIKE '%keyword%' for merchant searches.

Previous conversation:
{history}

User question: {question}

RAW SQL ONLY:""")

    sql_chain = sql_prompt | llm | StrOutputParser()

    raw_sql = sql_chain.invoke({
        "question": query,
        "history": _history_str(),
        "today": now.strftime("%a, %d %b %Y"),
        "this_month": this_month,
        "last_month": last_month,
        "this_year": this_year,
    }).strip()

    # Strip accidental markdown
    raw_sql = raw_sql.replace("```sql", "").replace("```", "").strip()
    print(f"   📝 SQL: {raw_sql[:150]}...")

    results = execute_sql(raw_sql)

    # Error recovery: feed the error back to Llama once
    if isinstance(results, str) and results.startswith("Error:"):
        print(f"   ⚠️  SQL Error, retrying... ({results})")
        retry_prompt = f"""The following SQL failed:
{raw_sql}
Error: {results}
Fix it and return ONLY corrected raw SQL for: {query}"""
        raw_sql = llm.invoke(retry_prompt).content.strip()
        raw_sql = raw_sql.replace("```sql", "").replace("```", "").strip()
        results = execute_sql(raw_sql)

    if not results or results == []:
        answer = "No matching transactions found in your database for that query."
        print(f"\n🧠 {answer}")
        _update_history(query, answer)
        return {"sql": raw_sql, "answer": answer}

    # Translate SQL results → natural language answer
    answer_prompt = ChatPromptTemplate.from_template("""You are a personal finance assistant for Rishabh.

User asked: {question}
SQL results: {data}

Provide a clear, concise, helpful answer.
- Format amounts in Indian Rupees (₹).
- If listing transactions, use a clean numbered list.
- If it's a total, state it clearly.
- Keep it under 150 words.""")

    final_answer = (answer_prompt | llm | StrOutputParser()).invoke({
        "question": query,
        "data": str(results)
    })

    print(f"\n🧠 {final_answer}")
    _update_history(query, final_answer)
    return {"sql": raw_sql, "answer": final_answer}


if __name__ == "__main__":
    while True:
        q = input("\nAsk (or 'exit'): ").strip()
        if q.lower() == "exit":
            break
        if q:
            ask(q)