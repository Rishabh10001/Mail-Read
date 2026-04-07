from vector_store import get_db
import os

def check_status():
    print("🔍 Checking system memory...\n")
    
    # 1. Check Tracker
    if os.path.exists("last_run.json"):
        print("❌ WARNING: last_run.json still exists! Delete it to fetch old emails.")
    else:
        print("✅ last_run.json is deleted. Ready for a fresh, full download.")

    # 2. Check Database
    try:
        db = get_db()
        # Access the underlying Chroma collection to get a fast count
        count = db._collection.count()
        
        if count == 0:
            print("✅ Chroma DB is completely empty (0 chunks).")
        else:
            print(f"❌ WARNING: Chroma DB still contains {count} chunks. Delete the 'chroma_db' folder.")
    except Exception as e:
        print(f"✅ Chroma DB folder does not exist yet (completely clean).")

if __name__ == "__main__":
    check_status()