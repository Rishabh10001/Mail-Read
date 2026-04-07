from vector_store import get_db
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from sql_store import execute_sql
import json
import sqlite3

# ── LLM ──────────────────────────────────────────────────────────────────────
llm = ChatOllama(
    model="llama3",
    num_ctx=8192,
    temperature=0.1,   # Low temp = consistent, factual financial answers
    top_p=0.9,
)

# ── Chat History (in-session memory) ─────────────────────────────────────────
chat_history = []   # List of {"role": "user"|"assistant", "content": "..."}

MAX_HISTORY = 6     # Keep last 3 Q&A pairs to avoid token bloat

def _history_str():
    """Format chat history into a readable string for prompts."""
    if not chat_history:
        return "None"
    lines = []
    for msg in chat_history[-MAX_HISTORY:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        lines.append(f"{role}: {msg['content']}")
    return "\n".join(lines)

# ── Intent Router ─────────────────────────────────────────────────────────────
def parse_query(query):
    """
    Classify the query into one of:
      - FINANCE_DB  → needs exact numbers, totals, counts, date ranges
      - EMAIL_RAG   → needs reading email text, summaries, general info
      - HYBRID      → needs both (e.g. "Why did I spend so much on Zomato?")
    """
    prompt = f"""You are a query router for a personal finance assistant.
Classify the user's query into exactly one of these intents:

1. FINANCE_DB  - User wants math, totals, counts, comparisons, or a list of transactions.
   Examples: "total spent", "how much", "list all", "how many times", "biggest purchase", "debits last month", "credits from HDFC"

2. EMAIL_RAG   - User wants to read or summarize email content, get general info not related to amounts.
   Examples: "what did the email say", "summarize", "did I get any email from", "what is the confirmation number"

3. HYBRID      - User wants a financial insight that requires both numbers AND email context.
   Examples: "why did I spend so much on Zomato", "is my Netflix subscription worth it", "explain my biggest transaction"

Previous conversation:
{_history_str()}

User query: {query}

Respond with ONLY valid JSON, no explanation:
{{"intent": "FINANCE_DB" | "EMAIL_RAG" | "HYBRID"}}"""

    try:
        response = llm.invoke(prompt)
        content = response.content.strip()
        # Extract JSON even if the model wraps it in text
        start = content.find("{")
        end = content.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(content[start:end])
    except Exception:
        pass
    # Default: if query has currency/number words, use FINANCE_DB
    financial_keywords = ["spent", "total", "amount", "how much", "cost", "paid", "received",
                          "debit", "credit", "rs", "₹", "rupee", "transaction", "last month",
                          "this month", "biggest", "least", "most", "count", "times"]
    if any(kw in query.lower() for kw in financial_keywords):
        return {"intent": "FINANCE_DB"}
    return {"intent": "EMAIL_RAG"}


# ── SQL Agent ─────────────────────────────────────────────────────────────────
def run_sql_agent(query):
    print("\n🧮 Querying Finance DB...")
    
    sql_prompt = ChatPromptTemplate.from_template("""You are an expert SQLite query writer for a personal finance database.

Database: transactions
Columns:
  - id        INTEGER  (auto primary key)
  - email_id  TEXT     (unique Gmail message ID)
  - date      TEXT     (transaction date, format: 'YYYY-MM-DD' or similar)
  - sender    TEXT     (email sender address)
  - merchant  TEXT     (merchant or recipient name)
  - type      TEXT     (EXACTLY 'credit' for money received, 'debit' for money spent)
  - amount    REAL     (transaction amount in Indian Rupees)
  - notes     TEXT     (brief description)

Rules:
- Write ONLY raw SQLite SQL. No markdown, no explanation, no backticks.
- For date filtering: use strftime or LIKE on the date column.
- 'credit' = inbound (money received), 'debit' = outbound (money spent).
- Always use SUM(amount) for totals, COUNT(*) for counts.
- Use LOWER(merchant) LIKE '%keyword%' for merchant searches.

Previous conversation context:
{history}

User question: {question}

RAW SQL ONLY:""")

    sql_chain = sql_prompt | llm | StrOutputParser()
    
    # Generate SQL
    raw_sql = sql_chain.invoke({
        "question": query,
        "history": _history_str()
    }).strip()
    
    # Clean up any accidental markdown
    raw_sql = raw_sql.replace("```sql", "").replace("```", "").strip()
    
    print(f"   📝 Generated SQL: {raw_sql[:120]}...")

    # Execute SQL with error recovery
    results = execute_sql(raw_sql)
    
    if isinstance(results, str) and results.startswith("Error:"):
        # Try once more with the error fed back to the LLM
        print(f"   ⚠️  SQL Error, retrying... ({results})")
        retry_prompt = f"""The following SQL failed: {raw_sql}
Error: {results}
Fix it and return ONLY the corrected raw SQL for this question: {query}"""
        raw_sql = llm.invoke(retry_prompt).content.strip()
        raw_sql = raw_sql.replace("```sql", "").replace("```", "").strip()
        results = execute_sql(raw_sql)

    if not results or results == []:
        answer = "I couldn't find any matching transactions in your database for that query."
        print(f"\n🧠 ANSWER: {answer}")
        _update_history(query, answer)
        return

    # Final natural-language answer
    answer_prompt = ChatPromptTemplate.from_template("""You are a personal finance assistant helping Rishabh understand his transactions.

User asked: {question}
SQL results: {data}

Provide a clear, concise, and helpful answer. 
- Format amounts in Indian Rupees (₹).
- If listing transactions, present them as a clean numbered list.
- If it's a total, state it clearly with context.
- If the result is empty or zero, say so honestly.
- Keep it under 150 words.""")

    final_answer = (answer_prompt | llm | StrOutputParser()).invoke({
        "question": query,
        "data": str(results)
    })
    
    print(f"\n🧠 ANSWER: {final_answer}")
    _update_history(query, final_answer)


# ── RAG Agent ─────────────────────────────────────────────────────────────────
def run_rag_agent(query):
    print("\n📧 Scanning email content...")
    db = get_db()
    
    # Retrieve more chunks for broad questions
    docs = db.similarity_search(query, k=8)
    
    if not docs:
        answer = "I could not find any relevant emails to answer your question."
        print(f"\n🧠 ANSWER: {answer}")
        _update_history(query, answer)
        return

    context = "\n\n---\n\n".join([d.page_content for d in docs])
    
    rag_prompt = ChatPromptTemplate.from_template("""You are a helpful email assistant for Rishabh.
Answer the question based ONLY on the provided email content below.
If the answer is not present in the emails, say: "I could not find the answer in your emails."

Previous conversation:
{history}

Email content:
{context}

Question: {question}

Answer concisely and helpfully:""")

    rag_chain = rag_prompt | llm | StrOutputParser()
    final_answer = rag_chain.invoke({
        "context": context,
        "question": query,
        "history": _history_str()
    })
    
    print(f"\n🧠 ANSWER: {final_answer}")
    _update_history(query, final_answer)


# ── Hybrid Agent ──────────────────────────────────────────────────────────────
def run_hybrid_agent(query):
    print("\n🔀 Running Hybrid Analysis (SQL + Email context)...")
    
    # Get SQL data first
    sql_prompt = ChatPromptTemplate.from_template("""Write a raw SQLite query for this question.
Table: transactions | Columns: id, email_id, date, sender, merchant, type, amount, notes
type is 'credit' or 'debit'. Return ONLY raw SQL.
Question: {question}""")
    
    raw_sql = (sql_prompt | llm | StrOutputParser()).invoke({"question": query}).strip()
    raw_sql = raw_sql.replace("```sql", "").replace("```", "").strip()
    sql_results = execute_sql(raw_sql)
    
    # Get RAG context
    db = get_db()
    docs = db.similarity_search(query, k=5)
    context = "\n\n".join([d.page_content for d in docs])
    
    # Combine both for a rich answer
    hybrid_prompt = ChatPromptTemplate.from_template("""You are a personal finance advisor for Rishabh.
Use BOTH the structured transaction data AND the email context to answer the question thoughtfully.

Transaction data (SQL): {sql_data}
Email context: {context}

Previous conversation:
{history}

Question: {question}

Provide a detailed, insightful answer. Format amounts in ₹.""")
    
    final_answer = (hybrid_prompt | llm | StrOutputParser()).invoke({
        "question": query,
        "sql_data": str(sql_results),
        "context": context[:2000],   # Cap to avoid token overflow
        "history": _history_str()
    })
    
    print(f"\n🧠 ANSWER: {final_answer}")
    _update_history(query, final_answer)


# ── History Helper ────────────────────────────────────────────────────────────
def _update_history(question, answer):
    chat_history.append({"role": "user", "content": question})
    chat_history.append({"role": "assistant", "content": answer})
    # Trim to avoid unbounded growth
    if len(chat_history) > MAX_HISTORY * 2:
        del chat_history[:2]


# ── Main Entry Point ──────────────────────────────────────────────────────────
def ask(query):
    parsed = parse_query(query)
    intent = parsed.get("intent", "EMAIL_RAG")
    
    if intent == "FINANCE_DB":
        run_sql_agent(query)
    elif intent == "HYBRID":
        run_hybrid_agent(query)
    else:
        run_rag_agent(query)


if __name__ == "__main__":
    while True:
        q = input("\nAsk (or 'exit'): ").strip()
        if q.lower() == "exit":
            break
        if q:
            ask(q)