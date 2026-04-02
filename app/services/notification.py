"""Multi-channel notification dispatcher.

Channels:
  - email  (always attempted via email_service)
  - ntfy   (elevated/critical only — self-hosted push)
  - discord (elevated/critical only — webhook embed)
  - telegram (elevated/critical only — bot message)

Severity routing:
  normal   → email only
  elevated → email + all enabled push channels
  critical → email + all enabled push channels
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── Severity push threshold ────────────────────────────────────────────────────
PUSH_SEVERITIES = ("elevated", "critical")


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class NotificationPayload:
    trading_pair: str
    triggered_price: float
    threshold: float
    condition: str          # "above" | "below"
    severity: str           # "normal" | "elevated" | "critical"
    timestamp: datetime
    to_address: str
    username: str
    message: str            # pre-formatted body text
    rule_label: str | None = None


@dataclass
class NotificationConfig:
    ntfy_enabled: bool = False
    ntfy_server_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""

    discord_enabled: bool = False
    discord_webhook_url: str = ""

    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


# ── Config loader ──────────────────────────────────────────────────────────────

async def _load_config(db: AsyncSession) -> NotificationConfig:
    from app.crud import system_settings as ss_crud
    raw = await ss_crud.get_notification_config(db)

    def _bool(key: str) -> bool:
        return raw.get(key, "false").lower() in ("true", "1", "yes")

    def _str(key: str, default: str = "") -> str:
        return raw.get(key, default)

    return NotificationConfig(
        ntfy_enabled=_bool(ss_crud.NOTIF_NTFY_ENABLED),
        ntfy_server_url=_str(ss_crud.NOTIF_NTFY_SERVER_URL, "https://ntfy.sh"),
        ntfy_topic=_str(ss_crud.NOTIF_NTFY_TOPIC),
        ntfy_token=_str(ss_crud.NOTIF_NTFY_TOKEN),
        discord_enabled=_bool(ss_crud.NOTIF_DISCORD_ENABLED),
        discord_webhook_url=_str(ss_crud.NOTIF_DISCORD_WEBHOOK_URL),
        telegram_enabled=_bool(ss_crud.NOTIF_TELEGRAM_ENABLED),
        telegram_bot_token=_str(ss_crud.NOTIF_TELEGRAM_BOT_TOKEN),
        telegram_chat_id=_str(ss_crud.NOTIF_TELEGRAM_CHAT_ID),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

_SEVERITY_EMOJI = {
    "normal":   "🔔",
    "elevated": "🔶",
    "critical": "🚨",
}

_NTFY_PRIORITY = {
    "normal":   "3",
    "elevated": "4",
    "critical": "5",
}

_DISCORD_COLOUR = {
    "normal":   0x27AE60,   # green
    "elevated": 0xF39C12,   # orange
    "critical": 0xE74C3C,   # red
}


def _make_title(payload: NotificationPayload) -> str:
    emoji = _SEVERITY_EMOJI.get(payload.severity, "🔔")
    direction = "▲" if payload.condition == "above" else "▼"
    return f"{emoji} {payload.trading_pair} {direction} ${payload.triggered_price:,.2f}"


def _make_short_body(payload: NotificationPayload) -> str:
    direction = "above" if payload.condition == "above" else "below"
    return (
        f"{payload.trading_pair} crossed {direction} ${payload.threshold:,.2f}\n"
        f"Current: ${payload.triggered_price:,.2f} | Severity: {payload.severity.upper()}\n"
        f"{payload.timestamp.strftime('%Y-%m-%d %H:%M UTC')}"
    )


# ── Channel senders ────────────────────────────────────────────────────────────

async def send_ntfy(payload: NotificationPayload, config: NotificationConfig) -> bool:
    if not (config.ntfy_server_url and config.ntfy_topic):
        logger.warning("ntfy: missing server_url or topic — skipping")
        return False

    url = f"{config.ntfy_server_url.rstrip('/')}/{config.ntfy_topic}"
    headers: dict[str, str] = {
        "Title":    _make_title(payload),
        "Priority": _NTFY_PRIORITY.get(payload.severity, "3"),
        "Tags":     f"bell,crypto,{payload.severity}",
    }
    if config.ntfy_token:
        headers["Authorization"] = f"Bearer {config.ntfy_token}"

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, content=_make_short_body(payload), headers=headers)
        resp.raise_for_status()

    logger.info(f"ntfy: sent to {config.ntfy_server_url}/{config.ntfy_topic}")
    return True


async def send_discord(payload: NotificationPayload, config: NotificationConfig) -> bool:
    if not config.discord_webhook_url:
        logger.warning("Discord: missing webhook_url — skipping")
        return False

    direction = "above ▲" if payload.condition == "above" else "below ▼"
    colour = _DISCORD_COLOUR.get(payload.severity, 0x27AE60)
    ts = payload.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

    embed = {
        "title":       _make_title(payload),
        "description": payload.message,
        "color":       colour,
        "fields": [
            {"name": "Asset",           "value": payload.trading_pair,                   "inline": True},
            {"name": "Price (USD)",     "value": f"${payload.triggered_price:,.2f}",     "inline": True},
            {"name": "Threshold",       "value": f"{direction} ${payload.threshold:,.2f}", "inline": True},
            {"name": "Severity",        "value": payload.severity.upper(),               "inline": True},
            {"name": "Time (UTC)",      "value": ts,                                     "inline": True},
        ],
        "footer": {"text": "Crypto Alert System"},
    }
    data = {"username": "Crypto Alerts", "embeds": [embed]}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(config.discord_webhook_url, json=data)
        resp.raise_for_status()

    logger.info("Discord: notification sent")
    return True


async def send_telegram(payload: NotificationPayload, config: NotificationConfig) -> bool:
    if not (config.telegram_bot_token and config.telegram_chat_id):
        logger.warning("Telegram: missing bot_token or chat_id — skipping")
        return False

    direction = "above ▲" if payload.condition == "above" else "below ▼"
    emoji = _SEVERITY_EMOJI.get(payload.severity, "🔔")
    ts = payload.timestamp.strftime("%H:%M UTC")

    text = (
        f"{emoji} <b>{payload.trading_pair} Alert</b>\n\n"
        f"<b>Price:</b> <code>${payload.triggered_price:,.2f}</code>\n"
        f"<b>Threshold:</b> {direction} <code>${payload.threshold:,.2f}</code>\n"
        f"<b>Severity:</b> {payload.severity.upper()}\n"
        f"<b>Time:</b> {ts}\n\n"
        f"{payload.message}"
    )

    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    data = {
        "chat_id":    config.telegram_chat_id,
        "text":       text,
        "parse_mode": "HTML",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=data)
        resp.raise_for_status()

    logger.info(f"Telegram: notification sent to chat {config.telegram_chat_id}")
    return True


# ── Test helpers (load config from DB, send a synthetic alert) ─────────────────

async def test_ntfy(db: AsyncSession) -> tuple[bool, str]:
    config = await _load_config(db)
    if not config.ntfy_enabled:
        return False, "ntfy is not enabled"
    payload = _test_payload()
    try:
        ok = await send_ntfy(payload, config)
        return ok, "Test message sent" if ok else "Send returned False"
    except Exception as exc:
        return False, str(exc)


async def test_discord(db: AsyncSession) -> tuple[bool, str]:
    config = await _load_config(db)
    if not config.discord_enabled:
        return False, "Discord is not enabled"
    payload = _test_payload()
    try:
        ok = await send_discord(payload, config)
        return ok, "Test message sent" if ok else "Send returned False"
    except Exception as exc:
        return False, str(exc)


async def test_telegram(db: AsyncSession) -> tuple[bool, str]:
    config = await _load_config(db)
    if not config.telegram_enabled:
        return False, "Telegram is not enabled"
    payload = _test_payload()
    try:
        ok = await send_telegram(payload, config)
        return ok, "Test message sent" if ok else "Send returned False"
    except Exception as exc:
        return False, str(exc)


def _test_payload() -> NotificationPayload:
    return NotificationPayload(
        trading_pair="BTC/USD",
        triggered_price=99_999.00,
        threshold=100_000.00,
        condition="above",
        severity="elevated",
        timestamp=datetime.now(timezone.utc),
        to_address="",
        username="test",
        message="This is a test notification from your Crypto Alert System.",
    )


# ── Dispatcher ─────────────────────────────────────────────────────────────────

class NotificationDispatcher:
    """Route an alert to all configured channels based on severity."""

    async def send_alert(
        self,
        db: AsyncSession,
        *,
        to_address: str,
        username: str,
        trading_pair: str,
        condition: str,
        threshold: float,
        triggered_price: float,
        message: str,
        timestamp: datetime,
        severity: str = "normal",
        rule_label: str | None = None,
    ) -> dict[str, bool]:
        """
        Dispatch notification to all appropriate channels.
        Returns a dict of channel → success bool.
        A failed channel never raises; failures are logged.
        """
        results: dict[str, bool] = {}

        payload = NotificationPayload(
            trading_pair=trading_pair,
            triggered_price=triggered_price,
            threshold=threshold,
            condition=condition,
            severity=severity,
            timestamp=timestamp,
            to_address=to_address,
            username=username,
            message=message,
            rule_label=rule_label,
        )

        # Email — always attempted
        if to_address:
            try:
                from app.services.email_service import email_service
                ok = await email_service.send_alert_email(
                    db,
                    to_address=to_address,
                    username=username,
                    trading_pair=trading_pair,
                    condition=condition,
                    threshold=threshold,
                    triggered_price=triggered_price,
                    message=message,
                    timestamp=timestamp,
                    severity=severity,
                )
                results["email"] = ok
            except Exception as exc:
                logger.error(f"Email notification failed: {exc}")
                results["email"] = False
        else:
            logger.warning(f"No email address for user '{username}' — skipping email channel")

        # Push channels — elevated & critical only
        if severity in PUSH_SEVERITIES:
            config = await _load_config(db)

            if config.ntfy_enabled:
                try:
                    results["ntfy"] = await send_ntfy(payload, config)
                except Exception as exc:
                    logger.error(f"ntfy notification failed: {exc}")
                    results["ntfy"] = False

            if config.discord_enabled:
                try:
                    results["discord"] = await send_discord(payload, config)
                except Exception as exc:
                    logger.error(f"Discord notification failed: {exc}")
                    results["discord"] = False

            if config.telegram_enabled:
                try:
                    results["telegram"] = await send_telegram(payload, config)
                except Exception as exc:
                    logger.error(f"Telegram notification failed: {exc}")
                    results["telegram"] = False

        return results


notification_dispatcher = NotificationDispatcher()
