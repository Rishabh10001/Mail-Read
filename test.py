from utils import get_last_run_time
from read_mail import fetch_and_ingest
from vector_store import get_db
import time

def main():
    print("🚀 Starting Financial OS...")
    
    # Check for bookmark
    last_run = get_last_run_time()
    
    # Run the Sync
    fetch_and_ingest(after=last_run)
    
    print("\n✅ Sync Complete. System Ready.")
    
    # Transition to Query Mode
    from query import ask
    while True:
        user_q = input("\nAsk about your finances (or 'exit'): ")
        if user_q.lower() == 'exit': break
        ask(user_q) 


if __name__ == "__main__":
    main()