import json
import os
from datetime import datetime

CHECKPOINT_FILE = "last_run.json"

def get_last_run_time():
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, "r") as f:
            data = json.load(f)
            val = data.get("last_run")
            return float(val) if val else None # This will now be a UNIX timestamp
    except Exception:
        return None

# FIX: Return epoch timestamp as a string so Gmail fetches from the exact second
def get_after_date(last_run):
    return str(int(last_run)) 

def update_last_run_time(timestamp=None):
    # FIX: Save current time as a UNIX timestamp, or a provided timestamp
    if timestamp is None:
        timestamp = datetime.utcnow().timestamp()
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"last_run": timestamp}, f)