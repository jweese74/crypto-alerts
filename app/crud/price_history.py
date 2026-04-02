"""
CRUD helpers for AssetPriceHistory.

Aggregation strategy:
  24h  → raw points (up to ~288 at 5-min interval)
  7d   → 30-minute bucket averages
  30d  → 2-hour bucket averages
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price_history import AssetPriceHistory

# Map range key → (timedelta, bucket_minutes)
# bucket_minutes=0 means no aggregation (raw)
_RANGE_CONFIG: dict[str, tuple[timedelta, int]] = {
    "24h": (timedelta(hours=24), 0),
    "7d":  (timedelta(days=7),   30),
    "30d": (timedelta(days=30),  120),
}


async def store_snapshot(
    db: AsyncSession,
    *,
    symbol: str,
    price: float,
    captured_at: Optional[datetime] = None,
    source: str = "kraken",
) -> AssetPriceHistory:
    row = AssetPriceHistory(
        asset_symbol=symbol,
        price_usd=price,
        captured_at=captured_at or datetime.now(timezone.utc),
        source=source,
    )
    db.add(row)
    await db.commit()
    return row


async def get_raw_history(
    db: AsyncSession,
    symbol: str,
    since: datetime,
    until: Optional[datetime] = None,
) -> list[tuple[datetime, float]]:
    """Return (captured_at, price_usd) tuples ordered by time."""
    q = (
        select(AssetPriceHistory.captured_at, AssetPriceHistory.price_usd)
        .where(AssetPriceHistory.asset_symbol == symbol)
        .where(AssetPriceHistory.captured_at >= since)
        .order_by(AssetPriceHistory.captured_at.asc())
    )
    if until:
        q = q.where(AssetPriceHistory.captured_at <= until)
    result = await db.execute(q)
    return list(result.fetchall())


def _bucket_points(
    rows: list[tuple[datetime, float]],
    bucket_minutes: int,
) -> list[tuple[datetime, float]]:
    """Average prices into fixed-size time buckets."""
    if not rows or bucket_minutes == 0:
        return rows

    buckets: dict[int, list[float]] = {}
    bsec = bucket_minutes * 60
    for ts, price in rows:
        bucket_key = int(ts.timestamp() // bsec) * bsec
        buckets.setdefault(bucket_key, []).append(price)

    result = []
    for bucket_key in sorted(buckets):
        avg_price = sum(buckets[bucket_key]) / len(buckets[bucket_key])
        bucket_dt = datetime.fromtimestamp(bucket_key, tz=timezone.utc)
        result.append((bucket_dt, avg_price))
    return result


async def get_chart_points(
    db: AsyncSession,
    symbol: str,
    range_key: str = "24h",
) -> list[tuple[datetime, float]]:
    """
    Return (datetime, price) pairs suitable for chart rendering.
    Applies aggregation for longer ranges.
    """
    cfg = _RANGE_CONFIG.get(range_key, _RANGE_CONFIG["24h"])
    delta, bucket_minutes = cfg
    since = datetime.now(timezone.utc) - delta

    rows = await get_raw_history(db, symbol, since)
    return _bucket_points(rows, bucket_minutes)


async def get_latest_price(
    db: AsyncSession, symbol: str
) -> Optional[float]:
    result = await db.execute(
        select(AssetPriceHistory.price_usd)
        .where(AssetPriceHistory.asset_symbol == symbol)
        .order_by(AssetPriceHistory.captured_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row


async def cleanup_old_history(
    db: AsyncSession,
    retention_days: int = 30,
) -> int:
    """Delete rows older than retention_days. Returns count deleted."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = await db.execute(
        delete(AssetPriceHistory).where(AssetPriceHistory.captured_at < cutoff)
    )
    await db.commit()
    return result.rowcount


async def get_history_stats(db: AsyncSession) -> dict:
    """Admin stats: total rows, per-symbol counts, time range."""
    total_result = await db.execute(select(func.count(AssetPriceHistory.id)))
    total = total_result.scalar_one()

    per_symbol_result = await db.execute(
        select(AssetPriceHistory.asset_symbol, func.count(AssetPriceHistory.id))
        .group_by(AssetPriceHistory.asset_symbol)
        .order_by(AssetPriceHistory.asset_symbol)
    )
    per_symbol = {row[0]: row[1] for row in per_symbol_result.fetchall()}

    oldest_result = await db.execute(
        select(func.min(AssetPriceHistory.captured_at))
    )
    oldest = oldest_result.scalar_one()

    newest_result = await db.execute(
        select(func.max(AssetPriceHistory.captured_at))
    )
    newest = newest_result.scalar_one()

    return {
        "total_rows": total,
        "per_symbol": per_symbol,
        "oldest": oldest,
        "newest": newest,
    }


async def get_price_minutes_ago(
    db: AsyncSession,
    symbol: str,
    minutes_ago: int,
) -> Optional[float]:
    """
    Return the price closest to ``minutes_ago`` minutes in the past for a
    symbol, searching within a ±15-minute window.  Returns None if no data
    is available in that window.
    """
    target_time = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    result = await db.execute(
        select(AssetPriceHistory.price_usd)
        .where(AssetPriceHistory.asset_symbol == symbol)
        .where(AssetPriceHistory.captured_at >= target_time - timedelta(minutes=15))
        .where(AssetPriceHistory.captured_at <= target_time + timedelta(minutes=15))
        .order_by(AssetPriceHistory.captured_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return float(row) if row is not None else None


async def count_snapshots(db: AsyncSession, symbol: str) -> int:
    """Return number of stored snapshots for a symbol."""
    result = await db.execute(
        select(func.count(AssetPriceHistory.id)).where(
            AssetPriceHistory.asset_symbol == symbol
        )
    )
    return result.scalar_one() or 0
