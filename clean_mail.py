# clean_mail.py
from bs4 import BeautifulSoup
import re

def clean_email(text):
    if not text:
        return None

    # HTML → text
    soup = BeautifulSoup(text, "html.parser")
    text = soup.get_text()

    # remove replies/forwards
    markers = ["On ", "wrote:", "From:", "Sent:", "Forwarded message"]
    for m in markers:
        idx = text.find(m)
        if idx != -1:
            text = text[:idx]

    # remove signatures
    sig_markers = ["Regards,", "Thanks,", "Best,"]
    for m in sig_markers:
        idx = text.lower().find(m.lower())
        if idx != -1:
            text = text[:idx]

    # FIX: Commented out URL removal so links remain intact in the DB
    # text = re.sub(r"http\S+", "", text)

    # normalize whitespace
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)