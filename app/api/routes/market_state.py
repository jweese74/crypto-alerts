"""
Market state API — returns the current market condition classification.
"""
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_login
from app.core.database import get_db
from app.crud import market_state as ms_crud
from app.models.user import User
from app.services.market_state import state_colour, state_icon

router = APIRouter(prefix="/api", tags=["market-state"])


@router.get("/market-state", response_class=JSONResponse)
async def get_market_state(
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    row = await ms_crud.get_current(db)
    if row is None:
        return {
            "state": "calm",
            "score": 0,
            "previous_state": None,
            "changed_at": None,
            "checked_at": None,
            "reasons": ["No data yet — waiting for first evaluation cycle"],
            "state_duration_minutes": None,
            "colour": state_colour("calm"),
            "icon": state_icon("calm"),
        }

    now = datetime.now(timezone.utc)
    duration_minutes = int((now - row.changed_at).total_seconds() / 60)

    try:
        reasons = json.loads(row.reasons_json)
    except (ValueError, TypeError):
        reasons = []

    return {
        "state": row.current_state,
        "score": row.score,
        "previous_state": row.previous_state,
        "changed_at": row.changed_at.isoformat() if row.changed_at else None,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
        "reasons": reasons,
        "state_duration_minutes": duration_minutes,
        "colour": state_colour(row.current_state),
        "icon": state_icon(row.current_state),
    }
