import uuid
from datetime import datetime, time, timezone
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert_history import AlertHistory
from app.models.alert_rule import AlertCondition, AlertRule


async def get_active_rules(db: AsyncSession) -> list[AlertRule]:
    result = await db.execute(
        select(AlertRule).where(AlertRule.is_active == True)  # noqa: E712
    )
    return list(result.scalars().all())


async def get_rules_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[AlertRule]:
    result = await db.execute(
        select(AlertRule)
        .where(AlertRule.user_id == user_id)
        .order_by(AlertRule.created_at.desc())
    )
    return list(result.scalars().all())


async def get_all_rules(db: AsyncSession) -> list[AlertRule]:
    """Admin: all rules across all users, newest first."""
    result = await db.execute(
        select(AlertRule).order_by(AlertRule.created_at.desc())
    )
    return list(result.scalars().all())


async def get_rule_by_id(db: AsyncSession, rule_id: uuid.UUID) -> Optional[AlertRule]:
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    return result.scalar_one_or_none()


async def create_rule(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    trading_pair: str,
    condition: AlertCondition,
    threshold: float,
    label: Optional[str] = None,
    custom_message: Optional[str] = None,
    cooldown_minutes: int = 60,
    send_once: bool = False,
    is_active: bool = True,
    time_filter_enabled: bool = False,
    active_hours_start: Optional[time] = None,
    active_hours_end: Optional[time] = None,
    active_timezone: str = "UTC",
    critical_override: bool = False,
) -> AlertRule:
    rule = AlertRule(
        user_id=user_id,
        trading_pair=trading_pair,
        condition=condition,
        threshold=threshold,
        label=label or None,
        custom_message=custom_message or None,
        cooldown_minutes=cooldown_minutes,
        send_once=send_once,
        is_active=is_active,
        time_filter_enabled=time_filter_enabled,
        active_hours_start=active_hours_start,
        active_hours_end=active_hours_end,
        active_timezone=active_timezone,
        critical_override=critical_override,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def update_rule(
    db: AsyncSession,
    rule: AlertRule,
    *,
    trading_pair: Optional[str] = None,
    condition: Optional[AlertCondition] = None,
    threshold: Optional[float] = None,
    label: Optional[str] = None,
    custom_message: Optional[str] = None,
    cooldown_minutes: Optional[int] = None,
    send_once: Optional[bool] = None,
    is_active: Optional[bool] = None,
    time_filter_enabled: Optional[bool] = None,
    active_hours_start: Optional[time] = None,
    active_hours_end: Optional[time] = None,
    active_timezone: Optional[str] = None,
    critical_override: Optional[bool] = None,
    _clear_hours: bool = False,
) -> AlertRule:
    if trading_pair is not None:
        rule.trading_pair = trading_pair
    if condition is not None:
        rule.condition = condition
    if threshold is not None:
        rule.threshold = threshold
    if label is not None:
        rule.label = label or None
    if custom_message is not None:
        rule.custom_message = custom_message or None
    if cooldown_minutes is not None:
        rule.cooldown_minutes = cooldown_minutes
    if send_once is not None:
        rule.send_once = send_once
    if is_active is not None:
        rule.is_active = is_active
    if time_filter_enabled is not None:
        rule.time_filter_enabled = time_filter_enabled
    if active_timezone is not None:
        rule.active_timezone = active_timezone
    if critical_override is not None:
        rule.critical_override = critical_override
    # Hours must be set explicitly; None means "keep current" unless _clear_hours=True
    if active_hours_start is not None:
        rule.active_hours_start = active_hours_start
    elif _clear_hours:
        rule.active_hours_start = None
    if active_hours_end is not None:
        rule.active_hours_end = active_hours_end
    elif _clear_hours:
        rule.active_hours_end = None
    rule.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(rule)
    return rule


async def toggle_rule(db: AsyncSession, rule: AlertRule) -> AlertRule:
    rule.is_active = not rule.is_active
    rule.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(rule)
    return rule


async def delete_rule(db: AsyncSession, rule: AlertRule) -> None:
    await db.delete(rule)
    await db.commit()


async def clone_rule(db: AsyncSession, rule: AlertRule) -> AlertRule:
    clone = AlertRule(
        user_id=rule.user_id,
        trading_pair=rule.trading_pair,
        condition=rule.condition,
        threshold=rule.threshold,
        label=f"{rule.label or rule.trading_pair} (copy)",
        custom_message=rule.custom_message,
        cooldown_minutes=rule.cooldown_minutes,
        send_once=rule.send_once,
        is_active=False,  # disabled by default so user reviews before activating
    )
    db.add(clone)
    await db.commit()
    await db.refresh(clone)
    return clone


async def update_rule_state(
    db: AsyncSession,
    rule: AlertRule,
    *,
    last_state: str,
    last_triggered_at: Optional[datetime] = None,
    is_active: Optional[bool] = None,
) -> AlertRule:
    rule.last_state = last_state
    if last_triggered_at is not None:
        rule.last_triggered_at = last_triggered_at
    if is_active is not None:
        rule.is_active = is_active
    await db.commit()
    await db.refresh(rule)
    return rule


async def create_history_record(
    db: AsyncSession,
    *,
    rule: AlertRule,
    triggered_price: float,
    message: str,
    severity: str = "normal",
) -> AlertHistory:
    record = AlertHistory(
        user_id=rule.user_id,
        rule_id=rule.id,
        trading_pair=rule.trading_pair,
        triggered_price=triggered_price,
        threshold_value=rule.threshold,
        message=message,
        severity=severity,
        delivery_channel="log",
        delivered=True,
        triggered_at=datetime.now(timezone.utc),
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_history_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 100,
) -> list[AlertHistory]:
    result = await db.execute(
        select(AlertHistory)
        .where(AlertHistory.user_id == user_id)
        .order_by(AlertHistory.triggered_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_all_history(db: AsyncSession, limit: int = 200) -> list[AlertHistory]:
    """Admin: all history across all users."""
    result = await db.execute(
        select(AlertHistory)
        .order_by(AlertHistory.triggered_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def count_rules(db: AsyncSession, user_id: Optional[uuid.UUID] = None) -> int:
    q = select(func.count(AlertRule.id))
    if user_id:
        q = q.where(AlertRule.user_id == user_id)
    result = await db.execute(q)
    return result.scalar_one()


async def count_active_rules(db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count(AlertRule.id)).where(AlertRule.is_active == True)  # noqa: E712
    )
    return result.scalar_one()


async def count_history(db: AsyncSession, user_id: Optional[uuid.UUID] = None) -> int:
    q = select(func.count(AlertHistory.id))
    if user_id:
        q = q.where(AlertHistory.user_id == user_id)
    result = await db.execute(q)
    return result.scalar_one()


async def get_recent_history(
    db: AsyncSession,
    user_id: Optional[uuid.UUID] = None,
    limit: int = 10,
) -> list[AlertHistory]:
    q = select(AlertHistory).order_by(AlertHistory.triggered_at.desc()).limit(limit)
    if user_id:
        q = q.where(AlertHistory.user_id == user_id)
    result = await db.execute(q)
    return list(result.scalars().all())


async def cleanup_old_alert_history(
    db: AsyncSession,
    retention_days: int = 365,
) -> int:
    """Delete AlertHistory rows older than retention_days. Returns count deleted.

    Safety: always preserves data from the last 7 days regardless of setting,
    and refuses to run if retention_days < 7.
    """
    from datetime import timedelta
    retention_days = max(retention_days, 7)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = await db.execute(
        delete(AlertHistory).where(AlertHistory.triggered_at < cutoff)
    )
    await db.commit()
    return result.rowcount
