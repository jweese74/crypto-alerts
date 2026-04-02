"""
IP Whitelist Middleware
=======================
Optional layer that restricts access to a configured list of IPs/CIDRs.

When the whitelist is empty (default), all IPs are allowed.
When configured, only matching IPs are allowed through.

Exempt paths: /health, /static/*  (so uptime monitors always work)

The whitelist is read from SystemSettings key ``security.ip_whitelist``
(comma-separated), checked on every request with a short in-memory cache
to avoid per-request DB hits.
"""
from __future__ import annotations

import ipaddress
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, HTMLResponse


# Paths that bypass IP filtering
_EXEMPT_PREFIXES = ("/health", "/static/")
_CACHE_TTL = 30  # seconds between whitelist reloads


def _client_ip(request: Request) -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def _ip_in_whitelist(ip_str: str, whitelist: list) -> bool:
    """Return True if *ip_str* matches any entry in *whitelist* (exact or CIDR)."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    for entry in whitelist:
        try:
            if "/" in entry:
                if addr in ipaddress.ip_network(entry, strict=False):
                    return True
            else:
                if addr == ipaddress.ip_address(entry):
                    return True
        except ValueError:
            continue
    return False


def _parse_whitelist(raw: str) -> list:
    """Parse a comma-separated IP/CIDR string into a clean list."""
    return [e.strip() for e in raw.split(",") if e.strip()]


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    """
    When ``security.ip_whitelist`` is non-empty in SystemSettings,
    all requests from IPs not in the list receive a 403.
    """

    def __init__(self, app, db_factory) -> None:
        super().__init__(app)
        self._db_factory = db_factory
        self._whitelist: list = []
        self._last_loaded: float = 0.0

    async def _load_whitelist(self) -> list:
        """Reload whitelist from DB at most every _CACHE_TTL seconds."""
        now = time.monotonic()
        if now - self._last_loaded < _CACHE_TTL:
            return self._whitelist
        try:
            async with self._db_factory() as db:
                from app.crud.system_settings import get_setting
                raw = await get_setting(db, "security.ip_whitelist", default="")
                self._whitelist = _parse_whitelist(raw)
                self._last_loaded = now
        except Exception:
            pass  # Preserve cached value on DB error
        return self._whitelist

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        # Always allow exempt paths
        for prefix in _EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        whitelist = await self._load_whitelist()
        if not whitelist:
            # Whitelist empty = no restriction
            return await call_next(request)

        ip = _client_ip(request)
        if _ip_in_whitelist(ip, whitelist):
            return await call_next(request)

        # Blocked
        return HTMLResponse(
            content=(
                "<h1>403 Forbidden</h1>"
                "<p>Your IP address is not permitted to access this system.</p>"
            ),
            status_code=403,
        )
