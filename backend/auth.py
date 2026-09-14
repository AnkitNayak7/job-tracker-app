"""Two independent auth mechanisms:

1. Session-cookie password gate for the human-facing dashboard
   (single shared password, SITE_PASSWORD env var).
2. API-key auth for the internal ingest endpoints that the daily
   scheduled pipeline (a Claude session, not a browser) calls
   (INTERNAL_API_KEY env var). Accepted either as an `X-API-Key` header
   (normal HTTP clients) or an `api_key` query parameter (needed because
   the scheduled pipeline session may only have a GET-only,
   no-custom-headers fetch tool available to it - see
   DAILY_PIPELINE_RUNBOOK.md for why).
"""
import os

from itsdangerous import BadSignature, URLSafeTimedSerializer
from fastapi import Cookie, Header, HTTPException, Query, status

SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "")

COOKIE_NAME = "jt_session"
_serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="jt-session")

# 30 days
MAX_AGE_SECONDS = 30 * 24 * 60 * 60


def make_session_token() -> str:
    return _serializer.dumps({"ok": True})


def verify_session_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        data = _serializer.loads(token, max_age=MAX_AGE_SECONDS)
        return bool(data.get("ok"))
    except BadSignature:
        return False
    except Exception:
        return False


async def require_session(jt_session: str | None = Cookie(default=None)):
    if not SITE_PASSWORD:
        # No password configured (e.g. local dev) - allow through.
        return True
    if not verify_session_token(jt_session):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return True


async def require_api_key(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
):
    if not INTERNAL_API_KEY:
        raise HTTPException(status_code=500, detail="Server missing INTERNAL_API_KEY configuration")
    supplied = x_api_key or api_key
    if supplied != INTERNAL_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return True
