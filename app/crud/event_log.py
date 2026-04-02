"""
CRUD helpers for SystemEvent.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_event import SystemEvent

# ── Known event types ─────────────────────────────────────────────────────────

ALERT_TRIGGERED          = "alert_triggered"
ALERT_SUPPRESSED_COOLDOWN    = "alert_suppressed_cooldown"
ALERT_SUPPRESSED_TIME_FILTER = "alert_suppressed_time_filter"
MARKET_STATE_CHANGED     = "market_state_changed"
RULE_CREATED             = "rule_created"
RULE_UPDATED             = "rule_updated"
RULE_DELETED             = "rule_deleted"
RULE_ENABLED             = "rule_enabled"
RULE_DISABLED            = "rule_disabled"
SYSTEM_STARTUP           = "system_startup"
USER_LOGIN               = "user_login"
RETENTION_RUN            = "retention_run"
# Security events
AUTH_FAILURE             = "auth_failure"
ACCOUNT_LOCKED           = "account_locked"
ACCOUNT_UNLOCKED         = "account_unlocked"
SUSPICIOUS_ACTIVITY      = "suspicious_activity"

# Event types visible to all users (not user-specific)
SYSTEM_EVENT_TYPES = {MARKET_STATE_CHANGED, SYSTEM_STARTUP, RETENTION_RUN}
# Security events visible to admins only (not in system set for regular users)
SECURITY_EVENT_TYPES = {AUTH_FAILURE, ACCOUNT_LOCKED, ACCOUNT_UNLOCKED, SUSPICIOUS_ACTIVITY}

# Severity values
INFO     = "info"
WARNING  = "warning"
CRITICAL = "critical"


async def log_event(
    db: AsyncSession,
    *,
    event_type: str,
    description: str,
    asset_symbol: Optional[str] = None,
    severity: str = INFO,
    user_id: Optional[str] = None,
    extra: Optional[dict] = None,
) -> SystemEvent:
    event = SystemEvent(
        event_type=event_type,
        description=description,
        asset_symbol=asset_symbol,
        severity=severity,
        user_id=user_id,
        extra=json.dumps(extra) if extra else None,
    )
    db.add(event)
    await db.commit()
    return event


async def get_events(
    db: AsyncSession,
    *,
    user_id: Optional[str] = None,
    is_admin: bool = False,
    asset_symbol: Optional[str] = None,
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[SystemEvent]:
    """
    Fetch events with filters.

    Access control:
    - Admins see all events.
    - Regular users see: events where user_id matches theirs,
      PLUS system-wide event types (market_state, system_startup, etc.)
    """
    q = select(SystemEvent).order_by(SystemEvent.occurred_at.desc())

    if not is_admin and user_id:
        from sqlalchemy import or_
        q = q.where(
            or_(
                SystemEvent.user_id == user_id,
                SystemEvent.event_type.in_(SYSTEM_EVENT_TYPES),
            )
        )

    if asset_symbol:
        q = q.where(SystemEvent.asset_symbol == asset_symbol)

    if event_type:
        q = q.where(SystemEvent.event_type == event_type)

    if severity:
        q = q.where(SystemEvent.severity == severity)

    if since:
        q = q.where(SystemEvent.occurred_at >= since)

    if until:
        q = q.where(SystemEvent.occurred_at <= until)

    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    return list(result.scalars().all())


async def count_events(
    db: AsyncSession,
    *,
    user_id: Optional[str] = None,
    is_admin: bool = False,
    asset_symbol: Optional[str] = None,
    event_type: Optional[str] = None,
    severity: Optional[str] = None,
    since: Optional[datetime] = None,
) -> int:
    from sqlalchemy import or_
    q = select(func.count(SystemEvent.id))

    if not is_admin and user_id:
        q = q.where(
            or_(
                SystemEvent.user_id == user_id,
                SystemEvent.event_type.in_(SYSTEM_EVENT_TYPES),
            )
        )

    if asset_symbol:
        q = q.where(SystemEvent.asset_symbol == asset_symbol)
    if event_type:
        q = q.where(SystemEvent.event_type == event_type)
    if severity:
        q = q.where(SystemEvent.severity == severity)
    if since:
        q = q.where(SystemEvent.occurred_at >= since)

    result = await db.execute(q)
    return result.scalar_one() or 0


async def cleanup_old_events(
    db: AsyncSession,
    retention_days: int = 90,
) -> int:
    retention_days = max(retention_days, 7)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = await db.execute(
        delete(SystemEvent).where(SystemEvent.occurred_at < cutoff)
    )
    await db.commit()
    return result.rowcount


async def get_distinct_assets(db: AsyncSession) -> list[str]:
    """Return sorted list of asset symbols that have events."""
    result = await db.execute(
        select(SystemEvent.asset_symbol)
        .where(SystemEvent.asset_symbol.isnot(None))
        .distinct()
        .order_by(SystemEvent.asset_symbol)
    )
    return [row[0] for row in result.fetchall()]
