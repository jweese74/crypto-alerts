"""
CRUD helpers for the FeaturedAsset table.

Featured assets are the admin-managed short-list of assets shown on the
dashboard price strip and chart nav sidebar.  All write operations call
featured_assets_service.invalidate_cache() so stale data is never served.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.featured_asset import FeaturedAsset

# ── Default seed list ─────────────────────────────────────────────────────────
# Only used when the table is empty on first run.
_DEFAULT_FEATURED = [
    {"asset_symbol": "BTC/USD", "kraken_pair": "XBTUSD", "display_name": "Bitcoin",  "sort_order": 1},
    {"asset_symbol": "ETH/USD", "kraken_pair": "ETHUSD", "display_name": "Ethereum", "sort_order": 2},
    {"asset_symbol": "SOL/USD", "kraken_pair": "SOLUSD", "display_name": "Solana",   "sort_order": 3},
    {"asset_symbol": "ADA/USD", "kraken_pair": "ADAUSD", "display_name": "Cardano",  "sort_order": 4},
    {"asset_symbol": "XRP/USD", "kraken_pair": "XRPUSD", "display_name": "XRP",      "sort_order": 5},
    {"asset_symbol": "DOGE/USD","kraken_pair": "XDGUSD", "display_name": "Dogecoin", "sort_order": 6},
]


# ── Reads ─────────────────────────────────────────────────────────────────────

async def get_all(db: AsyncSession) -> list[FeaturedAsset]:
    """Return all featured assets, sorted by sort_order then symbol."""
    result = await db.execute(
        select(FeaturedAsset).order_by(FeaturedAsset.sort_order, FeaturedAsset.asset_symbol)
    )
    return list(result.scalars().all())


async def get_enabled(db: AsyncSession) -> list[FeaturedAsset]:
    """Return only enabled featured assets, sorted."""
    result = await db.execute(
        select(FeaturedAsset)
        .where(FeaturedAsset.enabled.is_(True))
        .order_by(FeaturedAsset.sort_order, FeaturedAsset.asset_symbol)
    )
    return list(result.scalars().all())


async def get_enabled_symbols(db: AsyncSession) -> list[str]:
    """Return the ordered list of enabled featured asset symbols."""
    rows = await get_enabled(db)
    return [r.asset_symbol for r in rows]


async def get_by_symbol(db: AsyncSession, symbol: str) -> Optional[FeaturedAsset]:
    result = await db.execute(
        select(FeaturedAsset).where(FeaturedAsset.asset_symbol == symbol)
    )
    return result.scalar_one_or_none()


async def count(db: AsyncSession) -> int:
    from sqlalchemy import func
    result = await db.execute(select(func.count()).select_from(FeaturedAsset))
    return result.scalar_one()


# ── Writes ────────────────────────────────────────────────────────────────────

async def add(
    db: AsyncSession,
    asset_symbol: str,
    kraken_pair: str,
    display_name: Optional[str] = None,
    sort_order: Optional[int] = None,
    notes: Optional[str] = None,
    enabled: bool = True,
) -> FeaturedAsset:
    """Add a new featured asset.  Raises ValueError if already present."""
    existing = await get_by_symbol(db, asset_symbol)
    if existing:
        raise ValueError(f"'{asset_symbol}' is already in the featured list")

    if sort_order is None:
        # Place at the end
        all_rows = await get_all(db)
        sort_order = (max((r.sort_order for r in all_rows), default=0) + 1)

    row = FeaturedAsset(
        asset_symbol=asset_symbol,
        kraken_pair=kraken_pair,
        display_name=display_name or None,
        sort_order=sort_order,
        enabled=enabled,
        notes=notes,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    _bust_cache()
    return row


async def remove(db: AsyncSession, symbol: str) -> bool:
    """Remove a featured asset by symbol.  Returns True if it existed."""
    result = await db.execute(
        delete(FeaturedAsset).where(FeaturedAsset.asset_symbol == symbol)
    )
    await db.commit()
    _bust_cache()
    return result.rowcount > 0


async def toggle_enabled(db: AsyncSession, symbol: str) -> Optional[FeaturedAsset]:
    """Flip the enabled flag.  Returns the updated row, or None if not found."""
    row = await get_by_symbol(db, symbol)
    if not row:
        return None
    row.enabled = not row.enabled
    await db.commit()
    await db.refresh(row)
    _bust_cache()
    return row


async def set_sort_order(db: AsyncSession, symbol: str, sort_order: int) -> Optional[FeaturedAsset]:
    """Update the sort_order of a featured asset."""
    row = await get_by_symbol(db, symbol)
    if not row:
        return None
    row.sort_order = sort_order
    await db.commit()
    await db.refresh(row)
    _bust_cache()
    return row


async def move_up(db: AsyncSession, symbol: str) -> None:
    """Swap sort_order with the immediately preceding row."""
    rows = await get_all(db)
    idx = next((i for i, r in enumerate(rows) if r.asset_symbol == symbol), None)
    if idx is None or idx == 0:
        return
    prev = rows[idx - 1]
    curr = rows[idx]
    prev.sort_order, curr.sort_order = curr.sort_order, prev.sort_order
    await db.commit()
    _bust_cache()


async def move_down(db: AsyncSession, symbol: str) -> None:
    """Swap sort_order with the immediately following row."""
    rows = await get_all(db)
    idx = next((i for i, r in enumerate(rows) if r.asset_symbol == symbol), None)
    if idx is None or idx >= len(rows) - 1:
        return
    nxt = rows[idx + 1]
    curr = rows[idx]
    nxt.sort_order, curr.sort_order = curr.sort_order, nxt.sort_order
    await db.commit()
    _bust_cache()


async def update_display_name(
    db: AsyncSession, symbol: str, display_name: str
) -> Optional[FeaturedAsset]:
    """Update the friendly display name for a featured asset."""
    row = await get_by_symbol(db, symbol)
    if not row:
        return None
    row.display_name = display_name.strip() or None
    await db.commit()
    await db.refresh(row)
    return row


# ── Seeding ───────────────────────────────────────────────────────────────────

async def seed_defaults(db: AsyncSession) -> int:
    """
    Populate the featured_assets table with default pairs if it is empty.
    Safe to call on every startup — a no-op if rows already exist.
    Returns the number of rows inserted.
    """
    if await count(db) > 0:
        return 0

    inserted = 0
    for item in _DEFAULT_FEATURED:
        row = FeaturedAsset(**item, enabled=True)
        db.add(row)
        inserted += 1
    await db.commit()
    _bust_cache()
    return inserted


# ── Cache bust helper (avoids circular import) ─────────────────────────────────

def _bust_cache() -> None:
    """Signal the in-memory cache to refresh on next read."""
    try:
        from app.services.featured_assets import featured_assets_service
        featured_assets_service.invalidate_cache()
    except Exception:
        pass
