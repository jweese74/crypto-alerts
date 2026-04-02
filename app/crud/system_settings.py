from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSettings

# ── SMTP setting keys ─────────────────────────────────────────────────────────
SMTP_HOST          = "smtp.host"
SMTP_PORT          = "smtp.port"
SMTP_USER          = "smtp.user"
SMTP_PASSWORD      = "smtp.password"
SMTP_FROM          = "smtp.from"
SMTP_FROM_NAME     = "smtp.from_name"
SMTP_SECURITY_MODE = "smtp.security_mode"   # "none" | "ssl" | "starttls"
SMTP_TIMEOUT       = "smtp.timeout"
# Legacy key (kept for migration, no longer the primary field)
_SMTP_TLS_LEGACY   = "smtp.tls"

SMTP_KEYS = [
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM, SMTP_FROM_NAME, SMTP_SECURITY_MODE, SMTP_TIMEOUT,
    _SMTP_TLS_LEGACY,
]

# Tracking keys (not shown in UI form but written by the app)
SMTP_LAST_TEST_OK        = "smtp.last_test_ok"       # ISO timestamp or ""
SMTP_LAST_TEST_STATUS    = "smtp.last_test_status"   # "ok" | "failed" | ""
SMTP_LAST_TEST_MESSAGE   = "smtp.last_test_message"  # human string
SMTP_LAST_SEND_OK        = "smtp.last_send_ok"       # ISO timestamp of last alert sent


