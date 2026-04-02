"""
EventLog Service
================
Thin async wrapper that writes human-readable SystemEvent rows.

All methods are fire-and-forget from the caller's perspective:
failures are caught and logged to the standard logger so event
recording never breaks the main alert/market-state flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.core.logging import logger


class EventLogService:
    """
    Provides named methods for each event type so callers don't
    need to know about CRUD internals.
    """

    # ── Alert events ──────────────────────────────────────────────────────

    async def alert_triggered(
        self,
        db,
        *,
        trading_pair: str,
        condition: str,          # "above" | "below"
        threshold: float,
        triggered_price: float,
        severity: str = "info",
        user_id: Optional[str] = None,
        rule_label: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        direction = "above" if condition == "above" else "below"
        arrow     = "↑" if direction == "above" else "↓"
        asset     = trading_pair.split("/")[0]

        desc = (
            f"{asset} {arrow} crossed {direction} "
            f"${threshold:,.2f} USD at ${triggered_price:,.2f}"
        )
        if rule_label:
            desc += f' (rule: "{rule_label}")'

        ev_severity = {
            "critical": "critical",
            "elevated": "warning",
        }.get(severity.lower(), "warning")

        await self._write(
            db,
            event_type="alert_triggered",
            description=desc,
            asset_symbol=trading_pair,
            severity=ev_severity,
            user_id=user_id,
            extra=extra or {
                "condition": condition,
                "threshold": threshold,
                "price": triggered_price,
                "severity": severity,
            },
        )

    async def alert_suppressed_cooldown(
        self,
        db,
        *,
        trading_pair: str,
        threshold: float,
        condition: str,
        elapsed_minutes: float,
        cooldown_minutes: int,
        user_id: Optional[str] = None,
    ) -> None:
        asset = trading_pair.split("/")[0]
        direction = "above" if condition == "above" else "below"
        desc = (
            f"{asset} {direction} ${threshold:,.2f} alert suppressed — "
            f"cooldown active ({elapsed_minutes:.0f}/{cooldown_minutes} min)"
        )
        await self._write(
            db,
            event_type="alert_suppressed_cooldown",
            description=desc,
            asset_symbol=trading_pair,
            severity="info",
            user_id=user_id,
        )

    async def alert_suppressed_time_filter(
        self,
        db,
        *,
        trading_pair: str,
        threshold: float,
        condition: str,
        window_start: str,
        window_end: str,
        timezone_name: str,
        user_id: Optional[str] = None,
    ) -> None:
        asset = trading_pair.split("/")[0]
        direction = "above" if condition == "above" else "below"
        desc = (
            f"{asset} {direction} ${threshold:,.2f} alert suppressed — "
            f"outside active hours [{window_start}–{window_end} {timezone_name}]"
        )
        await self._write(
            db,
            event_type="alert_suppressed_time_filter",
            description=desc,
            asset_symbol=trading_pair,
            severity="info",
            user_id=user_id,
        )

    # ── Market state events ───────────────────────────────────────────────

    async def market_state_changed(
        self,
        db,
        *,
        previous_state: str,
        new_state: str,
        score: int,
        reasons: list[str],
    ) -> None:
        _icons = {"calm": "🟢", "warning": "🟡", "risk": "🟠", "event": "🔴"}
        icon = _icons.get(new_state.lower(), "⚪")
        desc = (
            f"{icon} Market state changed: "
            f"{previous_state.upper()} → {new_state.upper()} "
            f"(score {score})"
        )
        if reasons:
            desc += " — " + "; ".join(reasons[:3])

        ev_sev = {"warning": "warning", "risk": "warning", "event": "critical"}.get(
            new_state.lower(), "info"
        )
        await self._write(
            db,
            event_type="market_state_changed",
            description=desc,
            asset_symbol=None,
            severity=ev_sev,
            user_id=None,
            extra={"previous": previous_state, "new": new_state, "score": score},
        )

    # ── Rule lifecycle events ─────────────────────────────────────────────

    async def rule_created(
        self, db, *, trading_pair: str, condition: str, threshold: float,
        label: Optional[str] = None, user_id: str,
    ) -> None:
        asset = trading_pair.split("/")[0]
        direction = "above" if condition == "above" else "below"
        name = f' "{label}"' if label else ""
        await self._write(
            db,
            event_type="rule_created",
            description=f'Alert rule created{name}: {asset} {direction} ${threshold:,.2f}',
            asset_symbol=trading_pair,
            severity="info",
            user_id=user_id,
        )

    async def rule_deleted(
        self, db, *, trading_pair: str, condition: str = "", threshold: float = 0.0,
        label: Optional[str] = None, user_id: str,
    ) -> None:
        asset = trading_pair.split("/")[0]
        direction = "above" if condition == "above" else "below"
        name = f' "{label}"' if label else ""
        await self._write(
            db,
            event_type="rule_deleted",
            description=f'Alert rule deleted{name}: {asset} {direction} ${threshold:,.2f}',
            asset_symbol=trading_pair,
            severity="info",
            user_id=user_id,
        )

    async def rule_toggled(
        self, db, *, trading_pair: str, label: Optional[str] = None,
        enabled: bool, user_id: str,
    ) -> None:
        name = f' "{label}"' if label else f" ({trading_pair})"
        state = "enabled" if enabled else "disabled"
        event_type = "rule_enabled" if enabled else "rule_disabled"
        await self._write(
            db,
            event_type=event_type,
            description=f"Alert rule{name} {state}",
            asset_symbol=trading_pair,
            severity="info",
            user_id=user_id,
        )

    # ── System events ─────────────────────────────────────────────────────

    async def system_startup(self, db) -> None:
        from app.core.config import get_settings
        cfg = get_settings()
        await self._write(
            db,
            event_type="system_startup",
            description=f"System started (v{cfg.APP_VERSION})",
            severity="info",
        )

    async def user_login(self, db, *, username: str, user_id: str) -> None:
        await self._write(
            db,
            event_type="user_login",
            description=f'User "{username}" logged in',
            severity="info",
            user_id=user_id,
        )

    async def retention_run(
        self, db, *, price_deleted: int, alert_deleted: int
    ) -> None:
        desc = (
            f"Retention cleanup: {price_deleted} price rows, "
            f"{alert_deleted} alert history rows removed"
        )
        await self._write(
            db,
            event_type="retention_run",
            description=desc,
            severity="info",
        )

    # ── Security events ───────────────────────────────────────────────────

    async def auth_failure(
        self,
        db,
        *,
        login: str,
        ip: Optional[str] = None,
        reason: str = "invalid credentials",
    ) -> None:
        ip_part = f" from {ip}" if ip else ""
        await self._write(
            db,
            event_type="auth_failure",
            description=f'Failed login attempt for "{login}"{ip_part} — {reason}',
            severity="warning",
            extra={"login": login, "ip": ip, "reason": reason},
        )

    async def account_locked(
        self,
        db,
        *,
        identifier: str,
        lockout_minutes: int,
        ip: Optional[str] = None,
    ) -> None:
        ip_part = f" (source IP: {ip})" if ip else ""
        await self._write(
            db,
            event_type="account_locked",
            description=(
                f'Login locked for "{identifier}" after repeated failures{ip_part} — '
                f"locked for {lockout_minutes} min"
            ),
            severity="warning",
            extra={"identifier": identifier, "lockout_minutes": lockout_minutes, "ip": ip},
        )

    async def account_unlocked(
        self,
        db,
        *,
        identifier: str,
        admin_user: str,
    ) -> None:
        await self._write(
            db,
            event_type="account_unlocked",
            description=f'Lockout manually cleared for "{identifier}" by admin "{admin_user}"',
            severity="info",
            extra={"identifier": identifier, "admin": admin_user},
        )

    async def suspicious_activity(
        self,
        db,
        *,
        description: str,
        ip: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        ip_part = f" [IP: {ip}]" if ip else ""
        await self._write(
            db,
            event_type="suspicious_activity",
            description=f"{description}{ip_part}",
            severity="warning",
            extra=extra or ({"ip": ip} if ip else None),
        )

    # ── Internal writer ───────────────────────────────────────────────────

    async def _write(
        self,
        db,
        *,
        event_type: str,
        description: str,
        asset_symbol: Optional[str] = None,
        severity: str = "info",
        user_id: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        try:
            from app.crud.event_log import log_event
            await log_event(
                db,
                event_type=event_type,
                description=description,
                asset_symbol=asset_symbol,
                severity=severity,
                user_id=str(user_id) if user_id else None,
                extra=extra,
            )
        except Exception as exc:
            # Never let event logging break the main flow
            logger.error(f"[event_log] Failed to write event '{event_type}': {exc}")


event_log = EventLogService()
