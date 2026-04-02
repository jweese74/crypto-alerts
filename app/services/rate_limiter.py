"""
LoginAttemptTracker
===================
In-memory, per-key (IP address or username) brute-force protection.

Tracks sliding-window failure counts and enforces a lockout period after
too many consecutive failures. Resets on a successful login.

Thread-safe via asyncio.Lock (suitable for single-process ASGI deployment).
No external dependencies — no Redis required.

Configuration is read from app settings but can be overridden per-instance
for testing.
"""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


@dataclass
class _KeyState:
    # Ring-buffer of recent failure timestamps (within window)
    failure_times: deque = field(default_factory=deque)
    # When the lockout expires (None = not locked)
    locked_until: Optional[datetime] = None


class LoginAttemptTracker:
    """
    Usage::

        tracker = LoginAttemptTracker(max_attempts=5, window_minutes=15, lockout_minutes=30)

        # Check before attempting auth
        if tracker.is_locked(ip):
            raise TooManyRequests(tracker.seconds_remaining(ip))

        # After auth attempt
        if success:
            tracker.record_success(identifier)
        else:
            tracker.record_failure(identifier)
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_minutes: int = 15,
        lockout_minutes: int = 30,
    ) -> None:
        self.max_attempts = max_attempts
        self.window = timedelta(minutes=window_minutes)
        self.lockout = timedelta(minutes=lockout_minutes)
        self._states: dict[str, _KeyState] = {}
        self._lock = asyncio.Lock()

    # ── Public API ─────────────────────────────────────────────────────────

    def is_locked(self, key: str) -> bool:
        """Return True if *key* is currently locked out."""
        state = self._states.get(key)
        if not state or not state.locked_until:
            return False
        if datetime.now(timezone.utc) < state.locked_until:
            return True
        # Lockout expired — clean up
        state.locked_until = None
        state.failure_times.clear()
        return False

    def seconds_remaining(self, key: str) -> int:
        """Return seconds until lockout expires (0 if not locked)."""
        state = self._states.get(key)
        if not state or not state.locked_until:
            return 0
        remaining = (state.locked_until - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(remaining))

    def failure_count(self, key: str) -> int:
        """Return number of recent failures within the sliding window."""
        state = self._states.get(key)
        if not state:
            return 0
        self._prune(state)
        return len(state.failure_times)

    def record_failure(self, key: str) -> bool:
        """
        Record a failed attempt. Returns True if this failure triggered a lockout.
        """
        if key not in self._states:
            self._states[key] = _KeyState()
        state = self._states[key]
        self._prune(state)
        state.failure_times.append(datetime.now(timezone.utc))

        if len(state.failure_times) >= self.max_attempts:
            state.locked_until = datetime.now(timezone.utc) + self.lockout
            state.failure_times.clear()
            return True
        return False

    def record_success(self, key: str) -> None:
        """Clear failure history for *key* on successful authentication."""
        if key in self._states:
            self._states[key].failure_times.clear()
            self._states[key].locked_until = None

    def reset(self, key: str) -> None:
        """Manually reset lockout for *key* (admin unlock)."""
        self._states.pop(key, None)

    # ── Internal ───────────────────────────────────────────────────────────

    def _prune(self, state: _KeyState) -> None:
        """Remove failure timestamps that have fallen outside the window."""
        cutoff = datetime.now(timezone.utc) - self.window
        while state.failure_times and state.failure_times[0] < cutoff:
            state.failure_times.popleft()

    def all_locked_keys(self) -> list[str]:
        """Return list of currently locked keys (for admin UI)."""
        return [k for k, s in self._states.items() if s.locked_until and datetime.now(timezone.utc) < s.locked_until]

    def stats(self) -> dict:
        """Summary for admin/monitoring."""
        now = datetime.now(timezone.utc)
        locked = [
            {
                "key": k,
                "seconds_remaining": max(0, int((s.locked_until - now).total_seconds())),
            }
            for k, s in self._states.items()
            if s.locked_until and now < s.locked_until
        ]
        return {
            "tracked_keys": len(self._states),
            "locked_keys": len(locked),
            "locked": locked,
        }


def make_tracker() -> LoginAttemptTracker:
    """Create a tracker using application settings."""
    from app.core.config import get_settings
    cfg = get_settings()
    return LoginAttemptTracker(
        max_attempts=cfg.LOGIN_MAX_ATTEMPTS,
        window_minutes=cfg.LOGIN_WINDOW_MINUTES,
        lockout_minutes=cfg.LOGIN_LOCKOUT_MINUTES,
    )


# Module-level singleton — shared across all requests in the same process
login_tracker: LoginAttemptTracker = make_tracker()
