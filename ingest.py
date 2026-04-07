from read_mail import fetch_emails
from process_mail import process_emails
from vector_store import get_db

def ingest():
    # 📬 Fetch emails
    emails = fetch_emails()

    # 🧩 Process into chunks
    documents = process_emails(emails)

    db = get_db()

    # ⚠️ Avoid duplicates
    existing = db.get()
    existing_ids = set([m["chunk_id"] for m in existing["metadatas"]])

    new_docs = [d for d in documents if d.metadata["chunk_id"] not in existing_ids]

    # 🗄️ Store
    db.add_documents(new_docs)
    db.persist()

    print(f"✅ Stored {len(new_docs)} new chunks")

if __name__ == "__main__":
    ingest()