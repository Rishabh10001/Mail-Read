import os
import json
import time
import base64
import pickle
import threading
import re
import socket
import datetime
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from concurrent.futures import ThreadPoolExecutor, as_completed
from process_mail import process_emails
from utils import update_last_run_time, get_last_run_time

def load_processed_cache():
    if os.path.exists("processed_cache.txt"):
        with open("processed_cache.txt", "r") as f:
            return set(f.read().splitlines())
    return set()

def append_to_cache(email_ids):
    with open("processed_cache.txt", "a") as f:
        for eid in email_ids:
            f.write(str(eid) + "\n")

# 🟢 FIX 1: Prevent SSL Handshake Timeouts
socket.setdefaulttimeout(60) 

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
thread_local = threading.local()

def get_service(creds=None):
    if not creds:
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as t: 
                creds = pickle.load(t)
        else:
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=8080)
            with open('token.pickle', 'wb') as t: 
                pickle.dump(creds, t)
    return build('gmail', 'v1', credentials=creds)

def get_thread_safe_service(creds):
    if not hasattr(thread_local, "service"): 
        thread_local.service = build('gmail', 'v1', credentials=creds)
    return thread_local.service

def get_body(payload):
    """Recursively extracts plain text or HTML body from email payload."""
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] in ['text/plain', 'text/html']:
                data = part['body'].get('data')
                if data: return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
            if 'parts' in part:
                res = get_body(part)
                if res: return res
    else:
        data = payload.get('body', {}).get('data')
        if data: return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    return ""

def fetch_single_email(creds, message_id, thread_id):
    service = get_thread_safe_service(creds)
    try:
        m = service.users().messages().get(userId='me', id=message_id, format='full').execute()
        payload = m['payload']
        headers = payload.get("headers", [])
        
        body = get_body(payload)
        
        return {
            "email_id": f"{thread_id}_{message_id}",
            "date_ts": int(m.get('internalDate')),
            "sender": next((h['value'] for h in headers if h['name'].lower() == 'from'), "Unknown"),
            "subject": next((h['value'] for h in headers if h['name'].lower() == 'subject'), "No Subject"),
            "date": next((h['value'] for h in headers if h['name'].lower() == 'date'), ""),
            "body": body
        }
    except Exception:
        return None

def fetch_and_ingest(after=None):
    # Load Credentials
    with open('token.pickle', 'rb') as t: 
        creds = pickle.load(t)
    
    main_service = get_service(creds)
    
    # 🟢 FIX 2: Precision Resuming
    # Gmail API handles Unix timestamps in the 'after' parameter perfectly.
    import json
    
    query = "in:inbox"
    if after:
        # Use exact timestamp for true resumption so days are not recycled
        query += f" after:{int(float(after))}"
    else:
        if os.path.exists("resume_oldest.json"):
            try:
                with open("resume_oldest.json", "r") as f:
                    oldest_ts = json.load(f).get("oldest")
                    if oldest_ts:
                        query += f" before:{int(float(oldest_ts))}"
                        print(f"⏪ Bypassing new emails! Resuming backfill from {datetime.datetime.fromtimestamp(oldest_ts).strftime('%b %d %Y')}")
            except: pass
            
    print(f"DEBUG CHECK - Query is: {query}") # 👈 Look for this in your terminal!
    print(f"🚀 Gmail Query: {query}")

    next_token = None
    page = 1
    sync_max_ts = 0

    while True:
        print(f"\n📄 Fetching Page {page}...")
        try:
            res = main_service.users().messages().list(
                userId='me', q=query, pageToken=next_token, maxResults=50
            ).execute()
        except Exception as e:
            print(f"⚠️ API Error: {e}. Retrying in 10s...")
            time.sleep(10)
            continue
        
        messages = res.get('messages', [])
        if not messages: 
            print("🏁 No new emails found since last checkpoint.")
            break

        page_emails = []
        print(f"⚡ Downloading {len(messages)} emails in parallel...")
        
        # 🟢 Lowered max_workers to 50 for MacBook Air stability
        with ThreadPoolExecutor(max_workers=50) as exe:
            futures = [exe.submit(fetch_single_email, creds, m['id'], m['threadId']) for m in messages]
            for f in as_completed(futures):
                r = f.result()
                if r: page_emails.append(r)

        if page_emails:
            # CHECK CACHE SO WE NEVER PROCESS THE SAME EMAIL TWICE
            seen_cache = load_processed_cache()
            fresh_emails = [e for e in page_emails if e['email_id'] not in seen_cache]
            
            if not fresh_emails:
                print("⏭️  All emails in this page already processed. Skipping AI extraction...")
            else:
                process_emails(fresh_emails)
                print(f"✅ Processed {len(fresh_emails)} emails → SQL.")

                # Lock these emails into cache so they are permanently immune to restarts
                append_to_cache([e['email_id'] for e in fresh_emails])
            
            # Track the highest timestamp seen across all pages
            valid_ts = [e['date_ts'] for e in page_emails if e.get('date_ts')]
            if valid_ts:
                page_max = max(valid_ts) / 1000 
                page_min = min(valid_ts) / 1000
                if page_max > sync_max_ts:
                    sync_max_ts = page_max
                print(f"🗓️  Historical Timeline: {time.ctime(page_min)}")

                # ── Save checkpoint after EVERY page ─────────────────────────
                # Gmail returns newest-first, so after page 1 the checkpoint
                # is already at the most recent email. If interrupted, the next
                # run starts from here — no full inbox rescan needed.
                stored = get_last_run_time()
                if stored is None or sync_max_ts > stored:
                    update_last_run_time(sync_max_ts)
                
                # Anchor the backfill marker so if aborted, it only queries emails OLDER than this
                if not after:
                    with open("resume_oldest.json", "w") as f:
                        json.dump({"oldest": page_min}, f)

        next_token = res.get('nextPageToken')
        if not next_token: break
        
        page += 1
        print("⏳ Port cooling down (3s)...")
        time.sleep(3)

    if os.path.exists("resume_oldest.json"):
        os.remove("resume_oldest.json")

    print(f"\n🎉 Sync Complete. Checkpoint: {time.ctime(sync_max_ts) if sync_max_ts else 'N/A'}")