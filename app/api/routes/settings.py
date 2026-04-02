from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import csrf_protect, get_current_user, require_admin, require_login
from app.core.database import get_db
from app.core.session import flash, get_csrf_token, get_flashed_messages
from app.crud import system_settings as ss_crud
from app.models.user import User
from app.services.email_service import email_service

router = APIRouter(prefix="/settings", tags=["settings"])
templates = Jinja2Templates(directory="app/templates")


def _ctx(request: Request, user: User, **extra) -> dict:
    return {
        "request": request,
        "user": user,
        "flash_messages": get_flashed_messages(request),
        "csrf_token": get_csrf_token(request),
        **extra,
    }


# ── User settings ─────────────────────────────────────────────────────────────

POLL_INTERVAL_KEY = "poll_interval_seconds"


@router.get("/", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    current_user: User = Depends(require_login),
    db: AsyncSession = Depends(get_db),
):
    from app.core.config import get_settings
    cfg = get_settings()
    poll_val = await ss_crud.get(db, POLL_INTERVAL_KEY)
    poll_interval = int(poll_val) if poll_val else cfg.KRAKEN_POLL_INTERVAL_SECONDS
    return templates.TemplateResponse(
        "settings/index.html",
        _ctx(request, current_user, poll_interval=poll_interval),
    )


# ── SMTP settings (admin only) ────────────────────────────────────────────────

@router.get("/smtp", response_class=HTMLResponse)
async def smtp_settings_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    smtp_cfg = await ss_crud.get_smtp_config_from_db(db)
    from app.core.config import get_settings
    from app.crud.system_settings import (
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
        SMTP_FROM_NAME, SMTP_SECURITY_MODE, SMTP_TIMEOUT,
        SMTP_LAST_TEST_STATUS, SMTP_LAST_TEST_MESSAGE, SMTP_LAST_TEST_OK, SMTP_LAST_SEND_OK,
    )
    cfg = get_settings()

    # Determine effective security mode (with legacy migration)
    mode = smtp_cfg.get(SMTP_SECURITY_MODE, "starttls" if cfg.SMTP_TLS else "none")

    current = {
        "host":          smtp_cfg.get(SMTP_HOST, cfg.SMTP_HOST),
        "port":          smtp_cfg.get(SMTP_PORT, str(cfg.SMTP_PORT)),
        "user":          smtp_cfg.get(SMTP_USER, cfg.SMTP_USER),
        "has_password":  bool(smtp_cfg.get(SMTP_PASSWORD) or cfg.SMTP_PASSWORD),
        "from_addr":     smtp_cfg.get(SMTP_FROM, cfg.SMTP_FROM),
        "from_name":     smtp_cfg.get(SMTP_FROM_NAME, ""),
        "security_mode": mode,
        "timeout":       smtp_cfg.get(SMTP_TIMEOUT, "15"),
    }
    diag = {
        "last_test_status":  smtp_cfg.get(SMTP_LAST_TEST_STATUS, ""),
        "last_test_message": smtp_cfg.get(SMTP_LAST_TEST_MESSAGE, ""),
        "last_test_ok":      smtp_cfg.get(SMTP_LAST_TEST_OK, ""),
        "last_send_ok":      smtp_cfg.get(SMTP_LAST_SEND_OK, ""),
    }
    return templates.TemplateResponse(
        "settings/smtp.html",
        _ctx(request, admin, smtp=current, diag=diag),
    )


@router.post("/smtp", response_class=HTMLResponse)
async def save_smtp_settings(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    smtp_from_name: str = Form(""),
    smtp_security_mode: str = Form("starttls"),
    smtp_timeout: str = Form("15"),
    db: AsyncSession = Depends(get_db),
):
    from app.crud.system_settings import (
        SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
        SMTP_FROM_NAME, SMTP_SECURITY_MODE, SMTP_TIMEOUT,
    )
    from app.services.email_service import VALID_MODES, MODE_STARTTLS

    # Validate inputs
    errors = []
    port_val = smtp_port.strip()
    try:
        port_int = int(port_val)
        if not (1 <= port_int <= 65535):
            raise ValueError
    except ValueError:
        errors.append("Port must be an integer between 1 and 65535.")
        port_val = "587"

    mode = smtp_security_mode.strip().lower()
    if mode not in VALID_MODES:
        errors.append(f"Security mode must be one of: {', '.join(sorted(VALID_MODES))}.")
        mode = MODE_STARTTLS

    from_addr = smtp_from.strip()
    if from_addr:
        import re
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", from_addr):
            errors.append("From address must be a valid email address.")

    try:
        timeout_int = max(5, min(60, int(smtp_timeout.strip() or "15")))
    except ValueError:
        timeout_int = 15

    if errors:
        for e in errors:
            flash(request, e, "error")
        return RedirectResponse(url="/settings/smtp", status_code=303)

    # Build config dict — only update password if a new one was provided
    config = {
        SMTP_HOST:          smtp_host.strip(),
        SMTP_PORT:          port_val,
        SMTP_USER:          smtp_user.strip(),
        SMTP_FROM:          from_addr,
        SMTP_FROM_NAME:     smtp_from_name.strip(),
        SMTP_SECURITY_MODE: mode,
        SMTP_TIMEOUT:       str(timeout_int),
    }
    if smtp_password:  # blank = keep existing
        config[SMTP_PASSWORD] = smtp_password

    await ss_crud.save_smtp_config(db, config)
    flash(request, "SMTP settings saved.", "success")
    return RedirectResponse(url="/settings/smtp", status_code=303)


