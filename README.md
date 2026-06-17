# Read.ai → Claude Routine relay

A small Flask app that sits between a Read.ai webhook and a Claude Code Routine's "Call via API" trigger.

## Why this exists

Read.ai signs its webhooks with its own signing key, which your server verifies via the `X-Read-Signature` header (an HMAC-SHA256 over the raw body). Anthropic's routine Fire URL authenticates the opposite way: it requires an `Authorization: Bearer <routine token>` header. Read.ai has no field to attach a custom header like that to its outgoing webhook, and Anthropic's endpoint has no idea what Read.ai's signature is — so the two can't talk directly. This relay verifies Read.ai on one side and authenticates to Anthropic on the other.

## How it works

1. Read.ai POSTs a meeting report to this relay's `/readai-webhook` endpoint.
2. The relay recomputes the HMAC-SHA256 signature over the raw body using `READ_AI_SIGNING_KEY` and compares it to the `X-Read-Signature` header. A mismatch is rejected with 401, before anything is forwarded.
3. On a match, it formats the meeting title, summary, topics, action items, and key questions into a plain-text string (the routine's `text` field only accepts a literal string, not JSON).
4. It POSTs that string to `ROUTINE_FIRE_URL` with `Authorization: Bearer ROUTINE_TOKEN` and the required `anthropic-beta` header, which starts a new routine session.
5. It returns 200 to Read.ai either way (logging any forwarding failure), so Read.ai doesn't retry indefinitely.

Tested locally end to end: valid signatures verify and forward correctly, invalid/missing signatures are rejected with 401 before any call to Anthropic is made.

## Setup

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# fill in .env with your real values
```

## Environment variables

| Variable | Where to find it |
|---|---|
| `READ_AI_SIGNING_KEY` | Read.ai → Apps & Integrations → Webhooks → your webhook's signing key |
| `ROUTINE_FIRE_URL` | The routine's "Call via API" trigger, the "Fire URL" field |
| `ROUTINE_TOKEN` | Same screen, the "Token" field (shown once — copy it immediately) |

## Run locally

```bash
python relay.py
```

Test the whole pipeline without waiting for a real meeting:

```bash
python test_webhook.py
```

This computes a real signature with your signing key and posts a fake meeting report to your local relay, so you can confirm verification and forwarding both work before pointing Read.ai at anything.

## Deploy it somewhere Read.ai can reach

Read.ai needs a public HTTPS URL, so this has to run somewhere reachable from the internet, not on your laptop. Any small Python host works:

- **Render / Railway / Fly.io**: push this folder as a repo, set the three environment variables in the dashboard, set the start command to `gunicorn relay:app`.
- **A VPS**: run `gunicorn -w 2 -b 0.0.0.0:8080 relay:app` behind nginx or Caddy for TLS, under systemd so it restarts on boot.
- **Google Cloud Run / AWS Lambda**: works too, but a serverless platform like Lambda needs a small adapter (e.g. Mangum) since this is written as a plain Flask app rather than serverless-native — happy to adapt it if you go that route.
- **Vercel**: push this folder as a repo and import it as a new Vercel project. Vercel auto-detects Flask from `requirements.txt` and picks up `index.py` (included here, it just re-exports the `app` object from `relay.py`) as the entrypoint -- no `vercel.json` needed. Set the three environment variables under Project Settings → Environment Variables, then deploy. Your webhook URL becomes `https://<your-project>.vercel.app/readai-webhook`.
  - Timeouts aren't an issue: Vercel's current default execution model gives even the free Hobby tier up to 300s per request, far more than the ~15s call this makes to Anthropic.
  - One caveat specific to Vercel: the in-memory `request_id` de-dupe (see Notes below) assumes a long-running process. On Vercel, each invocation can land on a fresh, short-lived instance, so it may not reliably catch duplicate deliveries the way it would on a VM. Worst case is two routine sessions firing for the same meeting rather than one -- not a security issue, just a minor inefficiency. Swap in Vercel KV or Upstash Redis if you want it solid.

## Point Read.ai at it

In Read.ai's webhook settings, set the webhook URL to `https://<your-deployed-host>/readai-webhook`. Use the "Send test request" button there to confirm you get a 200 back, then trigger a real meeting (or manually re-trigger an old report) to confirm a routine session actually starts.

## Rotating credentials

If you click "Regenerate" on the routine's token, or rotate Read.ai's signing key, update the matching environment variable on your host and restart the relay. The old token/key stops working the moment it's regenerated.

## Notes

- Routines are in research preview, so the `anthropic-beta` header value (`experimental-cc-routine-2026-04-01`) may change over time. If requests start failing with a 400, that header is the first thing to check.
- The relay always returns 200 to Read.ai, even when forwarding to the routine fails, specifically to avoid endless retries. Check your logs (or wire up alerting) if `routine_fired` keeps coming back `false`.
- De-duplication of repeated `request_id`s is in-memory only and resets on restart. If you ever run more than one instance behind a load balancer, swap the in-memory `deque` for Redis or similar so duplicates are caught across instances too.
