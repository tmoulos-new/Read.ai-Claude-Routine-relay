"""
Simulates a Read.ai webhook delivery against a locally running relay,
so you can test the whole pipeline without waiting on a real meeting.

Usage:
    python test_webhook.py
"""

import base64
import hashlib
import hmac
import json
import os

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

RELAY_URL = os.environ.get("RELAY_URL", "http://localhost:8080/readai-webhook")
SIGNING_KEY = os.environ["READ_AI_SIGNING_KEY"]

payload = {
    "session_id": "test-session-001",
    "request_id": "test-request-001",
    "trigger": "manual",
    "title": "Weekly Growth Sync",
    "summary": "The team reviewed mia-Pizzeria's ad performance and agreed to test two new hooks this week.",
    "participants": [{"name": "Theo"}, {"name": "Maria"}],
    "topics": [{"text": "Ad creative performance"}, {"text": "Next sprint priorities"}],
    "action_items": [{"text": "Ship the two new TikTok hooks by Friday"}],
    "key_questions": [{"text": "Should we increase budget on the top-performing ad?"}],
    "report_url": "https://app.read.ai/analytics/meetings/test-session-001",
}

body = json.dumps(payload).encode("utf-8")
signature = hmac.new(base64.b64decode(SIGNING_KEY), body, hashlib.sha256).hexdigest()

resp = requests.post(
    RELAY_URL,
    data=body,
    headers={"Content-Type": "application/json", "X-Read-Signature": signature},
    timeout=15,
)

print(resp.status_code, resp.text)
