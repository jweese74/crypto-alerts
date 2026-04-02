from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import csrf_protect, get_current_user, require_login
from app.core.config import get_settings
from app.core.database import get_db
from app.core.session import flash, get_csrf_token, get_flashed_messages, set_session_user, clear_session
from app.crud import user as user_crud
from app.models.user import User
from app.services.rate_limiter import login_tracker

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")
settings = get_settings()


def _get_client_ip(request: Request) -> str:
    """Extract real client IP, respecting X-Forwarded-For if present."""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _ctx(request: Request, user: Optional[User] = None, **extra) -> dict:
    return {
        "request": request,
        "user": user,
        "flash_messages": get_flashed_messages(request),
        "csrf_token": get_csrf_token(request),
        "registration_enabled": settings.REGISTRATION_ENABLED,
        "locked": False,
        **extra,
    }


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: str = "/dashboard",
    current_user: Optional[User] = Depends(get_current_user),
):
    if current_user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("auth/login.html", _ctx(request, next=next))


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    login_field: str = Form(..., alias="login"),
    password: str = Form(...),
    next: str = Form("/dashboard"),
    db: AsyncSession = Depends(get_db),
):
    from app.services.event_log import event_log

    client_ip = _get_client_ip(request)
    # Sanitize inputs
    login_field = login_field.strip()[:255]

    # ── Rate limit check (by IP and by login identifier) ──────────────────
    ip_locked = login_tracker.is_locked(client_ip)
    login_locked = login_tracker.is_locked(login_field.lower())
    if ip_locked or login_locked:
        locked_key = client_ip if ip_locked else login_field.lower()
        secs = login_tracker.seconds_remaining(locked_key)
        mins = max(1, secs // 60)
        await event_log.suspicious_activity(
            db,
            description=f'Login attempt while locked out for "{login_field}"',
            ip=client_ip,
        )
        flash(
            request,
            f"Too many failed attempts. Try again in {mins} minute{'s' if mins != 1 else ''}.",
            "error",
        )
        return templates.TemplateResponse(
            "auth/login.html",
            _ctx(request, next=next, login_value=login_field, locked=True),
            status_code=429,
        )

    user = await user_crud.authenticate(db, login_field, password)

    if not user:
        # Record failure against both IP and login identifier
        ip_just_locked = login_tracker.record_failure(client_ip)
        id_just_locked = login_tracker.record_failure(login_field.lower())

        await event_log.auth_failure(
            db, login=login_field, ip=client_ip, reason="invalid credentials"
        )
        if ip_just_locked or id_just_locked:
            locked_key = client_ip if ip_just_locked else login_field.lower()
            mins = settings.LOGIN_LOCKOUT_MINUTES
            await event_log.account_locked(
                db,
                identifier=locked_key,
                lockout_minutes=mins,
                ip=client_ip,
            )
            flash(
                request,
                f"Too many failed attempts. Account locked for {mins} minutes.",
                "error",
            )
        else:
            remaining = settings.LOGIN_MAX_ATTEMPTS - login_tracker.failure_count(login_field.lower())
            flash(request, "Invalid credentials. Please try again.", "error")
            if remaining <= 2:
                flash(
                    request,
                    f"Warning: {remaining} attempt{'s' if remaining != 1 else ''} remaining before lockout.",
                    "warning",
                )
        return templates.TemplateResponse(
            "auth/login.html",
            _ctx(request, next=next, login_value=login_field),
            status_code=401,
        )

    if not user.is_active:
        await event_log.auth_failure(
            db, login=login_field, ip=client_ip, reason="account disabled"
        )
        flash(request, "Your account has been disabled. Contact an administrator.", "error")
        return templates.TemplateResponse("auth/login.html", _ctx(request, next=next), status_code=403)

    # ── Successful login ───────────────────────────────────────────────────
    login_tracker.record_success(client_ip)
    login_tracker.record_success(login_field.lower())

    set_session_user(request, str(user.id))
    await event_log.user_login(db, username=user.email or user.username, user_id=str(user.id))
    # Sanitize redirect target — only allow relative paths
    safe_next = next if next.startswith("/") and not next.startswith("//") else "/dashboard"
    return RedirectResponse(url=safe_next, status_code=303)


@router.post("/logout")
async def logout(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    current_user: User = Depends(require_login),
):
    clear_session(request)
    return RedirectResponse(url="/auth/login", status_code=303)


@router.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    current_user: Optional[User] = Depends(get_current_user),
):
    if not settings.REGISTRATION_ENABLED:
        flash(request, "Registration is currently disabled.", "error")
        return RedirectResponse(url="/auth/login", status_code=302)
    if current_user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse("auth/register.html", _ctx(request))


@router.post("/register", response_class=HTMLResponse)
async def register(
    request: Request,
    _csrf: None = Depends(csrf_protect),
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    if not settings.REGISTRATION_ENABLED:
        return RedirectResponse(url="/auth/login", status_code=303)

    errors: list[str] = []

    if password != confirm_password:
        errors.append("Passwords do not match.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if not any(c.isdigit() for c in password) or not any(c.isalpha() for c in password):
        errors.append("Password must contain letters and digits.")

    if not errors:
        if await user_crud.get_by_email(db, email):
            errors.append("An account with that email already exists.")
        if await user_crud.get_by_username(db, username):
            errors.append("That username is already taken.")

    if errors:
        for e in errors:
            flash(request, e, "error")
        return templates.TemplateResponse(
            "auth/register.html",
            _ctx(request, email_value=email, username_value=username),
            status_code=422,
        )

    user = await user_crud.create(db, email=email, username=username, password=password)
    set_session_user(request, str(user.id))
    flash(request, f"Welcome, {user.username}! Your account has been created.", "success")
    return RedirectResponse(url="/dashboard", status_code=303)

