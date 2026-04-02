"""
CRUD helpers for the UserTickerAsset table.

Users can build a personal list of assets to monitor in their ticker view.
Featured assets (managed by admin) are always prepended separately.
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_ticker_asset import UserTickerAsset


# ── Reads ─────────────────────────────────────────────────────────────────────

async def get_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[UserTickerAsset]:
    """Return all ticker assets for a user, sorted by sort_order."""
    result = await db.execute(
        select(UserTickerAsset)
        .where(UserTickerAsset.user_id == user_id)
        .order_by(UserTickerAsset.sort_order, UserTickerAsset.asset_symbol)
    )
    return list(result.scalars().all())


async def get_enabled_for_user(db: AsyncSession, user_id: uuid.UUID) -> list[UserTickerAsset]:
    """Return only enabled ticker assets for a user, sorted."""
    result = await db.execute(
        select(UserTickerAsset)
        .where(UserTickerAsset.user_id == user_id, UserTickerAsset.enabled.is_(True))
        .order_by(UserTickerAsset.sort_order, UserTickerAsset.asset_symbol)
    )
    return list(result.scalars().all())


async def get_by_symbol(
    db: AsyncSession, user_id: uuid.UUID, symbol: str
) -> Optional[UserTickerAsset]:
    result = await db.execute(
        select(UserTickerAsset).where(
            UserTickerAsset.user_id == user_id,
            UserTickerAsset.asset_symbol == symbol,
        )
    )
    return result.scalar_one_or_none()


async def get_enabled_symbols(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    rows = await get_enabled_for_user(db, user_id)
    return [r.asset_symbol for r in rows]


# ── Writes ────────────────────────────────────────────────────────────────────

async def add(
    db: AsyncSession,
    user_id: uuid.UUID,
    asset_symbol: str,
    display_name: Optional[str] = None,
    sort_order: Optional[int] = None,
    enabled: bool = True,
) -> UserTickerAsset:
    """Add an asset to a user's ticker.  Raises ValueError if already present."""
    existing = await get_by_symbol(db, user_id, asset_symbol)
    if existing:
        raise ValueError(f"'{asset_symbol}' is already in your ticker")

    if sort_order is None:
        all_rows = await get_for_user(db, user_id)
        sort_order = (max((r.sort_order for r in all_rows), default=0) + 1)

    row = UserTickerAsset(
        user_id=user_id,
        asset_symbol=asset_symbol,
        display_name=display_name or None,
        sort_order=sort_order,
        enabled=enabled,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def remove(db: AsyncSession, user_id: uuid.UUID, symbol: str) -> bool:
    """Remove an asset from a user's ticker.  Returns True if it existed."""
    result = await db.execute(
        delete(UserTickerAsset).where(
            UserTickerAsset.user_id == user_id,
            UserTickerAsset.asset_symbol == symbol,
        )
    )
    await db.commit()
    return result.rowcount > 0


async def toggle_enabled(
    db: AsyncSession, user_id: uuid.UUID, symbol: str
) -> Optional[UserTickerAsset]:
    """Flip the enabled flag.  Returns updated row or None if not found."""
    row = await get_by_symbol(db, user_id, symbol)
    if not row:
        return None
    row.enabled = not row.enabled
    await db.commit()
    await db.refresh(row)
    return row


async def move_up(db: AsyncSession, user_id: uuid.UUID, symbol: str) -> None:
    """Swap sort_order with the immediately preceding row."""
    rows = await get_for_user(db, user_id)
    idx = next((i for i, r in enumerate(rows) if r.asset_symbol == symbol), None)
    if idx is None or idx == 0:
        return
    prev, curr = rows[idx - 1], rows[idx]
    prev.sort_order, curr.sort_order = curr.sort_order, prev.sort_order
    await db.commit()


async def move_down(db: AsyncSession, user_id: uuid.UUID, symbol: str) -> None:
    """Swap sort_order with the immediately following row."""
    rows = await get_for_user(db, user_id)
    idx = next((i for i, r in enumerate(rows) if r.asset_symbol == symbol), None)
    if idx is None or idx >= len(rows) - 1:
        return
    nxt, curr = rows[idx + 1], rows[idx]
    nxt.sort_order, curr.sort_order = curr.sort_order, nxt.sort_order
    await db.commit()


async def update_display_name(
    db: AsyncSession, user_id: uuid.UUID, symbol: str, display_name: str
) -> Optional[UserTickerAsset]:
    row = await get_by_symbol(db, user_id, symbol)
    if not row:
        return None
    row.display_name = display_name.strip() or None
    await db.commit()
    await db.refresh(row)
    return row


async def count_for_user(db: AsyncSession, user_id: uuid.UUID) -> int:
    from sqlalchemy import func
    result = await db.execute(
        select(func.count()).select_from(UserTickerAsset).where(
            UserTickerAsset.user_id == user_id
        )
    )
    return result.scalar_one()
