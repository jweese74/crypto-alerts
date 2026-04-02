from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import csrf_protect, require_admin
from app.core.database import get_db
from app.core.session import flash, get_csrf_token, get_flashed_messages
from app.crud import user as user_crud
from app.models.user import User, UserRole

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["format_number"] = lambda v: f"{v:,}"

def _ctx(request: Request, admin: User, **extra) -> dict:
    return {
        "request": request,
        "user": admin,
        "flash_messages": get_flashed_messages(request),
        "csrf_token": get_csrf_token(request),
        **extra,
    }


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.crud import alert as alert_crud
    from app.crud import user as user_crud_inner
    from app.services.market_data import market_data_service
    from app.services.scheduler import scheduler

    users = await user_crud_inner.get_all(db)
    total_rules = await alert_crud.count_rules(db)
    active_rules = await alert_crud.count_active_rules(db)
    total_history = await alert_crud.count_history(db)
    recent_history = await alert_crud.get_recent_history(db, limit=10)
    prices = market_data_service.last_prices

    return templates.TemplateResponse(
        "admin/dashboard.html",
        _ctx(
            request, admin,
            users=users,
            total_rules=total_rules,
            active_rules=active_rules,
            total_history=total_history,
            recent_history=recent_history,
            prices=prices,
            last_poll_time=market_data_service.last_poll_time,
            scheduler_cycles=scheduler.cycle_count,
        ),
    )