async def get(db: AsyncSession, key: str) -> Optional[str]:
    result = await db.execute(select(SystemSettings).where(SystemSettings.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else None


# Alias with default support (used by ip_filter and other callers)
async def get_setting(db: AsyncSession, key: str, *, default: str = "") -> str:
    val = await get(db, key)
    return val if val is not None else default


async def set_setting(db: AsyncSession, key: str, value: str, description: Optional[str] = None) -> None:
    await set_value(db, key, value, description)


async def set_value(
    db: AsyncSession,
    key: str,
    value: str,
    description: Optional[str] = None,
) -> SystemSettings:
    result = await db.execute(select(SystemSettings).where(SystemSettings.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
        if description is not None:
            setting.description = description
    else:
        setting = SystemSettings(key=key, value=value, description=description)
        db.add(setting)
    await db.commit()
    await db.refresh(setting)
    return setting


async def get_all(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(select(SystemSettings))
    return {row.key: row.value for row in result.scalars().all()}


async def get_smtp_config_from_db(db: AsyncSession) -> dict[str, str]:
    """
    Return SMTP-related settings from the DB.

    Migration: if ``smtp.security_mode`` is absent but the old ``smtp.tls``
    boolean is present, derive the mode automatically so old installs keep
    working without a manual data migration.
    """
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key.in_(SMTP_KEYS))
    )
    rows = {row.key: row.value for row in result.scalars().all()}

    # ── Migrate legacy smtp.tls → smtp.security_mode ──────────────────────
    if SMTP_SECURITY_MODE not in rows and _SMTP_TLS_LEGACY in rows:
        rows[SMTP_SECURITY_MODE] = (
            "starttls" if rows[_SMTP_TLS_LEGACY].lower() in ("true", "1", "yes") else "none"
        )

    return rows


async def save_smtp_config(db: AsyncSession, config: dict[str, str]) -> None:
    """Persist a dict of {smtp_key: value} entries."""
    descriptions = {
        SMTP_HOST:          "SMTP server hostname",
        SMTP_PORT:          "SMTP server port",
        SMTP_USER:          "SMTP username / login",
        SMTP_PASSWORD:      "SMTP password",
        SMTP_FROM:          "Sender address shown in emails",
        SMTP_FROM_NAME:     "Sender display name (optional)",
        SMTP_SECURITY_MODE: "Security mode: none | ssl | starttls",
        SMTP_TIMEOUT:       "Connection timeout in seconds",
        SMTP_LAST_TEST_OK:       "Timestamp of last successful SMTP test",
        SMTP_LAST_TEST_STATUS:   "Last SMTP test result: ok | failed",
        SMTP_LAST_TEST_MESSAGE:  "Last SMTP test diagnostic message",
        SMTP_LAST_SEND_OK:       "Timestamp of last successful alert email",
    }
    all_allowed = set(SMTP_KEYS) | {
        SMTP_LAST_TEST_OK, SMTP_LAST_TEST_STATUS,
        SMTP_LAST_TEST_MESSAGE, SMTP_LAST_SEND_OK,
    }
    for key, value in config.items():
        if key in all_allowed:
            await set_value(db, key, value, descriptions.get(key))


# ── Notification channel setting keys ─────────────────────────────────────────

NOTIF_NTFY_ENABLED     = "notif.ntfy.enabled"
NOTIF_NTFY_SERVER_URL  = "notif.ntfy.server_url"
NOTIF_NTFY_TOPIC       = "notif.ntfy.topic"
NOTIF_NTFY_TOKEN       = "notif.ntfy.token"

NOTIF_DISCORD_ENABLED     = "notif.discord.enabled"
NOTIF_DISCORD_WEBHOOK_URL = "notif.discord.webhook_url"

NOTIF_TELEGRAM_ENABLED   = "notif.telegram.enabled"
NOTIF_TELEGRAM_BOT_TOKEN = "notif.telegram.bot_token"
NOTIF_TELEGRAM_CHAT_ID   = "notif.telegram.chat_id"

NOTIF_KEYS = [
    NOTIF_NTFY_ENABLED, NOTIF_NTFY_SERVER_URL, NOTIF_NTFY_TOPIC, NOTIF_NTFY_TOKEN,
    NOTIF_DISCORD_ENABLED, NOTIF_DISCORD_WEBHOOK_URL,
    NOTIF_TELEGRAM_ENABLED, NOTIF_TELEGRAM_BOT_TOKEN, NOTIF_TELEGRAM_CHAT_ID,
]


async def get_notification_config(db: AsyncSession) -> dict[str, str]:
    """Return all notification-channel settings from the DB."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key.in_(NOTIF_KEYS))
    )
    return {row.key: row.value for row in result.scalars().all()}


async def save_notification_config(db: AsyncSession, config: dict[str, str]) -> None:

    """Persist a dict of {notif_key: value} entries."""
    descriptions = {
        NOTIF_NTFY_ENABLED:     "Enable ntfy push notifications",
        NOTIF_NTFY_SERVER_URL:  "ntfy server URL (e.g. https://ntfy.sh)",
        NOTIF_NTFY_TOPIC:       "ntfy topic name",
        NOTIF_NTFY_TOKEN:       "ntfy access token (optional)",
        NOTIF_DISCORD_ENABLED:     "Enable Discord webhook notifications",
        NOTIF_DISCORD_WEBHOOK_URL: "Discord incoming webhook URL",
        NOTIF_TELEGRAM_ENABLED:    "Enable Telegram bot notifications",
        NOTIF_TELEGRAM_BOT_TOKEN:  "Telegram bot API token",
        NOTIF_TELEGRAM_CHAT_ID:    "Telegram chat/channel ID",
    }
    for key, value in config.items():
        if key in NOTIF_KEYS:
            await set_value(db, key, value, descriptions.get(key))


# ── Retention setting keys ─────────────────────────────────────────────────────

RETENTION_PRICE_ENABLED      = "retention.price_history_enabled"
RETENTION_PRICE_DAYS         = "retention.price_history_days"
RETENTION_ALERT_ENABLED      = "retention.alert_history_enabled"
RETENTION_ALERT_DAYS         = "retention.alert_history_days"
RETENTION_LAST_RUN           = "retention.last_run"
RETENTION_LAST_PRICE_DELETED = "retention.last_price_deleted"
RETENTION_LAST_ALERT_DELETED = "retention.last_alert_deleted"

RETENTION_KEYS = [
    RETENTION_PRICE_ENABLED, RETENTION_PRICE_DAYS,
    RETENTION_ALERT_ENABLED, RETENTION_ALERT_DAYS,
    RETENTION_LAST_RUN, RETENTION_LAST_PRICE_DELETED, RETENTION_LAST_ALERT_DELETED,
]

_RETENTION_DEFAULTS: dict[str, str] = {
    RETENTION_PRICE_ENABLED:      "true",
    RETENTION_PRICE_DAYS:         "90",
    RETENTION_ALERT_ENABLED:      "false",
    RETENTION_ALERT_DAYS:         "365",
}


async def get_retention_config(db: AsyncSession) -> dict[str, str]:
    """Return retention settings from DB, falling back to safe defaults."""
    result = await db.execute(
        select(SystemSettings).where(SystemSettings.key.in_(RETENTION_KEYS))
    )
    stored = {row.key: row.value for row in result.scalars().all()}
    # Merge with defaults so callers always get a complete config
    return {**_RETENTION_DEFAULTS, **stored}


async def save_retention_config(db: AsyncSession, config: dict[str, str]) -> None:
    """Persist a dict of {retention_key: value} entries."""
    descriptions = {
        RETENTION_PRICE_ENABLED:      "Auto-prune old price history rows",
        RETENTION_PRICE_DAYS:         "Retain price history for this many days",
        RETENTION_ALERT_ENABLED:      "Auto-prune old alert history rows",
        RETENTION_ALERT_DAYS:         "Retain alert history for this many days",
    }
    for key, value in config.items():
        if key in RETENTION_KEYS:
            await set_value(db, key, value, descriptions.get(key))
