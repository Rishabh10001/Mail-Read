import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from langchain_ollama import ChatOllama
from sql_store import insert_transaction


# ── Models ───────────────────────────────────────────────────────────────────
# Fast classifier: tiny context, deterministic YES/NO
classifier_llm = ChatOllama(
    model="llama3",
    num_ctx=1024,       # Only needs subject + sender + short snippet
    temperature=0.0,    # Fully deterministic
)

# Full extractor: larger context for complete email body
extractor_llm = ChatOllama(
    model="llama3",
    num_ctx=16384,
    temperature=0.2,
    top_p=0.9,
)

def is_financial_email(subject: str, sender: str, snippet: str) -> bool:
    """
    Lightweight Llama classifier — asks YES/NO before running full extraction.
    Called only when the ₹ fast-path doesn't trigger.
    """
    prompt = f"""You are screening emails to find real financial transaction notifications.
Answer ONLY with YES or NO — nothing else.

Classify as YES if the email is a genuine bank/payment notification:
  - Bank debit/credit alert
  - UPI payment confirmation
  - Mutual fund / SIP purchase confirmation
  - Salary, refund, or cashback credited

Classify as NO for everything else:
  - Marketing, promotional, or pricing emails
  - SaaS subscription offers (HubSpot, Salesforce, etc.)
  - Invoices / quotes not yet paid
  - Account statements or portfolio summaries
  - Order confirmations where payment hasn't happened yet

Sender: {sender}
Subject: {subject}
Preview: {snippet[:300]}

Answer (YES or NO):"""

    try:
        resp = classifier_llm.invoke(prompt).content.strip().upper()
        return resp.startswith("YES")
    except Exception:
        return False  # On failure, skip rather than crash


def log_rejection(subject, reason):
    with open("rejected_emails.txt", "a", encoding="utf-8") as f:
        f.write(f"REJECTED: {reason} | Subject: {subject}\n")

def strip_html_tags(text):
    """Strips HTML so Llama only sees readable text."""
    if not text: return ""
    text = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def extract_structured_data(email):
    clean_body = strip_html_tags(email['body'])
    ai_input = clean_body[:1500]
    text_lower = (email['subject'] + " " + ai_input).lower()

    # ── Tier 1: ₹ fast-path (instant, no model needed) ───────────────────────
    # If ₹ is present it's almost certainly a real Indian bank email.
    # Skip the classifier and go straight to full extraction.
    has_rupee = "₹" in text_lower or "₹" in email['subject']

    if not has_rupee:
        # ── Tier 2: Lightweight Llama classifier (cheap YES/NO) ──────────────
        # Only runs when ₹ is absent — handles UPI refs, investment emails, etc.
        if not is_financial_email(email['subject'], email['sender'], ai_input):
            log_rejection(email['subject'], "CLASSIFIER_NO")
            return

    # ── Llama 3: Classify + Extract ──────────────────────────────────────────
    prompt = f"""You are a Financial Auditor reviewing Indian bank/payment notification emails.
Your ONLY job is to identify emails where money has ALREADY moved — not quotes, offers, or plans.

RULE 1 — IS IT AN EXECUTED TRANSACTION?
  TRUE only if money has ALREADY moved:
    - Bank sends "Your account has been debited ₹X"
    - UPI payment success notification
    - SIP / mutual fund purchase confirmation
    - Salary/refund/cashback credited
  FALSE (mark is_transaction: false) for:
    - Pricing pages, plan upgrades, subscription offers ("Your plan costs ₹X")
    - Invoices or quotes not yet paid
    - Monthly account statements and portfolio summaries
    - Marketing emails from SaaS companies (HubSpot, Salesforce, etc.)
    - Order PLACED but not yet paid / COD orders
    - Any email where the money movement is in the FUTURE or conditional

RULE 2 — DIRECTION
  debit  → money LEFT your account (spent, transferred, invested, SIP, EMI)
  credit → money ARRIVED in your account (salary, UPI received, refund, cashback, interest)
  ⚠️ Buying mutual fund units = DEBIT (you paid money out, even though you received units)

RULE 3 — COUNTER-PARTY (merchant field)
  Find the actual shop / person / fund / service money went to or came from.
  Look after: "Paid to", "VPA", "Transfer to", "Fund:", "Scheme:", "Spent at"
  Example: "Paid to Zomato via HDFC" → merchant: "Zomato"
  Example: "SIP in Axis Bluechip Fund" → merchant: "Axis Bluechip Fund"

STRICT JSON OUTPUT ONLY — no explanation, no markdown:
{{
    "is_transaction": true or false,
    "type": "debit" or "credit",
    "merchant": "exact payee or payer name",
    "amount": 0.0,
    "notes": "one-line context"
}}

Email:
Subject: {email['subject']}
Body: {ai_input}
"""


    try:
        response = extractor_llm.invoke(prompt)
        raw_text = response.content.strip()

        # Extract JSON even if Llama adds explanation around it
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if match:
            clean_json = re.sub(r'//.*', '', match.group(0))
            data = json.loads(clean_json)
        else:
            log_rejection(email['subject'], "NO_JSON_STRUCTURE")
            return

        # Handle case where Llama returns a list
        if isinstance(data, list):
            data = data[0] if len(data) > 0 else {"is_transaction": False}

        if data.get("is_transaction") is True and data.get("amount"):
            amount_val = str(data["amount"])
            amount = float(re.sub(r'[^\d.]', '', amount_val))

            # Sanity check: reject zero, negative, or implausibly large amounts
            if amount <= 0 or amount > 50_000_000:  # Max 5 crore
                log_rejection(email['subject'], f"INVALID_AMOUNT: {amount}")
                return

            # Enforce valid type — default debit if unclear
            t_type = data.get("type", "debit").lower().strip()
            if t_type not in ("credit", "debit"):
                t_type = "debit"

            merchant = data.get("merchant", "").strip()
            # Fall back to sender name only if merchant is blank or a generic bank name
            if not merchant or merchant.lower() in ["hdfc", "sbi", "icici", "axis", "kotak", "bank", "unknown", "n/a"]:
                merchant = email['sender'].split('<')[0].replace('"', '').strip()

            insert_transaction(
                email["email_id"],
                email["date"],
                email["sender"],
                merchant,
                t_type,
                amount,
                data.get("notes", "")
            )

            print(f"  [₹{amount}] {merchant} ({t_type})")

        else:
            log_rejection(email['subject'], "AI_REJECTED_LOGICALLY")

    except Exception as e:
        log_rejection(email['subject'], f"EXTRACTION_CRASH: {str(e)[:100]}")


def process_emails(emails):
    """Process a batch of emails: classify → extract → insert to SQL."""
    def handle_single(email):
        extract_structured_data(email)

    with ThreadPoolExecutor(max_workers=5) as exe:
        futures = [exe.submit(handle_single, email) for email in emails]
        for f in as_completed(futures):
            f.result()
