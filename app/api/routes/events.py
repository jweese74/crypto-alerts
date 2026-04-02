"""
Routes for the human-readable event log feed.
GET /events       — HTML page with filters + pagination
GET /api/events   — JSON endpoint
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_login
from app.core.database import get_db
from app.crud import event_log as event_crud
from app.models.user import User

router = APIRouter(prefix="/events", tags=["events"])
templates = Jinja2Templates(directory="app/templates")

_PAGE_SIZE = 50


def _parse_since(range_key: str) -> Optional[datetime]:
    now = datetime.now(timezone.utc)
    mapping = {
        "1h":  timedelta(hours=1),
        "24h": timedelta(hours=24),
        "7d":  timedelta(days=7),
        "30d": timedelta(days=30),
        "all": None,
    }
    delta = mapping.get(range_key)
    return (now - delta) if delta else None


@router.get("/", response_class=HTMLResponse)
async def events_page(
    request: Request,
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
    asset: str = Query("", alias="asset"),
    event_type: str = Query("", alias="event_type"),
    severity: str = Query("", alias="severity"),
    range_key: str = Query("24h", alias="range"),
    page: int = Query(1, ge=1),
):
    since = _parse_since(range_key)
    offset = (page - 1) * _PAGE_SIZE

    events = await event_crud.get_events(
        db,
        user_id=str(current_user.id),
        is_admin=current_user.is_admin,
        asset_symbol=asset or None,
        event_type=event_type or None,
        severity=severity or None,
        since=since,
        limit=_PAGE_SIZE,
        offset=offset,
    )
    total = await event_crud.count_events(
        db,
        user_id=str(current_user.id),
        is_admin=current_user.is_admin,
        asset_symbol=asset or None,
        event_type=event_type or None,
        severity=severity or None,
        since=since,
    )
    known_assets = await event_crud.get_distinct_assets(db)

    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

    event_types = [
        ("", "All types"),
        (event_crud.ALERT_TRIGGERED, "Alert triggered"),
        (event_crud.ALERT_SUPPRESSED_COOLDOWN, "Suppressed (cooldown)"),
        (event_crud.ALERT_SUPPRESSED_TIME_FILTER, "Suppressed (time filter)"),
        (event_crud.MARKET_STATE_CHANGED, "Market state changed"),
        (event_crud.RULE_CREATED, "Rule created"),
        (event_crud.RULE_UPDATED, "Rule updated"),
        (event_crud.RULE_DELETED, "Rule deleted"),
        (event_crud.RULE_ENABLED, "Rule enabled"),
        (event_crud.RULE_DISABLED, "Rule disabled"),
        (event_crud.SYSTEM_STARTUP, "System startup"),
        (event_crud.USER_LOGIN, "User login"),
        (event_crud.RETENTION_RUN, "Retention run"),
    ]

    return templates.TemplateResponse(
        "events/index.html",
        {
            "request": request,
            "current_user": current_user,
            "events": events,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "page_size": _PAGE_SIZE,
            "filters": {
                "asset": asset,
                "event_type": event_type,
                "severity": severity,
                "range": range_key,
            },
            "known_assets": known_assets,
            "event_types": event_types,
            "range_options": [
                ("1h", "Last hour"),
                ("24h", "Last 24 hours"),
                ("7d", "Last 7 days"),
                ("30d", "Last 30 days"),
                ("all", "All time"),
            ],
        },
    )


@router.get("/api", response_class=JSONResponse)
async def events_api(
    request: Request,
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
    asset: str = Query("", alias="asset"),
    event_type: str = Query("", alias="event_type"),
    severity: str = Query("", alias="severity"),
    range_key: str = Query("24h", alias="range"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    since = _parse_since(range_key)
    events = await event_crud.get_events(
        db,
        user_id=str(current_user.id),
        is_admin=current_user.is_admin,
        asset_symbol=asset or None,
        event_type=event_type or None,
        severity=severity or None,
        since=since,
        limit=limit,
        offset=offset,
    )
    return {
        "events": [
            {
                "id": str(e.id),
                "occurred_at": e.occurred_at.isoformat(),
                "event_type": e.event_type,
                "severity": e.severity,
                "asset_symbol": e.asset_symbol,
                "description": e.description,
            }
            for e in events
        ],
        "count": len(events),
    }
