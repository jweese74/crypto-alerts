"""Tests for security hardening: rate limiter, session timeout, IP filter."""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── LoginAttemptTracker ────────────────────────────────────────────────────────

from app.services.rate_limiter import LoginAttemptTracker


def make_tracker(**kwargs) -> LoginAttemptTracker:
    defaults = {"max_attempts": 3, "window_minutes": 10, "lockout_minutes": 5}
    defaults.update(kwargs)
    return LoginAttemptTracker(**defaults)


class TestLoginAttemptTracker:
    def test_not_locked_initially(self):
        t = make_tracker()
        assert not t.is_locked("1.2.3.4")

    def test_failure_count_zero_initially(self):
        t = make_tracker()
        assert t.failure_count("user@example.com") == 0

    def test_single_failure_does_not_lock(self):
        t = make_tracker()
        triggered = t.record_failure("1.2.3.4")
        assert not triggered
        assert not t.is_locked("1.2.3.4")
        assert t.failure_count("1.2.3.4") == 1

    def test_lockout_triggered_at_max_attempts(self):
        t = make_tracker(max_attempts=3)
        t.record_failure("ip")
        t.record_failure("ip")
        triggered = t.record_failure("ip")
        assert triggered
        assert t.is_locked("ip")

    def test_seconds_remaining_after_lockout(self):
        t = make_tracker(max_attempts=2, lockout_minutes=5)
        t.record_failure("ip")
        t.record_failure("ip")
        secs = t.seconds_remaining("ip")
        assert 290 <= secs <= 300  # ≈ 5 minutes

    def test_seconds_remaining_not_locked(self):
        t = make_tracker()
        assert t.seconds_remaining("ip") == 0

    def test_success_clears_failures(self):
        t = make_tracker(max_attempts=5)
        t.record_failure("ip")
        t.record_failure("ip")
        t.record_success("ip")
        assert t.failure_count("ip") == 0
        assert not t.is_locked("ip")

    def test_reset_clears_lockout(self):
        t = make_tracker(max_attempts=2)
        t.record_failure("ip")
        t.record_failure("ip")
        assert t.is_locked("ip")
        t.reset("ip")
        assert not t.is_locked("ip")

    def test_reset_nonexistent_key_is_safe(self):
        t = make_tracker()
        t.reset("nonexistent")  # should not raise

    def test_all_locked_keys_returns_locked(self):
        t = make_tracker(max_attempts=2)
        t.record_failure("ip1")
        t.record_failure("ip1")
        t.record_failure("ip2")
        locked = t.all_locked_keys()
        assert "ip1" in locked
        assert "ip2" not in locked

    def test_stats_returns_dict(self):
        t = make_tracker(max_attempts=2)
        t.record_failure("ip1")
        t.record_failure("ip1")
        stats = t.stats()
        assert "tracked_keys" in stats
        assert "locked_keys" in stats
        assert stats["locked_keys"] == 1

    def test_sliding_window_prunes_old_failures(self):
        t = make_tracker(max_attempts=3, window_minutes=1)
        # Inject an old failure directly
        from datetime import datetime, timedelta, timezone
        t._states["ip"] = t._states.get("ip") or __import__("app.services.rate_limiter", fromlist=["_KeyState"])._KeyState()
        from app.services.rate_limiter import _KeyState
        from collections import deque
        old_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        state = _KeyState(failure_times=deque([old_time]))
        t._states["old_ip"] = state
        # Prune should remove it
        t._prune(state)
        assert len(state.failure_times) == 0

    def test_different_keys_tracked_independently(self):
        t = make_tracker(max_attempts=2)
        t.record_failure("ip1")
        t.record_failure("ip1")
        assert t.is_locked("ip1")
        assert not t.is_locked("ip2")

    def test_make_tracker_uses_settings(self):
        """make_tracker() should create a tracker from app config."""
        from app.services.rate_limiter import make_tracker as _make
        tracker = _make()
        assert isinstance(tracker, LoginAttemptTracker)
        assert tracker.max_attempts >= 1

    def test_module_singleton_exists(self):
        from app.services.rate_limiter import login_tracker
        assert isinstance(login_tracker, LoginAttemptTracker)


# ── Session timeout helpers ────────────────────────────────────────────────────

from app.core.session import (
    set_session_user,
    is_session_expired,
    touch_session,
    clear_session,
)


def _mock_request(session_data: dict) -> MagicMock:
    req = MagicMock()
    req.session = session_data
    return req


class TestSessionTimeout:
    def test_fresh_session_not_expired(self):
        req = _mock_request({})
        set_session_user(req, "user-id")
        assert not is_session_expired(req)

    def test_session_expired_after_absolute_timeout(self):
        req = _mock_request({})
        set_session_user(req, "user-id")
        # Backdate creation time by 200 hours (> 168h default)
        req.session["sess_created"] = int(time.time()) - (200 * 3600)
        assert is_session_expired(req)

    def test_session_expired_after_idle_timeout(self):
        req = _mock_request({})
        set_session_user(req, "user-id")
        # Backdate last activity by 9 hours (> 8h default)
        req.session["sess_activity"] = int(time.time()) - (9 * 3600)
        assert is_session_expired(req)

    def test_session_not_expired_within_idle(self):
        req = _mock_request({})
        set_session_user(req, "user-id")
        # Only 30 min idle
        req.session["sess_activity"] = int(time.time()) - 1800
        assert not is_session_expired(req)

    def test_touch_session_updates_activity(self):
        req = _mock_request({})
        set_session_user(req, "user-id")
        old_activity = req.session["sess_activity"]
        time.sleep(0.01)
        touch_session(req)
        # Activity should have been updated (same second is fine in fast tests)
        assert req.session["sess_activity"] >= old_activity

    def test_session_without_timestamps_not_expired(self):
        """Old sessions (missing keys) should not be incorrectly expired."""
        req = _mock_request({"uid": "user-id"})
        assert not is_session_expired(req)

    def test_set_session_stores_timestamps(self):
        req = _mock_request({})
        set_session_user(req, "user-id")
        assert "sess_created" in req.session
        assert "sess_activity" in req.session

    def test_set_session_rotates_csrf(self):
        req = _mock_request({"csrf": "old-token"})
        set_session_user(req, "user-id")
        assert req.session.get("csrf") != "old-token"


