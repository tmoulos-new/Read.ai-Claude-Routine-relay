"""
Vercel entrypoint.

Vercel's zero-config Python/Flask detection looks for a top-level `app`
variable in one of a fixed set of filenames: app.py, index.py, server.py,
main.py, wsgi.py, or asgi.py. The actual implementation lives in relay.py
so that gunicorn relay:app keeps working unchanged for Render/Railway/VPS
deployments -- this file just re-exports it under a name Vercel expects.
"""

from relay import app  # noqa: F401
