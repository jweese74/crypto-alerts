"""
Simulation routes
=================
GET  /simulate        — page: asset + range + rule selector
POST /simulate        — run simulation, render results inline
GET  /api/simulate    — JSON version (same logic)
"""
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_login
from app.core.database import get_db
from app.core.session import get_csrf_token, get_flashed_messages
from app.models.user import User

router = APIRouter(prefix="/simulate", tags=["simulation"])
templates = Jinja2Templates(directory="app/templates")

_RANGE_OPTIONS = [
    ("24h",  "Last 24 hours"),
    ("7d",   "Last 7 days"),
    ("30d",  "Last 30 days"),
]


def _ctx(request: Request, user: User, **extra) -> dict:
    return {
        "request": request,
        "user": user,
        "flash_messages": get_flashed_messages(request),
        "csrf_token": get_csrf_token(request),
        **extra,
    }


async def _get_user_rules_for_asset(
    db: AsyncSession,
    user: User,
    trading_pair: Optional[str],
) -> list:
    """Return alert rules for user, optionally filtered by trading_pair."""
    from app.crud.alert import get_rules_for_user
    from app.models.user import UserRole

    if user.role == UserRole.ADMIN:
        from app.crud.alert import get_all_rules
        rules = await get_all_rules(db)
    else:
        rules = await get_rules_for_user(db, user.id)

    if trading_pair:
        rules = [r for r in rules if r.trading_pair == trading_pair]
    return rules


async def _load_price_points(
    db: AsyncSession,
    trading_pair: str,
    range_key: str,
) -> list[tuple]:
    """Load (datetime, price) tuples from price history."""
    from app.crud.price_history import get_chart_points
    # Use raw range key — get_chart_points handles aggregation
    return await get_chart_points(db, trading_pair, range_key=range_key)


@router.get("", response_class=HTMLResponse)
async def simulate_page(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
    asset: Optional[str] = None,
    range_key: str = "7d",
):
    from app.services.kraken_assets import kraken_assets_service
    pairs = kraken_assets_service.get_all_usd_pairs()

    rules = await _get_user_rules_for_asset(db, user, asset)

    return templates.TemplateResponse(
        "simulation/index.html",
        _ctx(
            request, user,
            pairs=pairs,
            rules=rules,
            selected_asset=asset or "",
            selected_range=range_key,
            range_options=_RANGE_OPTIONS,
            result=None,
        ),
    )


@router.post("", response_class=HTMLResponse)
async def run_simulation_view(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from app.services.kraken_assets import kraken_assets_service
    from app.services.simulation import run_simulation, sim_rule_from_alert_rule

    form = await request.form()
    asset      = str(form.get("asset", "")).strip()
    range_key  = str(form.get("range_key", "7d")).strip()
    rule_ids   = set(form.getlist("rule_ids"))

    pairs = kraken_assets_service.get_all_usd_pairs()
    all_rules = await _get_user_rules_for_asset(db, user, asset if asset else None)

    errors = []
    result = None

    if not asset:
        errors.append("Please select an asset.")
    else:
        selected_rules = [r for r in all_rules if not rule_ids or str(r.id) in rule_ids]

        if not selected_rules:
            errors.append("No matching alert rules found for this asset. Create a rule first.")
        else:
            price_points = await _load_price_points(db, asset, range_key)
            if not price_points:
                errors.append(
                    f"No price history found for {asset} in the selected time range. "
                    "The system needs to collect data first."
                )
            else:
                sim_rules = [sim_rule_from_alert_rule(r) for r in selected_rules]
                result = run_simulation(
                    asset_symbol=asset,
                    price_points=price_points,
                    rules=sim_rules,
                    range_key=range_key,
                )

    # Reload rules filtered to asset for display
    display_rules = await _get_user_rules_for_asset(db, user, asset if asset else None)

    return templates.TemplateResponse(
        "simulation/index.html",
        _ctx(
            request, user,
            pairs=pairs,
            rules=display_rules,
            selected_asset=asset,
            selected_range=range_key,
            selected_rule_ids=rule_ids,
            range_options=_RANGE_OPTIONS,
            result=result,
            errors=errors,
        ),
    )


@router.get("/api", response_class=JSONResponse)
async def simulate_api(
    request: Request,
    user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
    asset: str = "",
    range_key: str = "7d",
    rule_ids: str = "",   # comma-separated rule IDs
):
    """JSON endpoint — returns simulation results for programmatic access."""
    from app.services.simulation import run_simulation, sim_rule_from_alert_rule

    if not asset:
        return JSONResponse({"error": "asset parameter required"}, status_code=400)

    id_filter = set(rule_ids.split(",")) if rule_ids else set()
    all_rules  = await _get_user_rules_for_asset(db, user, asset)
    sel_rules  = [r for r in all_rules if not id_filter or str(r.id) in id_filter]

    price_points = await _load_price_points(db, asset, range_key)
    if not sel_rules or not price_points:
        return JSONResponse({
            "asset": asset,
            "range": range_key,
            "price_points": len(price_points),
            "rules_evaluated": len(sel_rules),
            "total_triggers": 0,
            "rule_results": [],
        })

    sim_rules = [sim_rule_from_alert_rule(r) for r in sel_rules]
    result = run_simulation(
        asset_symbol=asset,
        price_points=price_points,
        rules=sim_rules,
        range_key=range_key,
    )

    return JSONResponse({
        "asset": result.asset_symbol,
        "range": result.range_key,
        "start_time": result.start_time.isoformat(),
        "end_time":   result.end_time.isoformat(),
        "price_points": result.price_point_count,
        "total_triggers": result.total_triggers,
        "rule_results": [
            {
                "rule_id":    rr.rule.id,
                "rule_label": rr.rule.label,
                "trading_pair": rr.rule.trading_pair,
                "condition":  rr.rule.condition.value,
                "threshold":  rr.rule.threshold,
                "trigger_count": rr.trigger_count,
                "skipped_cooldown": rr.skipped_cooldown,
                "skipped_time_filter": rr.skipped_time_filter,
                "deactivated_send_once": rr.deactivated_send_once,
                "triggers": [
                    {
                        "triggered_at":    t.triggered_at.isoformat(),
                        "triggered_price": t.triggered_price,
                        "previous_state":  t.previous_state,
                    }
                    for t in rr.triggers
                ],
            }
            for rr in result.rule_results
        ],
    })
