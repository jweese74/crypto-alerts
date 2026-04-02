"""
Asset price history and chart routes.

URL-safe symbol format: BTC_USD (slash replaced with underscore).
"""
import uuid as _uuid
from bisect import bisect_left
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_login
from app.core.config import get_settings
from app.core.database import get_db
from app.core.session import get_csrf_token, get_flashed_messages
from app.crud import alert as alert_crud
from app.crud import price_history as ph_crud
from app.models.user import User
from app.services.market_data import market_data_service

router = APIRouter(tags=["assets"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()

_VALID_RANGES = {"24h", "7d", "30d"}
_RANGE_LABELS = {"24h": "24 Hours", "7d": "7 Days", "30d": "30 Days"}


def _symbol_to_url(symbol: str) -> str:
    """BTC/USD → BTC_USD"""
    return symbol.replace("/", "_")


def _url_to_symbol(url_sym: str) -> str:
    """BTC_USD → BTC/USD"""
    return url_sym.replace("_", "/")


def _fmt_label(dt: datetime, range_key: str) -> str:
    """Format a datetime for chart X-axis labels."""
    if range_key == "24h":
        return dt.strftime("%H:%M")
    return dt.strftime("%m-%d %H:%M")


def _build_chart_data(
    display_symbol: str,
    range_key: str,
    points: list,
    current_price: float | None,
    user_rules: list,
    alert_history: list,
) -> dict:
    """Build the chart_data dict consumed by the frontend chart JS."""
    labels     = [_fmt_label(ts, range_key) for ts, _ in points]
    prices     = [round(p, 4) for _, p in points]
    timestamps = [ts.isoformat() for ts, _ in points]   # ISO-8601 for rich tooltips
    ts_list    = [ts for ts, _ in points]                # for trigger nearest-match

    # Alert thresholds for this symbol
    thresholds = [
        {
            "price":     rule.threshold,
            "condition": rule.condition.value,
            "label":     rule.label or f"{rule.condition.value.capitalize()} ${rule.threshold:,.2f}",
            "is_active": rule.is_active,
            "rule_id":   str(rule.id),
        }
        for rule in user_rules
        if rule.trading_pair == display_symbol
    ]

    # Trigger history: match each event to the nearest chart point by timestamp
    triggers = []
    for h in alert_history:
        if h.trading_pair != display_symbol:
            continue
        if not ts_list:
            break
        trig_ts = h.triggered_at
        # Ensure timezone-aware comparison
        if trig_ts.tzinfo is None:
            trig_ts = trig_ts.replace(tzinfo=timezone.utc)
        # Skip if outside chart window
        if trig_ts < ts_list[0] or trig_ts > ts_list[-1]:
            continue
        # Bisect to nearest point
        idx = bisect_left(ts_list, trig_ts)
        if idx >= len(ts_list):
            idx = len(ts_list) - 1
        if idx > 0:
            before_delta = abs((trig_ts - ts_list[idx - 1]).total_seconds())
            after_delta  = abs((ts_list[idx] - trig_ts).total_seconds())
            if before_delta < after_delta:
                idx -= 1
        triggers.append({
            "idx":          idx,
            "label":        labels[idx] if idx < len(labels) else "",
            "price":        round(h.triggered_price, 4),
            "message":      (h.message or "")[:120],
            "severity":     getattr(h, "severity", "normal") or "normal",
            "triggered_at": trig_ts.isoformat(),
        })

    return {
        "symbol":        display_symbol,
        "range":         range_key,
        "labels":        labels,
        "timestamps":    timestamps,
        "prices":        prices,
        "current_price": current_price,
        "thresholds":    thresholds,
        "triggers":      triggers,
        "point_count":   len(points),
    }


@router.get("/assets/{symbol}/chart", response_class=HTMLResponse)
async def chart_page(
    symbol: str,
    request: Request,
    range: str = Query("24h"),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    display_symbol = _url_to_symbol(symbol)

    if "/" not in display_symbol or not display_symbol.endswith("/USD"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown asset: {display_symbol}")

    if range not in _VALID_RANGES:
        range = "24h"

    points       = await ph_crud.get_chart_points(db, display_symbol, range)
    current_price = market_data_service.last_prices.get(display_symbol)
    if current_price is None:
        current_price = await ph_crud.get_latest_price(db, display_symbol)

    user_rules   = await alert_crud.get_rules_for_user(db, current_user.id)
    alert_history = await alert_crud.get_history_for_user(db, current_user.id, limit=200)

    chart_data = _build_chart_data(
        display_symbol, range, points, current_price, user_rules, alert_history
    )
    thresholds = chart_data["thresholds"]

    # Nav: featured pairs (from DB) + user's rule pairs (max 20, sorted)
    from app.services.featured_assets import featured_assets_service
    featured = await featured_assets_service.get_featured_symbols(db)
    nav_symbols  = list(dict.fromkeys(featured + [r.trading_pair for r in user_rules]))[:20]
    all_pairs_url = [{"symbol": s, "url": _symbol_to_url(s)} for s in sorted(nav_symbols)]

    return templates.TemplateResponse(
        "assets/chart.html",
        {
            "request":       request,
            "user":          current_user,
            "flash_messages": get_flashed_messages(request),
            "csrf_token":    get_csrf_token(request),
            "symbol":        display_symbol,
            "symbol_url":    symbol,
            "range":         range,
            "range_labels":  _RANGE_LABELS,
            "chart_data":    chart_data,
            "current_price": current_price,
            "thresholds":    thresholds,
            "all_pairs":     all_pairs_url,
            "has_history":   len(points) > 0,
        },
    )


@router.get("/api/assets/{symbol}/history", response_class=JSONResponse)
async def asset_history_api(
    symbol: str,
    range: str = Query("24h"),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    display_symbol = _url_to_symbol(symbol)

    if "/" not in display_symbol or not display_symbol.endswith("/USD"):
        raise HTTPException(status_code=404, detail="Unknown asset")

    if range not in _VALID_RANGES:
        range = "24h"

    points        = await ph_crud.get_chart_points(db, display_symbol, range)
    current_price = market_data_service.last_prices.get(display_symbol)
    user_rules    = await alert_crud.get_rules_for_user(db, current_user.id)
    alert_history = await alert_crud.get_history_for_user(db, current_user.id, limit=200)

    return _build_chart_data(
        display_symbol, range, points, current_price, user_rules, alert_history
    )


@router.get("/api/assets/stats", response_class=JSONResponse)
async def history_stats_api(
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    stats = await ph_crud.get_history_stats(db)
    for key in ("oldest", "newest"):
        if stats.get(key):
            stats[key] = stats[key].isoformat()
    return stats
