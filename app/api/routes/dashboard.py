from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_login
from app.core.config import get_settings
from app.core.database import get_db
from app.core.session import get_csrf_token, get_flashed_messages
from app.crud import alert as alert_crud
from app.crud import market_state as ms_crud
from app.models.user import User
from app.services.market_data import market_data_service
from app.services.market_state import state_colour, state_icon
from app.services.scheduler import scheduler

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()

_PAIR_ICONS: dict[str, str] = {
    "BTC/USD": "₿", "ETH/USD": "Ξ", "LTC/USD": "Ł",
    "XRP/USD": "✕", "DOGE/USD": "Ð", "SOL/USD": "◎",
    "ADA/USD": "₳", "DOT/USD": "●", "TAO/USD": "τ", "FET/USD": "𝔽",
    "AVAX/USD": "A", "LINK/USD": "⬡", "ATOM/USD": "⚛", "NEAR/USD": "Ⓝ",
    "UNI/USD": "🦄", "MATIC/USD": "M", "TRX/USD": "T", "XLM/USD": "✦",
}


def _pair_icon(symbol: str) -> str:
    return _PAIR_ICONS.get(symbol, symbol.split("/")[0][0])


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    prices = market_data_service.last_prices
    recent_history = await alert_crud.get_recent_history(db, user_id=current_user.id, limit=5)
    rule_count = await alert_crud.count_rules(db, user_id=current_user.id)
    active_rule_count = await alert_crud.count_active_rules(db)

    # Market state
    import json
    from datetime import datetime, timezone
    ms_row = await ms_crud.get_current(db)
    if ms_row:
        now = datetime.now(timezone.utc)
        try:
            ms_reasons = json.loads(ms_row.reasons_json)
        except (ValueError, TypeError):
            ms_reasons = []
        market_state = {
            "state": ms_row.current_state,
            "score": ms_row.score,
            "colour": state_colour(ms_row.current_state),
            "icon": state_icon(ms_row.current_state),
            "duration_minutes": int((now - ms_row.changed_at).total_seconds() / 60),
            "reasons": ms_reasons,
        }
    else:
        market_state = {
            "state": "calm",
            "score": 0,
            "colour": state_colour("calm"),
            "icon": state_icon("calm"),
            "duration_minutes": 0,
            "reasons": [],
        }

    # Build display pairs: featured pairs (from DB) + user's rule pairs, deduplicated, max 20
    from app.services.featured_assets import featured_assets_service
    featured = await featured_assets_service.get_featured_symbols(db)
    user_rules = await alert_crud.get_rules_for_user(db, current_user.id)
    rule_pairs = list({r.trading_pair for r in user_rules})
    display_symbols = list(dict.fromkeys(featured + rule_pairs))[:20]
    display_pairs = [
        {
            "symbol": s,
            "price": prices.get(s),
            "icon": _pair_icon(s),
            "url": s.replace("/", "_"),
        }
        for s in display_symbols
    ]

    return templates.TemplateResponse(
        "dashboard/index.html",
        {
            "request": request,
            "user": current_user,
            "flash_messages": get_flashed_messages(request),
            "csrf_token": get_csrf_token(request),
            "prices": prices,
            "display_pairs": display_pairs,
            "market_state": market_state,
            "last_poll_time": market_data_service.last_poll_time,
            "scheduler_cycles": scheduler.cycle_count,
            "last_tick_at": scheduler.last_tick_at,
            "recent_history": recent_history,
            "rule_count": rule_count,
            "active_rule_count": active_rule_count,
        },
    )
