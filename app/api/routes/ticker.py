"""
Ticker routes.

GET  /ticker              — dedicated live ticker view page
GET  /ticker/manage       — manage personal ticker assets
GET  /api/ticker/data     — JSON price data for the ticker (used by auto-refresh)

POST /ticker/assets/add
POST /ticker/assets/{symbol}/remove
POST /ticker/assets/{symbol}/toggle
POST /ticker/assets/{symbol}/move-up
POST /ticker/assets/{symbol}/move-down
POST /ticker/assets/{symbol}/rename
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import csrf_protect, require_login
from app.core.database import get_db
from app.core.session import flash, get_csrf_token, get_flashed_messages
from app.crud import alert as alert_crud
from app.crud import user_ticker as ut_crud
from app.models.user import User
from app.services.featured_assets import featured_assets_service
from app.services.market_data import market_data_service

router = APIRouter(tags=["ticker"])
templates = Jinja2Templates(directory="app/templates")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sym_to_key(symbol: str) -> str:
    """Convert 'BTC/USD' → 'BTC_USD' for use in HTML element IDs."""
    return symbol.replace("/", "_")


def _sym_from_url(symbol: str) -> str:
    """Reverse URL-safe symbol: 'BTC_USD' → 'BTC/USD'."""
    return symbol.replace("_", "/")


async def _build_ticker_items(
    db: AsyncSession,
    current_user: User,
) -> tuple[list[dict[str, Any]], int, int]:
    """
    Assemble the combined ticker item list:
      1. Admin-featured assets (enabled only)
      2. User personal assets (enabled only), deduplicating any overlap

    Returns (items, featured_count, personal_count).
    Each item dict has:
      symbol, display_name, price, is_featured, has_alert, key
    """
    prices = market_data_service.last_prices

    # Featured assets (admin-managed)
    from app.crud import featured_assets as fa_crud
    featured_rows = await fa_crud.get_enabled(db)
    featured_symbols: set[str] = {r.asset_symbol for r in featured_rows}

    # User personal assets
    personal_rows = await ut_crud.get_enabled_for_user(db, current_user.id)
    personal_symbols: set[str] = {r.asset_symbol for r in personal_rows}

    # User's alert rules — build a set of pairs with active rules
    user_rules = await alert_crud.get_rules_for_user(db, current_user.id)
    alert_pairs: set[str] = {r.trading_pair for r in user_rules if r.is_active}

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 1. Featured first
    for row in featured_rows:
        items.append({
            "symbol": row.asset_symbol,
            "display_name": row.display_name or row.asset_symbol.split("/")[0],
            "price": prices.get(row.asset_symbol),
            "is_featured": True,
            "has_alert": row.asset_symbol in alert_pairs,
            "key": _sym_to_key(row.asset_symbol),
        })
        seen.add(row.asset_symbol)

    # 2. User personal ticker assets (skip duplicates of featured)
    personal_unique = [r for r in personal_rows if r.asset_symbol not in seen]
    for row in personal_unique:
        items.append({
            "symbol": row.asset_symbol,
            "display_name": row.display_name or row.asset_symbol.split("/")[0],
            "price": prices.get(row.asset_symbol),
            "is_featured": False,
            "has_alert": True,  # explicitly added and has alert if in alert_pairs
            "key": _sym_to_key(row.asset_symbol),
        })
        seen.add(row.asset_symbol)

    # 3. Assets the user has active alert rules for — auto-included even if
    #    not explicitly added to their personal ticker list
    alert_personal_count = 0
    for pair in sorted(alert_pairs):
        if pair not in seen:
            items.append({
                "symbol": pair,
                "display_name": pair.split("/")[0],
                "price": prices.get(pair),
                "is_featured": False,
                "has_alert": True,
                "key": _sym_to_key(pair),
            })
            seen.add(pair)
            alert_personal_count += 1

    personal_total = len(personal_unique) + alert_personal_count
    return items, len(featured_rows), personal_total


# ── Ticker view page ──────────────────────────────────────────────────────────

@router.get("/ticker", response_class=HTMLResponse)
async def ticker_view(
    request: Request,
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    items, featured_count, personal_count = await _build_ticker_items(db, current_user)
    return templates.TemplateResponse(
        "ticker/view.html",
        {
            "request": request,
            "user": current_user,
            "csrf_token": get_csrf_token(request),
            "items": items,
            "featured_count": featured_count,
            "personal_count": personal_count,
            "updated_at": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            "item_count": len(items),
        },
    )


# ── JSON data endpoint (called by auto-refresh JS) ─────────────────────────────

@router.get("/api/ticker/data")
async def ticker_data(
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    """Return current ticker prices as JSON.  Used by the frontend auto-refresh."""
    items, featured_count, personal_count = await _build_ticker_items(db, current_user)
    return JSONResponse({
        "items": items,
        "featured_count": featured_count,
        "personal_count": personal_count,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


# ── Manage personal ticker assets ─────────────────────────────────────────────

@router.get("/ticker/manage", response_class=HTMLResponse)
async def ticker_manage(
    request: Request,
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from app.crud import featured_assets as fa_crud
    from app.services.kraken_assets import kraken_assets_service

    personal_rows = await ut_crud.get_for_user(db, current_user.id)
    personal_symbols = {r.asset_symbol for r in personal_rows}
    featured_rows = await fa_crud.get_enabled(db)
    all_pairs = kraken_assets_service.get_all_usd_pairs()

    return templates.TemplateResponse(
        "ticker/manage.html",
        {
            "request": request,
            "user": current_user,
            "flash_messages": get_flashed_messages(request),
            "csrf_token": get_csrf_token(request),
            "personal_rows": personal_rows,
            "personal_symbols": personal_symbols,
            "featured_rows": featured_rows,
            "all_pairs": all_pairs,
        },
    )


# ── CRUD endpoints ─────────────────────────────────────────────────────────────

@router.post("/ticker/assets/add")
async def add_ticker_asset(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
    asset_symbol: str = Form(...),
    display_name: str = Form(""),
):
    from app.services.kraken_assets import kraken_assets_service

    symbol = asset_symbol.strip().upper()
    if not kraken_assets_service.validate_symbol(symbol):
        flash(request, f"'{symbol}' is not a known Kraken USD pair.", "error")
        return RedirectResponse(url="/ticker/manage", status_code=303)

    try:
        await ut_crud.add(
            db,
            user_id=current_user.id,
            asset_symbol=symbol,
            display_name=display_name.strip() or None,
        )
        flash(request, f"'{symbol}' added to your ticker.", "success")
    except ValueError as exc:
        flash(request, str(exc), "error")

    return RedirectResponse(url="/ticker/manage", status_code=303)


@router.post("/ticker/assets/{symbol:path}/remove")
async def remove_ticker_asset(
    symbol: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    removed = await ut_crud.remove(db, current_user.id, symbol)
    if removed:
        flash(request, f"'{symbol}' removed from your ticker.", "success")
    else:
        flash(request, f"'{symbol}' was not in your ticker.", "warning")
    return RedirectResponse(url="/ticker/manage", status_code=303)


@router.post("/ticker/assets/{symbol:path}/toggle")
async def toggle_ticker_asset(
    symbol: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    row = await ut_crud.toggle_enabled(db, current_user.id, symbol)
    if row:
        state = "enabled" if row.enabled else "disabled"
        flash(request, f"'{symbol}' {state} in your ticker.", "success")
    else:
        flash(request, f"'{symbol}' not found in your ticker.", "warning")
    return RedirectResponse(url="/ticker/manage", status_code=303)


@router.post("/ticker/assets/{symbol:path}/move-up")
async def move_ticker_up(
    symbol: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    await ut_crud.move_up(db, current_user.id, symbol)
    return RedirectResponse(url="/ticker/manage", status_code=303)


@router.post("/ticker/assets/{symbol:path}/move-down")
async def move_ticker_down(
    symbol: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    await ut_crud.move_down(db, current_user.id, symbol)
    return RedirectResponse(url="/ticker/manage", status_code=303)


@router.post("/ticker/assets/{symbol:path}/rename")
async def rename_ticker_asset(
    symbol: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
    display_name: str = Form(""),
):
    await ut_crud.update_display_name(db, current_user.id, symbol, display_name)
    flash(request, f"Label for '{symbol}' updated.", "success")
    return RedirectResponse(url="/ticker/manage", status_code=303)
