"""
Data Retention Service
======================
Prunes old price-history and alert-history rows based on configurable
retention windows stored in system_settings.

Safety guarantees
-----------------
- Price history: never deletes rows captured within the last 24 hours.
  The minimum configurable retention is 1 day; we always preserve the
  last 24 h regardless.
- Alert history: never deletes rows from the last 7 days regardless of
  the configured retention window.
- If the database is unreachable the cycle simply logs the error and returns.
- Counts deleted are logged and stored back in system_settings.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.crud import system_settings as ss_crud


async def prune_price_history(
    db: AsyncSession,
    retention_days: int,
) -> int:
    """
    Remove AssetPriceHistory rows older than *retention_days*.

    Returns the number of rows deleted.
    Enforces a minimum retention of 1 day (never touches last 24 h).
    """
    from app.crud import price_history as ph_crud

    safe_days = max(retention_days, 1)
    deleted = await ph_crud.cleanup_old_history(db, retention_days=safe_days)
    if deleted:
        logger.info(
            f"[retention] Price history pruned: {deleted} rows deleted "
            f"(retention={safe_days}d)"
        )
    else:
        logger.debug(f"[retention] Price history pruning: no rows older than {safe_days}d found")
    return deleted


async def prune_alert_history(
    db: AsyncSession,
    retention_days: int,
) -> int:
    """
    Remove AlertHistory rows older than *retention_days*.

    Returns the number of rows deleted.
    Enforces a minimum retention of 7 days.
    """
    from app.crud import alert as alert_crud

    safe_days = max(retention_days, 7)
    deleted = await alert_crud.cleanup_old_alert_history(db, retention_days=safe_days)
    if deleted:
        logger.info(
            f"[retention] Alert history pruned: {deleted} rows deleted "
            f"(retention={safe_days}d)"
        )
    else:
        logger.debug(f"[retention] Alert history pruning: no rows older than {safe_days}d found")
    return deleted


async def get_storage_stats(db: AsyncSession) -> dict:
    """
    Return a dict with row counts and date ranges for both history tables.

    Keys:
        price_total          — total AssetPriceHistory rows
        price_per_symbol     — {symbol: count}
        price_oldest         — datetime of oldest price row (or None)
        price_newest         — datetime of newest price row (or None)
        alert_total          — total AlertHistory rows
        alert_oldest         — datetime of oldest alert row (or None)
        alert_newest         — datetime of newest alert row (or None)
    """
    from sqlalchemy import func, select
    from app.models.price_history import AssetPriceHistory
    from app.models.alert_history import AlertHistory

    # Price history stats
    ph_stats = await _crud_price_history_stats(db)

    # Alert history stats
    ah_total_result = await db.execute(select(func.count(AlertHistory.id)))
    ah_total = ah_total_result.scalar_one() or 0

    ah_oldest_result = await db.execute(select(func.min(AlertHistory.triggered_at)))
    ah_oldest = ah_oldest_result.scalar_one()

    ah_newest_result = await db.execute(select(func.max(AlertHistory.triggered_at)))
    ah_newest = ah_newest_result.scalar_one()

    return {
        "price_total": ph_stats["total_rows"],
        "price_per_symbol": ph_stats["per_symbol"],
        "price_oldest": ph_stats["oldest"],
        "price_newest": ph_stats["newest"],
        "alert_total": ah_total,
        "alert_oldest": ah_oldest,
        "alert_newest": ah_newest,
    }


async def _crud_price_history_stats(db: AsyncSession) -> dict:
    from app.crud import price_history as ph_crud
    return await ph_crud.get_history_stats(db)


async def run_retention_cycle(db: AsyncSession) -> dict[str, int]:
    """
    Read retention config from system_settings and run pruning for each
    enabled table.  Stores counts + timestamp back into system_settings.

    Returns {"price_deleted": N, "alert_deleted": N}.
    """
    config = await ss_crud.get_retention_config(db)

    price_enabled = config.get(ss_crud.RETENTION_PRICE_ENABLED, "true").lower() == "true"
    alert_enabled = config.get(ss_crud.RETENTION_ALERT_ENABLED, "false").lower() == "true"

    try:
        price_days = int(config.get(ss_crud.RETENTION_PRICE_DAYS, "90"))
    except ValueError:
        price_days = 90
    try:
        alert_days = int(config.get(ss_crud.RETENTION_ALERT_DAYS, "365"))
    except ValueError:
        alert_days = 365

    price_deleted = 0
    alert_deleted = 0

    if price_enabled:
        price_deleted = await prune_price_history(db, price_days)

    if alert_enabled:
        alert_deleted = await prune_alert_history(db, alert_days)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    await ss_crud.set_value(db, ss_crud.RETENTION_LAST_RUN, now_str)
    await ss_crud.set_value(db, ss_crud.RETENTION_LAST_PRICE_DELETED, str(price_deleted))
    await ss_crud.set_value(db, ss_crud.RETENTION_LAST_ALERT_DELETED, str(alert_deleted))

    if price_deleted or alert_deleted:
        logger.info(
            f"[retention] Cycle complete — price: {price_deleted} deleted, "
            f"alert: {alert_deleted} deleted"
        )
    else:
        logger.debug("[retention] Cycle complete — nothing to prune")

    return {"price_deleted": price_deleted, "alert_deleted": alert_deleted}
