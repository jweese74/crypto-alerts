import uuid as _uuid
from datetime import time as time_type
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import csrf_protect, require_login
from app.core.config import get_settings
from app.core.database import get_db
from app.core.session import flash, get_csrf_token, get_flashed_messages
from app.crud import alert as alert_crud
from app.models.alert_rule import AlertCondition
from app.models.user import User
from app.services.kraken_assets import kraken_assets_service
from app.services.market_data import market_data_service

router = APIRouter(prefix="/alerts", tags=["alerts"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()

# Sorted IANA timezone list, built once at import time
_ALL_TIMEZONES: list[str] = sorted(available_timezones())


def _ctx(request: Request, user: User, **extra) -> dict:
    return {
        "request": request,
        "user": user,
        "flash_messages": get_flashed_messages(request),
        "csrf_token": get_csrf_token(request),
        **extra,
    }


def _parse_uuid(val: str) -> _uuid.UUID:
    try:
        return _uuid.UUID(val)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


def _validate_pair(trading_pair: str) -> bool:
    """Return True if the trading pair is acceptable."""
    if kraken_assets_service.validate_symbol(trading_pair):
        return True
    if settings.ALLOW_CUSTOM_PAIRS:
        return trading_pair.endswith("/USD") and len(trading_pair) >= 5
    return False


@router.get("/", response_class=HTMLResponse)
async def list_alerts(
    request: Request,
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    rules = await alert_crud.get_rules_for_user(db, current_user.id)
    prices = market_data_service.last_prices
    return templates.TemplateResponse(
        "alerts/list.html",
        _ctx(request, current_user, rules=rules, prices=prices, conditions=list(AlertCondition)),
    )


@router.get("/create", response_class=HTMLResponse)
async def create_alert_page(
    request: Request,
    current_user: User = Depends(require_login),
):
    pairs = kraken_assets_service.get_all_usd_pairs()
    return templates.TemplateResponse(
        "alerts/form.html",
        _ctx(
            request, current_user,
            rule=None,
            pairs=pairs,
            conditions=list(AlertCondition),
            action="/alerts/",
            form_title="New Alert Rule",
            allow_custom=settings.ALLOW_CUSTOM_PAIRS,
            timezones=_ALL_TIMEZONES,
        ),
    )


def _parse_time(s: str) -> time_type | None:
    """Parse 'HH:MM' string from an HTML time input; returns None if blank/invalid."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        parts = s.split(":")
        return time_type(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


@router.post("/", response_class=HTMLResponse)
async def create_alert(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    trading_pair: str = Form(...),
    condition: AlertCondition = Form(...),
    threshold: float = Form(...),
    label: str = Form(""),
    custom_message: str = Form(""),
    cooldown_minutes: int = Form(60),
    send_once: str = Form("false"),
    time_filter_enabled: str = Form("false"),
    active_hours_start: str = Form(""),
    active_hours_end: str = Form(""),
    active_timezone: str = Form("UTC"),
    critical_override: str = Form("false"),
    db: AsyncSession = Depends(get_db),
):
    trading_pair = trading_pair.strip().upper()
    if not _validate_pair(trading_pair):
        flash(request, f"'{trading_pair}' is not a supported Kraken USD pair.", "error")
        return RedirectResponse(url="/alerts/create", status_code=303)
    if threshold <= 0:
        flash(request, "Threshold must be greater than 0.", "error")
        return RedirectResponse(url="/alerts/create", status_code=303)

    rule = await alert_crud.create_rule(
        db,
        user_id=current_user.id,
        trading_pair=trading_pair,
        condition=condition,
        threshold=threshold,
        label=label.strip() or None,
        custom_message=custom_message.strip() or None,
        cooldown_minutes=max(1, cooldown_minutes),
        send_once=(send_once.lower() in ("true", "1", "on")),
        time_filter_enabled=(time_filter_enabled.lower() in ("true", "1", "on")),
        active_hours_start=_parse_time(active_hours_start),
        active_hours_end=_parse_time(active_hours_end),
        active_timezone=active_timezone.strip() or "UTC",
        critical_override=(critical_override.lower() in ("true", "1", "on")),
    )
    flash(request, f"Alert rule created for {rule.trading_pair}.", "success")
    from app.services.event_log import event_log
    await event_log.rule_created(
        db,
        trading_pair=rule.trading_pair,
        condition=rule.condition.value,
        threshold=rule.threshold,
        user_id=str(current_user.id),
    )
    return RedirectResponse(url="/alerts/", status_code=303)


@router.get("/history", response_class=HTMLResponse)
async def alert_history(
    request: Request,
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    history = await alert_crud.get_history_for_user(db, current_user.id, limit=100)
    return templates.TemplateResponse(
        "alerts/history.html",
        _ctx(request, current_user, history=history),
    )


@router.get("/{alert_id}/edit", response_class=HTMLResponse)
async def edit_alert_page(
    alert_id: str,
    request: Request,
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    rule = await alert_crud.get_rule_by_id(db, _parse_uuid(alert_id))
    if not rule or (rule.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    pairs = kraken_assets_service.get_all_usd_pairs()
    return templates.TemplateResponse(
        "alerts/form.html",
        _ctx(
            request, current_user,
            rule=rule,
            pairs=pairs,
            conditions=list(AlertCondition),
            action=f"/alerts/{alert_id}/edit",
            form_title="Edit Alert Rule",
            allow_custom=settings.ALLOW_CUSTOM_PAIRS,
            timezones=_ALL_TIMEZONES,
        ),
    )


@router.post("/{alert_id}/edit", response_class=HTMLResponse)
async def update_alert(
    alert_id: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    trading_pair: str = Form(...),
    condition: AlertCondition = Form(...),
    threshold: float = Form(...),
    label: str = Form(""),
    custom_message: str = Form(""),
    cooldown_minutes: int = Form(60),
    send_once: str = Form("false"),
    time_filter_enabled: str = Form("false"),
    active_hours_start: str = Form(""),
    active_hours_end: str = Form(""),
    active_timezone: str = Form("UTC"),
    critical_override: str = Form("false"),
    db: AsyncSession = Depends(get_db),
):
    rule = await alert_crud.get_rule_by_id(db, _parse_uuid(alert_id))
    if not rule or (rule.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    trading_pair = trading_pair.strip().upper()
    if not _validate_pair(trading_pair):
        flash(request, f"'{trading_pair}' is not a supported Kraken USD pair.", "error")
        return RedirectResponse(url=f"/alerts/{alert_id}/edit", status_code=303)

    parsed_start = _parse_time(active_hours_start)
    parsed_end   = _parse_time(active_hours_end)

    await alert_crud.update_rule(
        db, rule,
        trading_pair=trading_pair,
        condition=condition,
        threshold=threshold,
        label=label.strip(),
        custom_message=custom_message.strip(),
        cooldown_minutes=max(1, cooldown_minutes),
        send_once=(send_once.lower() in ("true", "1", "on")),
        time_filter_enabled=(time_filter_enabled.lower() in ("true", "1", "on")),
        active_hours_start=parsed_start,
        active_hours_end=parsed_end,
        active_timezone=active_timezone.strip() or "UTC",
        critical_override=(critical_override.lower() in ("true", "1", "on")),
        _clear_hours=(parsed_start is None and parsed_end is None),
    )
    flash(request, "Alert rule updated.", "success")
    return RedirectResponse(url="/alerts/", status_code=303)


@router.post("/{alert_id}/toggle", response_class=HTMLResponse)
async def toggle_alert(
    alert_id: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    rule = await alert_crud.get_rule_by_id(db, _parse_uuid(alert_id))
    if not rule or (rule.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    updated = await alert_crud.toggle_rule(db, rule)
    state = "enabled" if updated.is_active else "paused"
    flash(request, f"Alert rule {state}.", "success")
    from app.services.event_log import event_log
    await event_log.rule_toggled(db, trading_pair=updated.trading_pair, enabled=updated.is_active, user_id=str(current_user.id))
    return RedirectResponse(url="/alerts/", status_code=303)


@router.post("/{alert_id}/clone", response_class=HTMLResponse)
async def clone_alert(
    alert_id: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    rule = await alert_crud.get_rule_by_id(db, _parse_uuid(alert_id))
    if not rule or (rule.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    clone = await alert_crud.clone_rule(db, rule)
    flash(request, f"Alert cloned as '{clone.label}' (disabled — review before enabling).", "success")
    return RedirectResponse(url="/alerts/", status_code=303)


@router.post("/{alert_id}/delete", response_class=HTMLResponse)
async def delete_alert(
    alert_id: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    rule = await alert_crud.get_rule_by_id(db, _parse_uuid(alert_id))
    if not rule or (rule.user_id != current_user.id and not current_user.is_admin):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await alert_crud.delete_rule(db, rule)
    flash(request, "Alert rule deleted.", "success")
    from app.services.event_log import event_log
    await event_log.rule_deleted(db, trading_pair=rule.trading_pair, user_id=str(current_user.id))
    return RedirectResponse(url="/alerts/", status_code=303)
