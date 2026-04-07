import re
import json
import math
import hashlib
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from concurrent.futures import ThreadPoolExecutor, as_completed
from langchain_ollama import ChatOllama
from classes.ollama_embedding import OllamaEmbeddings
from sql_store import insert_transaction 

# Setup
splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=200)
embedding_model = OllamaEmbeddings(model="nomic-embed-text")
# Increased context window to 4096 to prevent 'Char 0' crashes
extractor_llm = ChatOllama(
    model="llama3", 
    num_ctx=16384,      # 🟢 Double the memory! 16GB can handle this easily.
    temperature=0.2,    # 🟢 Slightly higher for better "Analytical" writing
    top_p=0.9,      # Llama 3 takes longer to process than 3.2
)

def log_rejection(subject, reason, score=None):
    score_text = f" [Score: {score:.2f}]" if score is not None else ""
    with open("rejected_emails.txt", "a", encoding="utf-8") as f:
        f.write(f"REJECTED: {reason}{score_text} | Subject: {subject}\n")

def strip_html_tags(text):
    """Universally strips code noise so AI only sees human-readable text."""
    if not text: return ""
    text = re.sub(r'<(style|script)[^>]*>.*?</\1>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()

def cosine_similarity(v1, v2):
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_v1 = math.sqrt(sum(a * a for a in v1))
    norm_v2 = math.sqrt(sum(b * b for b in v2))
    return dot_product / (norm_v1 * norm_v2) if norm_v1 and norm_v2 else 0.0

FINANCE_CONCEPT = "capital movement, asset purchase, bank debits, mutual fund investments, UPI payments, bills"
TARGET_VECTOR = embedding_model.embed_query(FINANCE_CONCEPT)

def extract_structured_data(email):
    # Noise reduction
    clean_body = strip_html_tags(email['body'])
    ai_input = clean_body[:1500] 
    text_to_check = (email['subject'] + " " + ai_input).lower()

    # 1. VIP Check
    vip_words = ["₹", "inr", "rs.", "paid", "debited", "credited", "upi", "folio", "statement", "txn"]
    if not any(kw in text_to_check for kw in vip_words):
        # 2. Semantic Bouncer
        email_vector = embedding_model.embed_query(text_to_check[:1000])
        if cosine_similarity(email_vector, TARGET_VECTOR) < 0.45:
            log_rejection(email['subject'], "LOW_SIMILARITY")
            return

    # 3. Universal Extraction Prompt (First Principles)
    # 🧠 CONTEXTUAL DIRECTION PROMPT (Zero Hardcoding)
    # 🧠 FIRST PRINCIPLES: THE REALITY-CHECK PROMPT
    # 🧠 UNIVERSAL ROLE-BASED PROMPT (Zero Keywords, Zero Hardcoding)
    # 🧠 UNIVERSAL EVENT-STATE PROMPT (Zero Hardcoding)
    prompt = f"""
    You are a Financial Auditor. Your task is to distinguish between an EXECUTED EVENT and a PROPOSED STATE.
    
    LOGICAL AUDIT:
    1. EXECUTED (is_transaction: true): 
       - Value has ALREADY been exchanged or the settlement is currently in progress.
       - The language describes an ACTION completed (e.g., "Paid", "Received", "Success", "Processed").
       - FIND the "Counter-Party": This is the actual shop, person, or service where the money went or came from.
       - Look for names following: "Paid to", "Spent at", "VPA", "Info", or "Transfer to".
       - Example: If the text says "Paid to Zomato using HDFC Card", the merchant is "Zomato".
       - ASSET ACQUISITION: If the user receives "Units", "Shares", or "Quantity" for an "Amount Paid", it is an EXECUTED transaction.
    
    2. PROPOSED/CONDITIONAL (is_transaction: false): 
       - The data describes a potential future value, a contract offer, a quota, or an eligibility status.
       - No capital has actually moved yet.
       - The language describes a POSSIBILITY (e.g., "Offer", "Shortlisted", "Eligible", "Estimate", "Statement for review").

    DIRECTIONAL LOGIC:
    - If EXECUTED: Identify if the User is the Source (Debit) or the Recipient (Credit).
    
    STRICT JSON OUTPUT:
    {{
        "is_transaction": boolean,
        "type": "credit/debit",
        "merchant": "Actual Recipient or Source and the bank name",
        "amount": 0.0,
        "notes": "Transaction context or any other relevant info"
    }}

    Data:
    Subject: {email['subject']}
    Body: {ai_input}
    """
    
    try:
        response = extractor_llm.invoke(prompt)
        raw_text = response.content.strip()

        # 🟢 1. THE "GREEDY" ISOLATION (Fixes 'Extra data' and 'Char 0' crashes)
        # This keeps your existing logic but makes it immune to AI chatter.
        # It ensures that even if Llama 3 explains its rejection, the script won't crash.
        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        
        if match:
            # Strip // comments which Llama 3 (8B) likes to add but JSON cannot parse
            clean_json = re.sub(r'//.*', '', match.group(0))
            data = json.loads(clean_json)
        else:
            # If no JSON exists, it's not a transaction (e.g., plain newsletters)
            log_rejection(email['subject'], "NO_JSON_STRUCTURE")
            return

        # 🟢 2. LIST FLATTENING (Handles Llama 3 returning arrays like [{}])
        if isinstance(data, list):
            data = data[0] if len(data) > 0 else {"is_transaction": False}

        # 🟢 3. SEMANTIC VALIDATION GATE
        # We only proceed if the AI explicitly confirms an EXECUTED event + valid amount.
        if data.get("is_transaction") is True and data.get("amount"):
            
            # Universal digit stripper: handles symbols (₹, $, Rs) and commas
            amount_val = str(data["amount"])
            amount = float(re.sub(r'[^\d.]', '', amount_val))
            
            # 🟢 4. DIRECTIONAL AWARENESS (Inflow vs Outflow)
            # This captures the 'meaning' of the transaction (credit/debit)
            t_type = data.get("type", "debit").lower().strip()
            
            # 🟢 5. MERCHANT REFINEMENT (The "HDFC" vs "Zomato" fix)
            # Prioritizes the counter-party found in the text over the bank's name.
            merchant = data.get("merchant", "").strip()
            
            # If the AI failed to find a specific merchant or defaulted to the bank name:
            if not merchant or merchant.lower() in ["hdfc", "sbi", "icici", "bank", "unknown"]:
                # Use the clean sender name as a fallback
                merchant = email['sender'].split('<')[0].replace('"', '').strip()

            # 🟢 6. INCREMENTAL STORAGE
            # Ensure sql_store.py insert_transaction accepts the 't_type' argument!
            insert_transaction(
                email["email_id"], 
                email["date"], 
                email["sender"], 
                merchant, 
                t_type,
                amount, 
                data.get("notes", "")
            )
            
            # Visual feedback in Terminal
            # icon = "📈 IN" if t_type == "credit" else "📉 OUT"
            print(f"  [₹{amount}] {merchant}")
            
        else:
            # 🟢 7. LOGICAL REJECTION (The "Infosys" and "NVIDIA" fix)
            # Because the regex fixed the 'Extra data' crash, job alerts and 
            # newsletters now land here safely as intended.
            log_rejection(email['subject'], "AI_REJECTED_LOGICALLY")

    except Exception as e:
        # Catch-all for unexpected syntax errors or code failures
        log_rejection(email['subject'], f"EXTRACTION_CRASH: {str(e)[:100]}")

def process_emails(emails):
    documents = []

    def handle_single(email):
        # Step A: Extract numbers for SQL
        extract_structured_data(email)
        
        # Step B: Chunk text for Vector RAG
        full_text = f"Subject: {email['subject']}\nFrom: {email['sender']}\nBody: {strip_html_tags(email['body'])}"
        chunks = splitter.split_text(full_text)
        
        local_docs = []
        for i, chunk in enumerate(chunks):
            cid = hashlib.md5(f"{email['email_id']}_{i}".encode()).hexdigest()
            local_docs.append(Document(
                page_content=chunk, 
                metadata={**email, "chunk_id": cid, "chunk_index": i}
            ))
        return local_docs

    # Threading bypasses the sequential bottleneck while keeping bounds and accuracy intact
    with ThreadPoolExecutor(max_workers=5) as exe:
        futures = [exe.submit(handle_single, email) for email in emails]
        for f in as_completed(futures):
            res = f.result()
            if res:
                documents.extend(res)
                
    return documents