"""
CRUD helpers for MarketState.
"""
import json
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_state import MarketState

_SINGLETON_ID = 1


async def get_current(db: AsyncSession) -> Optional[MarketState]:
    result = await db.execute(select(MarketState).where(MarketState.id == _SINGLETON_ID))
    return result.scalar_one_or_none()


async def upsert(
    db: AsyncSession,
    *,
    current_state: str,
    previous_state: Optional[str],
    score: int,
    changed_at: datetime,
    checked_at: datetime,
    reasons: list[str],
) -> MarketState:
    row = await get_current(db)
    now = checked_at or datetime.now(timezone.utc)

    if row is None:
        row = MarketState(id=_SINGLETON_ID)
        db.add(row)

    row.current_state = current_state
    row.previous_state = previous_state
    row.score = score
    row.changed_at = changed_at
    row.checked_at = now
    row.reasons_json = json.dumps(reasons)
    await db.commit()
    await db.refresh(row)
    return row
