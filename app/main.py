from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.api.deps import CSRFError, RequiresLoginException
from app.api.routes import auth, alerts, admin, dashboard
from app.api.routes import settings as settings_router
from app.api.routes import assets as assets_router
from app.api.routes import market_state as market_state_router
from app.api.routes import simulation as simulation_router
from app.api.routes import events as events_router
from app.api.routes import ticker as ticker_router
from app.core.config import get_settings
from app.core.ip_filter import IPWhitelistMiddleware
from app.core.database import AsyncSessionLocal, Base, engine
from app.core.logging import setup_logging
from app.core.session import flash
from app.crud import user as user_crud
from app.models.user import UserRole
from app.models import price_history as _price_history_model  # noqa: F401 — registers table
from app.models import market_state as _market_state_model    # noqa: F401 — registers table
from app.models import system_event as _system_event_model    # noqa: F401 — registers table
from app.models import featured_asset as _featured_asset_model  # noqa: F401 — registers table
from app.models import user_ticker_asset as _user_ticker_asset_model  # noqa: F401 — registers table
from app.services.kraken_assets import kraken_assets_service
from app.services.market_data import market_data_service
from app.services.scheduler import scheduler

setup_logging()
config = get_settings()


async def _init_db() -> None:
    """Create all tables (idempotent) and seed the first admin if configured."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Idempotent column additions for schema evolution (no Alembic)
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE alert_history "
            "ADD COLUMN IF NOT EXISTS severity VARCHAR(20) NOT NULL DEFAULT 'normal'"
        ))
        # Step 11: time-based alert filtering columns
        for stmt in [
            "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS time_filter_enabled BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS active_hours_start TIME",
            "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS active_hours_end TIME",
            "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS active_timezone VARCHAR(64) NOT NULL DEFAULT 'UTC'",
            "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS critical_override BOOLEAN NOT NULL DEFAULT false",
        ]:
            await conn.execute(text(stmt))

    if config.FIRST_ADMIN_EMAIL and config.FIRST_ADMIN_PASSWORD:
        async with AsyncSessionLocal() as db:
            if await user_crud.count(db) == 0:
                admin_user = await user_crud.create(
                    db,
                    email=config.FIRST_ADMIN_EMAIL,
                    username=config.FIRST_ADMIN_USERNAME,
                    password=config.FIRST_ADMIN_PASSWORD,
                    role=UserRole.ADMIN,
                )
                from app.core.logging import logger
                logger.info(f"First admin created: {admin_user.email}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _init_db()
    await kraken_assets_service.start()
    await market_data_service.start()
    await scheduler.start()
    # Log system startup event
    async with AsyncSessionLocal() as db:
        from app.services.event_log import event_log
        await event_log.system_startup(db)
    # Seed default featured assets if the table is empty
    async with AsyncSessionLocal() as db:
        from app.services.featured_assets import featured_assets_service
        await featured_assets_service.ensure_seeded(db)
    yield
    await scheduler.stop()
    await market_data_service.stop()
    await kraken_assets_service.stop()


app = FastAPI(
    title=config.APP_NAME,
    version=config.APP_VERSION,
    debug=config.DEBUG,
    lifespan=lifespan,
)

# ── Middleware ────────────────────────────────────────────────────────────────

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SECRET_KEY,
    max_age=config.SESSION_MAX_AGE,
    https_only=config.HTTPS_ONLY,
    same_site="lax",
)

app.add_middleware(IPWhitelistMiddleware, db_factory=AsyncSessionLocal)

# ── Exception handlers ────────────────────────────────────────────────────────

@app.exception_handler(RequiresLoginException)
async def requires_login_handler(request: Request, exc: RequiresLoginException) -> RedirectResponse:
    flash(request, "Please log in to continue.", "info")
    return RedirectResponse(url=f"/auth/login?next={exc.next_url}", status_code=303)


@app.exception_handler(CSRFError)
async def csrf_error_handler(request: Request, exc: CSRFError) -> HTMLResponse:
    templates = Jinja2Templates(directory="app/templates")
    return templates.TemplateResponse(
        "errors/403.html",
        {"request": request, "detail": "CSRF validation failed. Please try again."},
        status_code=403,
    )

# ── Static files & templates ──────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(alerts.router)
app.include_router(admin.router)
app.include_router(settings_router.router)
app.include_router(assets_router.router)
app.include_router(market_state_router.router)
app.include_router(simulation_router.router)
app.include_router(events_router.router)
app.include_router(ticker_router.router)


# ── Core endpoints ────────────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "version": config.APP_VERSION}


@app.get("/", tags=["system"])
async def root():
    return RedirectResponse(url="/dashboard", status_code=302)