# ── IP whitelist helpers ───────────────────────────────────────────────────────

from app.core.ip_filter import _ip_in_whitelist, _parse_whitelist


class TestIPWhitelist:
    def test_exact_ip_match(self):
        wl = _parse_whitelist("192.168.1.1")
        assert _ip_in_whitelist("192.168.1.1", wl)

    def test_exact_ip_no_match(self):
        wl = _parse_whitelist("192.168.1.1")
        assert not _ip_in_whitelist("192.168.1.2", wl)

    def test_cidr_match(self):
        wl = _parse_whitelist("192.168.1.0/24")
        assert _ip_in_whitelist("192.168.1.100", wl)

    def test_cidr_no_match(self):
        wl = _parse_whitelist("192.168.1.0/24")
        assert not _ip_in_whitelist("192.168.2.1", wl)

    def test_multiple_entries(self):
        wl = _parse_whitelist("10.0.0.1, 192.168.0.0/16")
        assert _ip_in_whitelist("10.0.0.1", wl)
        assert _ip_in_whitelist("192.168.5.5", wl)
        assert not _ip_in_whitelist("172.16.0.1", wl)

    def test_empty_whitelist_returns_false(self):
        assert not _ip_in_whitelist("1.2.3.4", [])

    def test_invalid_ip_returns_false(self):
        wl = _parse_whitelist("192.168.1.1")
        assert not _ip_in_whitelist("not-an-ip", wl)

    def test_invalid_whitelist_entry_skipped(self):
        wl = _parse_whitelist("192.168.1.1, bad-entry, 10.0.0.1")
        assert _ip_in_whitelist("10.0.0.1", wl)

    def test_parse_whitelist_strips_whitespace(self):
        entries = _parse_whitelist("  10.0.0.1 ,  192.168.1.0/24  ")
        assert "10.0.0.1" in entries
        assert "192.168.1.0/24" in entries

    def test_parse_empty_string(self):
        assert _parse_whitelist("") == []

    def test_parse_whitespace_only(self):
        assert _parse_whitelist("   ,  , ") == []

    def test_ipv6_exact_match(self):
        wl = _parse_whitelist("::1")
        assert _ip_in_whitelist("::1", wl)

    def test_ipv6_cidr(self):
        wl = _parse_whitelist("fe80::/10")
        assert _ip_in_whitelist("fe80::1", wl)


# ── Security event log service ────────────────────────────────────────────────

from app.services.event_log import EventLogService


@pytest.mark.asyncio
async def test_event_log_auth_failure():
    svc = EventLogService()
    db = AsyncMock()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.auth_failure(db, login="baduser", ip="1.2.3.4", reason="invalid credentials")
    kwargs = mock_log.call_args[1]
    assert kwargs["event_type"] == "auth_failure"
    assert "baduser" in kwargs["description"]
    assert "1.2.3.4" in kwargs["description"]
    assert kwargs["severity"] == "warning"


@pytest.mark.asyncio
async def test_event_log_auth_failure_no_ip():
    svc = EventLogService()
    db = AsyncMock()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.auth_failure(db, login="baduser")
    kwargs = mock_log.call_args[1]
    assert "baduser" in kwargs["description"]


@pytest.mark.asyncio
async def test_event_log_account_locked():
    svc = EventLogService()
    db = AsyncMock()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.account_locked(db, identifier="hacker@evil.com", lockout_minutes=30, ip="5.5.5.5")
    kwargs = mock_log.call_args[1]
    assert kwargs["event_type"] == "account_locked"
    assert "30 min" in kwargs["description"]
    assert kwargs["severity"] == "warning"


@pytest.mark.asyncio
async def test_event_log_account_unlocked():
    svc = EventLogService()
    db = AsyncMock()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.account_unlocked(db, identifier="user@example.com", admin_user="admin")
    kwargs = mock_log.call_args[1]
    assert kwargs["event_type"] == "account_unlocked"
    assert "admin" in kwargs["description"]


@pytest.mark.asyncio
async def test_event_log_suspicious_activity():
    svc = EventLogService()
    db = AsyncMock()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.suspicious_activity(
            db, description="Login attempt while locked", ip="9.9.9.9"
        )
    kwargs = mock_log.call_args[1]
    assert kwargs["event_type"] == "suspicious_activity"
    assert "9.9.9.9" in kwargs["description"]
    assert kwargs["severity"] == "warning"


# ── Event log security constants ──────────────────────────────────────────────

def test_security_event_type_constants():
    from app.crud.event_log import (
        AUTH_FAILURE, ACCOUNT_LOCKED, ACCOUNT_UNLOCKED,
        SUSPICIOUS_ACTIVITY, SECURITY_EVENT_TYPES,
    )
    assert AUTH_FAILURE == "auth_failure"
    assert ACCOUNT_LOCKED == "account_locked"
    assert SUSPICIOUS_ACTIVITY == "suspicious_activity"
    assert AUTH_FAILURE in SECURITY_EVENT_TYPES
    assert ACCOUNT_LOCKED in SECURITY_EVENT_TYPES