@router.post("/test-email")
async def send_test_email(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    to_address: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    recipient = to_address.strip() or admin.email
    ok, msg = await email_service.send_test(db, to_address=recipient)
    # Flash each line separately for multi-line diagnostics
    for line in msg.split("\n"):
        line = line.strip()
        if line:
            flash(request, line, "success" if ok else "error")
    return RedirectResponse(url="/settings/smtp", status_code=303)


@router.post("/validate-smtp")
async def validate_smtp(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    ok, msg = await email_service.validate_config(db)
    for line in msg.split("\n"):
        line = line.strip()
        if line:
            flash(request, line, "success" if ok else "error")
    return RedirectResponse(url="/settings/smtp", status_code=303)



# ── Poll interval (admin only) ────────────────────────────────────────────────

@router.post("/poll-interval")
async def save_poll_interval(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    poll_interval: int = Form(30),
    db: AsyncSession = Depends(get_db),
):
    poll_interval = max(10, min(3600, poll_interval))
    await ss_crud.set_value(db, POLL_INTERVAL_KEY, str(poll_interval))
    flash(request, f"Poll interval saved: {poll_interval}s.", "success")
    return RedirectResponse(url="/settings/", status_code=303)


# ── Notification channel settings (admin only) ────────────────────────────────

@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    raw = await ss_crud.get_notification_config(db)

    def _s(key: str, default: str = "") -> str:
        return raw.get(key, default)

    notif = {
        "ntfy_enabled":      _s(ss_crud.NOTIF_NTFY_ENABLED, "false"),
        "ntfy_server_url":   _s(ss_crud.NOTIF_NTFY_SERVER_URL, "https://ntfy.sh"),
        "ntfy_topic":        _s(ss_crud.NOTIF_NTFY_TOPIC),
        "ntfy_token":        _s(ss_crud.NOTIF_NTFY_TOKEN),
        "discord_enabled":   _s(ss_crud.NOTIF_DISCORD_ENABLED, "false"),
        "discord_webhook_url": _s(ss_crud.NOTIF_DISCORD_WEBHOOK_URL),
        "telegram_enabled":  _s(ss_crud.NOTIF_TELEGRAM_ENABLED, "false"),
        "telegram_bot_token": _s(ss_crud.NOTIF_TELEGRAM_BOT_TOKEN),
        "telegram_chat_id":  _s(ss_crud.NOTIF_TELEGRAM_CHAT_ID),
    }
    return templates.TemplateResponse(
        "settings/notifications.html",
        _ctx(request, admin, notif=notif),
    )


@router.post("/notifications/ntfy")
async def save_ntfy_settings(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    ntfy_enabled: str = Form("false"),
    ntfy_server_url: str = Form("https://ntfy.sh"),
    ntfy_topic: str = Form(""),
    ntfy_token: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await ss_crud.save_notification_config(db, {
        ss_crud.NOTIF_NTFY_ENABLED:    ntfy_enabled,
        ss_crud.NOTIF_NTFY_SERVER_URL: ntfy_server_url.strip(),
        ss_crud.NOTIF_NTFY_TOPIC:      ntfy_topic.strip(),
        ss_crud.NOTIF_NTFY_TOKEN:      ntfy_token.strip(),
    })
    flash(request, "ntfy settings saved.", "success")
    return RedirectResponse(url="/settings/notifications", status_code=303)


@router.post("/notifications/discord")
async def save_discord_settings(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    discord_enabled: str = Form("false"),
    discord_webhook_url: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await ss_crud.save_notification_config(db, {
        ss_crud.NOTIF_DISCORD_ENABLED:     discord_enabled,
        ss_crud.NOTIF_DISCORD_WEBHOOK_URL: discord_webhook_url.strip(),
    })
    flash(request, "Discord settings saved.", "success")
    return RedirectResponse(url="/settings/notifications", status_code=303)


@router.post("/notifications/telegram")
async def save_telegram_settings(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    telegram_enabled: str = Form("false"),
    telegram_bot_token: str = Form(""),
    telegram_chat_id: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    await ss_crud.save_notification_config(db, {
        ss_crud.NOTIF_TELEGRAM_ENABLED:    telegram_enabled,
        ss_crud.NOTIF_TELEGRAM_BOT_TOKEN:  telegram_bot_token.strip(),
        ss_crud.NOTIF_TELEGRAM_CHAT_ID:    telegram_chat_id.strip(),
    })
    flash(request, "Telegram settings saved.", "success")
    return RedirectResponse(url="/settings/notifications", status_code=303)


@router.post("/notifications/test/ntfy")
async def test_ntfy_channel(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.notification import test_ntfy
    ok, msg = await test_ntfy(db)
    flash(request, f"ntfy test: {msg}", "success" if ok else "error")
    return RedirectResponse(url="/settings/notifications", status_code=303)


@router.post("/notifications/test/discord")
async def test_discord_channel(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.notification import test_discord
    ok, msg = await test_discord(db)
    flash(request, f"Discord test: {msg}", "success" if ok else "error")
    return RedirectResponse(url="/settings/notifications", status_code=303)


@router.post("/notifications/test/telegram")
async def test_telegram_channel(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    from app.services.notification import test_telegram
    ok, msg = await test_telegram(db)
    flash(request, f"Telegram test: {msg}", "success" if ok else "error")
    return RedirectResponse(url="/settings/notifications", status_code=303)