@router.get("/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    users = await user_crud.get_all(db)
    return templates.TemplateResponse(
        "admin/users.html",
        _ctx(request, admin, users=users, roles=list(UserRole)),
    )


@router.post("/users", response_class=HTMLResponse)
async def create_user(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    role: UserRole = Form(UserRole.USER),
    db: AsyncSession = Depends(get_db),
):
    if await user_crud.get_by_email(db, email):
        flash(request, f"Email '{email}' is already registered.", "error")
        return RedirectResponse(url="/admin/users", status_code=303)
    if await user_crud.get_by_username(db, username):
        flash(request, f"Username '{username}' is already taken.", "error")
        return RedirectResponse(url="/admin/users", status_code=303)
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return RedirectResponse(url="/admin/users", status_code=303)

    new_user = await user_crud.create(db, email=email, username=username, password=password, role=role)
    flash(request, f"User '{new_user.username}' created successfully.", "success")
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle-active")
async def toggle_user_active(
    user_id: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    import uuid as _uuid
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    target = await user_crud.get_by_id(db, uid)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if target.id == admin.id:
        flash(request, "You cannot disable your own account.", "error")
        return RedirectResponse(url="/admin/users", status_code=303)

    updated = await user_crud.set_active(db, target, not target.is_active)
    state = "enabled" if updated.is_active else "disabled"
    flash(request, f"User '{updated.username}' has been {state}.", "success")
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/role")
async def change_user_role(
    user_id: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    role: UserRole = Form(...),
    db: AsyncSession = Depends(get_db),
):
    import uuid as _uuid
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    target = await user_crud.get_by_id(db, uid)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if target.id == admin.id and role != UserRole.ADMIN:
        flash(request, "You cannot remove your own admin role.", "error")
        return RedirectResponse(url="/admin/users", status_code=303)

    await user_crud.set_role(db, target, role)
    flash(request, f"Role for '{target.username}' updated to {role.value}.", "success")
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/users/{user_id}/delete")
async def delete_user(
    user_id: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    import uuid as _uuid
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    target = await user_crud.get_by_id(db, uid)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if target.id == admin.id:
        flash(request, "You cannot delete your own account.", "error")
        return RedirectResponse(url="/admin/users", status_code=303)

    username = target.username
    await user_crud.delete(db, target)
    flash(request, f"User '{username}' has been permanently deleted.", "success")
    return RedirectResponse(url="/admin/users", status_code=303)



@router.get("/alerts", response_class=HTMLResponse)
async def admin_all_alerts(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.crud import alert as alert_crud
    from app.crud import user as user_crud_inner

    rules = await alert_crud.get_all_rules(db)
    all_users = await user_crud_inner.get_all(db)
    user_map = {u.id: u for u in all_users}
    return templates.TemplateResponse(
        "admin/alerts.html",
        _ctx(request, admin, rules=rules, user_map=user_map),
    )


@router.get("/assets", response_class=HTMLResponse)
async def admin_assets(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.kraken_assets import kraken_assets_service
    from app.core.config import get_settings
    from app.crud import featured_assets as fa_crud

    settings_obj = get_settings()
    all_pairs = kraken_assets_service.get_all_usd_pairs()
    featured_rows = await fa_crud.get_all(db)
    featured_symbols = {r.asset_symbol for r in featured_rows}

    return templates.TemplateResponse(
        "admin/assets.html",
        _ctx(
            request, admin,
            pairs=all_pairs,
            pair_count=kraken_assets_service.pair_count,
            last_refresh=kraken_assets_service.last_refresh,
            is_using_fallback=kraken_assets_service.is_using_fallback,
            allow_custom=settings_obj.ALLOW_CUSTOM_PAIRS,
            featured_rows=featured_rows,
            featured_symbols=featured_symbols,
        ),
    )


@router.post("/assets/refresh", response_class=HTMLResponse)
async def refresh_assets(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
):
    from app.services.kraken_assets import kraken_assets_service
    count = await kraken_assets_service.force_refresh()
    flash(request, f"Asset list refreshed — {count} USD pairs discovered.", "success")
    return RedirectResponse(url="/admin/assets", status_code=303)


@router.post("/assets/featured/add")
async def add_featured_asset(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    asset_symbol: str = Form(...),
    display_name: str = Form(""),
):
    from app.crud import featured_assets as fa_crud
    from app.services.kraken_assets import kraken_assets_service

    symbol = asset_symbol.strip().upper()
    if not symbol:
        flash(request, "Asset symbol is required.", "error")
        return RedirectResponse(url="/admin/assets", status_code=303)

    pair = kraken_assets_service.get_pair(symbol)
    if not pair:
        flash(request, f"'{symbol}' is not a known Kraken USD pair.", "error")
        return RedirectResponse(url="/admin/assets", status_code=303)

    try:
        await fa_crud.add(
            db,
            asset_symbol=symbol,
            kraken_pair=pair.query_name,
            display_name=display_name.strip() or None,
        )
        flash(request, f"'{symbol}' added to featured markets.", "success")
    except ValueError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(url="/admin/assets", status_code=303)


@router.post("/assets/featured/{symbol:path}/remove")
async def remove_featured_asset(
    symbol: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.crud import featured_assets as fa_crud

    removed = await fa_crud.remove(db, symbol)
    if removed:
        flash(request, f"'{symbol}' removed from featured markets.", "success")
    else:
        flash(request, f"'{symbol}' was not in the featured list.", "warning")
    return RedirectResponse(url="/admin/assets", status_code=303)


@router.post("/assets/featured/{symbol:path}/toggle")
async def toggle_featured_asset(
    symbol: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.crud import featured_assets as fa_crud

    row = await fa_crud.toggle_enabled(db, symbol)
    if row:
        state = "enabled" if row.enabled else "disabled"
        flash(request, f"'{symbol}' {state} in featured markets.", "success")
    else:
        flash(request, f"'{symbol}' not found in featured list.", "warning")
    return RedirectResponse(url="/admin/assets", status_code=303)


@router.post("/assets/featured/{symbol:path}/move-up")
async def move_featured_up(
    symbol: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.crud import featured_assets as fa_crud
    await fa_crud.move_up(db, symbol)
    return RedirectResponse(url="/admin/assets", status_code=303)


@router.post("/assets/featured/{symbol:path}/move-down")
async def move_featured_down(
    symbol: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.crud import featured_assets as fa_crud
    await fa_crud.move_down(db, symbol)
    return RedirectResponse(url="/admin/assets", status_code=303)


@router.post("/assets/featured/{symbol:path}/rename")
async def rename_featured_asset(
    symbol: str,
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    display_name: str = Form(""),
):
    from app.crud import featured_assets as fa_crud
    await fa_crud.update_display_name(db, symbol, display_name)
    flash(request, f"Display name for '{symbol}' updated.", "success")
    return RedirectResponse(url="/admin/assets", status_code=303)


# ── Data Retention ─────────────────────────────────────────────────────────────

_RETENTION_DAY_OPTIONS = [7, 14, 30, 60, 90, 180, 365]


@router.get("/retention", response_class=HTMLResponse)
async def retention_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.crud import system_settings as ss_crud
    from app.services.retention import get_storage_stats

    config = await ss_crud.get_retention_config(db)
    stats = await get_storage_stats(db)

    return templates.TemplateResponse(
        "admin/retention.html",
        _ctx(
            request, admin,
            config=config,
            stats=stats,
            day_options=_RETENTION_DAY_OPTIONS,
            ss=ss_crud,
        ),
    )


@router.post("/retention/settings")
async def save_retention_settings(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    price_enabled: str = Form("false"),
    price_days: int = Form(90),
    alert_enabled: str = Form("false"),
    alert_days: int = Form(365),
):
    from app.crud import system_settings as ss_crud

    # Clamp to safe values
    price_days = max(1, min(price_days, 3650))
    alert_days = max(7, min(alert_days, 3650))

    await ss_crud.save_retention_config(db, {
        ss_crud.RETENTION_PRICE_ENABLED: price_enabled.lower(),
        ss_crud.RETENTION_PRICE_DAYS:    str(price_days),
        ss_crud.RETENTION_ALERT_ENABLED: alert_enabled.lower(),
        ss_crud.RETENTION_ALERT_DAYS:    str(alert_days),
    })
    flash(request, "Retention settings saved.", "success")
    return RedirectResponse(url="/admin/retention", status_code=303)


@router.post("/retention/run")
async def run_retention_now(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.retention import run_retention_cycle

    result = await run_retention_cycle(db)
    p = result["price_deleted"]
    a = result["alert_deleted"]
    flash(
        request,
        f"Cleanup complete — price history: {p} rows removed, alert history: {a} rows removed.",
        "success",
    )
    return RedirectResponse(url="/admin/retention", status_code=303)


# ── Backup / Export / Import ───────────────────────────────────────────────────

@router.get("/backup", response_class=HTMLResponse)
async def backup_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.crud import alert as alert_crud
    from app.crud import user as user_crud_inner
    from app.crud import system_settings as ss_crud

    rule_count = await alert_crud.count_rules(db)
    history_count = await alert_crud.count_history(db)
    user_count = await user_crud_inner.count(db)
    setting_count = len(await ss_crud.get_all(db))

    return templates.TemplateResponse(
        "admin/backup.html",
        _ctx(
            request, admin,
            rule_count=rule_count,
            history_count=history_count,
            user_count=user_count,
            setting_count=setting_count,
        ),
    )


@router.get("/backup/export")
async def export_backup(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    include_users: bool = False,
    include_history: bool = False,
):
    from datetime import datetime, timezone
    from fastapi.responses import Response
    from app.services.backup import export_data, to_json

    payload = await export_data(
        db,
        include_users=include_users,
        include_history=include_history,
        exported_by=admin.username,
    )
    filename = (
        f"crypto_alerts_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    )
    return Response(
        content=to_json(payload),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/backup/import")
async def import_backup(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from fastapi import UploadFile, File
    from app.services.backup import from_json, validate_payload, import_data, ImportError as BkImportError

    form = await request.form()
    upload = form.get("backup_file")
    if not upload or not hasattr(upload, "read"):
        flash(request, "No file uploaded.", "error")
        return RedirectResponse(url="/admin/backup", status_code=303)

    raw = await upload.read()
    if len(raw) > 10 * 1024 * 1024:  # 10 MB guard
        flash(request, "File too large (max 10 MB).", "error")
        return RedirectResponse(url="/admin/backup", status_code=303)

    try:
        payload = from_json(raw)
    except ValueError as exc:
        flash(request, f"Invalid JSON: {exc}", "error")
        return RedirectResponse(url="/admin/backup", status_code=303)

    try:
        validate_payload(payload)
    except BkImportError as exc:
        flash(request, f"Validation failed: {exc}", "error")
        return RedirectResponse(url="/admin/backup", status_code=303)

    import_settings = form.get("import_settings", "false").lower() == "true"
    import_rules = form.get("import_rules", "false").lower() == "true"
    import_history = form.get("import_history", "false").lower() == "true"
    import_users = form.get("import_users", "false").lower() == "true"
    overwrite_rules = form.get("overwrite_rules", "false").lower() == "true"

    # Take a safety backup before any writes
    from app.services.backup import export_data, to_json
    pre_backup = await export_data(
        db,
        include_users=True,
        include_history=True,
        exported_by=f"pre-import-auto ({admin.username})",
    )
    request.session["pre_import_backup"] = to_json(pre_backup)

    counts = await import_data(
        db,
        payload,
        import_settings=import_settings,
        import_rules=import_rules,
        import_history=import_history,
        import_users=import_users,
        overwrite_rules=overwrite_rules,
    )

    summary = (
        f"Import complete — "
        f"settings: {counts['settings']}, "
        f"rules: {counts['rules']}, "
        f"history: {counts['history']}, "
        f"users: {counts['users']} imported."
    )
    flash(request, summary, "success")
    return RedirectResponse(url="/admin/backup", status_code=303)


@router.get("/backup/pre-import-snapshot")
async def download_pre_import_snapshot(
    request: Request,
    admin: User = Depends(require_admin),
):
    """Download the automatic pre-import safety snapshot (session-stored)."""
    from fastapi.responses import Response
    from datetime import datetime, timezone

    snapshot = request.session.get("pre_import_backup")
    if not snapshot:
        flash(request, "No pre-import snapshot available in this session.", "error")
        return RedirectResponse(url="/admin/backup", status_code=303)

    filename = (
        f"pre_import_snapshot_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    )
    return Response(
        content=snapshot,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Security admin routes ─────────────────────────────────────────────────────

@router.get("/security", response_class=HTMLResponse)
async def security_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.crud.event_log import get_events, count_events, AUTH_FAILURE, ACCOUNT_LOCKED, SUSPICIOUS_ACTIVITY
    from app.crud.system_settings import get_setting
    from app.services.rate_limiter import login_tracker
    from app.core.config import get_settings
    cfg = get_settings()

    security_events = await get_events(
        db, is_admin=True,
        limit=50,
    )
    security_events = [
        e for e in security_events
        if e.event_type in (AUTH_FAILURE, ACCOUNT_LOCKED, SUSPICIOUS_ACTIVITY, "account_unlocked", "user_login")
    ][:30]

    ip_whitelist_raw = await get_setting(db, "security.ip_whitelist", default="")
    tracker_stats = login_tracker.stats()

    return templates.TemplateResponse(
        "admin/security.html",
        {
            "request": request,
            "current_user": admin,
            "flash_messages": get_flashed_messages(request),
            "csrf_token": get_csrf_token(request),
            "security_events": security_events,
            "ip_whitelist_raw": ip_whitelist_raw,
            "tracker_stats": tracker_stats,
            "cfg": {
                "max_attempts": cfg.LOGIN_MAX_ATTEMPTS,
                "window_minutes": cfg.LOGIN_WINDOW_MINUTES,
                "lockout_minutes": cfg.LOGIN_LOCKOUT_MINUTES,
                "session_idle_hours": cfg.SESSION_IDLE_TIMEOUT_HOURS,
                "session_abs_hours": cfg.SESSION_ABSOLUTE_TIMEOUT_HOURS,
                "https_only": cfg.HTTPS_ONLY,
            },
        },
    )


@router.post("/security/ip-whitelist", response_class=HTMLResponse)
async def save_ip_whitelist(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
    ip_whitelist: str = Form(""),
):
    from app.crud.system_settings import set_setting
    from app.core.ip_filter import _parse_whitelist
    # Validate entries
    from app.core.ip_filter import _parse_whitelist
    import ipaddress
    raw = ip_whitelist.strip()
    entries = _parse_whitelist(raw)
    errors = []
    for e in entries:
        try:
            if "/" in e:
                ipaddress.ip_network(e, strict=False)
            else:
                ipaddress.ip_address(e)
        except ValueError:
            errors.append(e)
    if errors:
        flash(request, f"Invalid IP/CIDR entries: {', '.join(errors)}", "error")
    else:
        await set_setting(db, "security.ip_whitelist", raw)
        # Force middleware cache refresh
        from app.main import app as _app
        for mw in _app.middleware_stack.__dict__.get("app", {}) if hasattr(_app.middleware_stack, "__dict__") else []:
            pass  # cache will naturally expire
        flash(request, "IP whitelist saved.", "success")
    return RedirectResponse(url="/admin/security", status_code=303)


@router.post("/security/unlock", response_class=HTMLResponse)
async def unlock_identifier(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    _csrf: None = Depends(csrf_protect),
    identifier: str = Form(...),
):
    from app.services.rate_limiter import login_tracker
    from app.services.event_log import event_log
    login_tracker.reset(identifier.strip())
    await event_log.account_unlocked(db, identifier=identifier.strip(), admin_user=admin.email or admin.username)
    flash(request, f'Lockout cleared for "{identifier}".', "success")
    return RedirectResponse(url="/admin/security", status_code=303)
