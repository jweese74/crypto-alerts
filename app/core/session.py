"""
Session, flash message, and CSRF token helpers.

The app uses Starlette's signed-cookie SessionMiddleware.
All data lives in request.session (a dict backed by an itsdangerous cookie).
"""
import secrets
import time
from typing import Optional

from fastapi import Request

_USER_KEY = "uid"
_CSRF_KEY = "csrf"
_FLASH_KEY = "flash"
_CREATED_KEY = "sess_created"    # Unix timestamp — absolute timeout reference
_ACTIVITY_KEY = "sess_activity"  # Unix timestamp — idle timeout reference


# ── User identity ────────────────────────────────────────────────────────────

def get_session_user_id(request: Request) -> Optional[str]:
    return request.session.get(_USER_KEY)


def set_session_user(request: Request, user_id: str) -> None:
    now = int(time.time())
    # Rotate CSRF token on login to prevent session-fixation attacks
    request.session.clear()
    request.session[_USER_KEY] = user_id
    request.session[_CSRF_KEY] = secrets.token_hex(32)
    request.session[_CREATED_KEY] = now
    request.session[_ACTIVITY_KEY] = now


def touch_session(request: Request) -> None:
    """Update the last-activity timestamp (call on authenticated requests)."""
    request.session[_ACTIVITY_KEY] = int(time.time())


def is_session_expired(request: Request) -> bool:
    """
    Return True if the session has exceeded idle or absolute timeout.
    Reads limits from application config.
    """
    from app.core.config import get_settings
    cfg = get_settings()
    now = int(time.time())
    idle_limit = cfg.SESSION_IDLE_TIMEOUT_HOURS * 3600
    abs_limit = cfg.SESSION_ABSOLUTE_TIMEOUT_HOURS * 3600

    created = request.session.get(_CREATED_KEY)
    last_activity = request.session.get(_ACTIVITY_KEY)

    if created is not None and (now - created) > abs_limit:
        return True
    if last_activity is not None and (now - last_activity) > idle_limit:
        return True
    return False


def clear_session(request: Request) -> None:
    request.session.clear()


# ── CSRF ─────────────────────────────────────────────────────────────────────

def get_csrf_token(request: Request) -> str:
    """Return existing CSRF token, generating one if absent."""
    token = request.session.get(_CSRF_KEY)
    if not token:
        token = secrets.token_hex(32)
        request.session[_CSRF_KEY] = token
    return token


def validate_csrf(request: Request, submitted_token: str) -> bool:
    """Constant-time comparison of session token vs submitted form token."""
    session_token = request.session.get(_CSRF_KEY, "")
    if not session_token or not submitted_token:
        return False
    return secrets.compare_digest(session_token, submitted_token)


# ── Flash messages ────────────────────────────────────────────────────────────

def flash(request: Request, message: str, category: str = "info") -> None:
    """Queue a one-time display message (consumed on next render)."""
    messages: list = request.session.setdefault(_FLASH_KEY, [])
    messages.append({"message": message, "category": category})


def get_flashed_messages(request: Request) -> list[dict]:
    """Pop and return all queued flash messages."""
    return request.session.pop(_FLASH_KEY, [])
