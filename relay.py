"""
Read.ai -> Claude Code Routine relay.

Sits between Read.ai's webhook and a Claude Code Routine's "Call via API"
trigger. Read.ai authenticates itself to this relay using its own HMAC
signing key (verified here). This relay then authenticates itself to
Anthropic's Fire URL using the routine's bearer token. The two credentials
never need to know about each other.

Required environment variables:
    READ_AI_SIGNING_KEY   Signing key from Read.ai's webhook settings page.
    ROUTINE_FIRE_URL       The routine's Fire URL, e.g.
                           https://api.anthropic.com/v1/claude_code/routines/trig_XXXX/fire
    ROUTINE_TOKEN          The bearer token shown next to "Token" in the
                           routine's "Call via API" trigger settings.

Optional:
    PORT                   Defaults to 8080.
"""

import hashlib
import hmac
import logging
import os
from collections import deque

import requests
from flask import Flask, jsonify, request

try:
    from dotenv import load_dotenv  # local dev convenience only
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("readai-relay")

app = Flask(__name__)

ANTHROPIC_BETA_HEADER = "experimental-cc-routine-2026-04-01"
ANTHROPIC_VERSION = "2023-06-01"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


READ_AI_SIGNING_KEY = _require_env("READ_AI_SIGNING_KEY")
ROUTINE_FIRE_URL = _require_env("ROUTINE_FIRE_URL")
ROUTINE_TOKEN = _require_env("ROUTINE_TOKEN")

# Lightweight in-process de-dupe so retried/duplicate Read.ai deliveries
# don't fire the routine twice. Resets on restart and isn't shared across
# multiple instances -- swap for Redis/a DB if you run more than one.
_RECENT_REQUEST_IDS = deque(maxlen=500)


def _is_duplicate(request_id):
    if not request_id:
        return False
    if request_id in _RECENT_REQUEST_IDS:
        return True
    _RECENT_REQUEST_IDS.append(request_id)
    return False


def verify_read_ai_signature(raw_body: bytes, signature_header: str) -> bool:
    """Recompute the HMAC-SHA256 signature over the exact raw body and
    compare it against X-Read-Signature using a constant-time check."""
    if not signature_header:
        return False
    expected = hmac.new(READ_AI_SIGNING_KEY.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def build_routine_text(payload: dict) -> str:
    """The routine's API trigger only accepts a literal string in `text`,
    not structured JSON -- shape the Read.ai payload into readable prose."""
    title = payload.get("title") or "Untitled meeting"
    trigger = payload.get("trigger", "unknown")
    summary = payload.get("summary") or "(no summary provided)"
    participants = payload.get("participants") or []
    topics = payload.get("topics") or []
    action_items = payload.get("action_items") or []
    key_questions = payload.get("key_questions") or []
    report_url = payload.get("report_url")

    lines = [f'New Read.ai meeting report: "{title}" (trigger: {trigger}).']

    if participants:
        names = ", ".join(p.get("name", "Unknown") for p in participants)
        lines.append(f"Participants: {names}")

    lines += ["", "Summary:", summary]

    def _section(label, items):
        if not items:
            return
        lines.append("")
        lines.append(label)
        for item in items:
            text = item.get("text", item) if isinstance(item, dict) else item
            lines.append(f"- {text}")

    _section("Topics discussed:", topics)
    _section("Action items:", action_items)
    _section("Key questions raised:", key_questions)

    if report_url:
        lines += ["", f"Full report: {report_url}"]

    return "\n".join(lines)


@app.route("/readai-webhook", methods=["POST"])
def readai_webhook():
    raw_body = request.get_data()  # exact raw bytes -- required for the signature check
    signature = request.headers.get("X-Read-Signature", "")

    if not verify_read_ai_signature(raw_body, signature):
        logger.warning("Rejected webhook: signature mismatch")
        return jsonify({"error": "invalid signature"}), 401

    payload = request.get_json(silent=True) or {}
    request_id = payload.get("request_id")

    if _is_duplicate(request_id):
        logger.info("Duplicate request_id=%s, skipping re-fire", request_id)
        return jsonify({"status": "duplicate", "routine_fired": False}), 200

    logger.info("Verified Read.ai webhook request_id=%s", request_id)
    text = build_routine_text(payload)

    try:
        resp = requests.post(
            ROUTINE_FIRE_URL,
            headers={
                "Authorization": f"Bearer {ROUTINE_TOKEN}",
                "anthropic-beta": ANTHROPIC_BETA_HEADER,
                "anthropic-version": ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            },
            json={"text": text},
            timeout=15,
        )
    except requests.RequestException:
        logger.exception("Failed to reach the routine Fire URL")
        # Still ack Read.ai with 2xx so it doesn't retry indefinitely; the
        # failure is in the logs for you to investigate.
        return jsonify({"status": "received", "routine_fired": False}), 200

    if resp.status_code >= 300:
        logger.error("Routine fire failed: %s %s", resp.status_code, resp.text[:500])
        return jsonify({"status": "received", "routine_fired": False}), 200

    session = resp.json()
    logger.info("Routine fired: session_url=%s", session.get("claude_code_session_url"))
    return jsonify({"status": "received", "routine_fired": True}), 200


@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
