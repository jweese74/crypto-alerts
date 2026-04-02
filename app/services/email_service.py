"""
Email (SMTP) notification service.

Security modes
--------------
ssl      — Implicit TLS, wraps TCP in TLS from the start.  Use port 465.
           aiosmtplib: use_tls=True, start_tls=False
starttls — Plain TCP connect, EHLO, then upgrade with STARTTLS.  Use port 587.
           aiosmtplib: use_tls=False, start_tls=True
none     — Plain SMTP, no encryption.  Use port 25 or 587.
           aiosmtplib: use_tls=False, start_tls=False

Config priority: DB (system_settings table) > .env / Settings object

Retry policy: up to MAX_RETRIES attempts with exponential back-off.
"""
import asyncio
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

import aiosmtplib

from app.core.config import get_settings
from app.core.logging import logger

_settings = get_settings()

MAX_RETRIES = 3
RETRY_BASE_DELAY = 2  # seconds; doubles on each retry

# Valid security mode values
MODE_NONE     = "none"
MODE_SSL      = "ssl"
MODE_STARTTLS = "starttls"
VALID_MODES   = {MODE_NONE, MODE_SSL, MODE_STARTTLS}

# Regex for basic email syntax validation
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Config ─────────────────────────────────────────────────────────────────────

@dataclass
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_addr: str
    from_name: str
    security_mode: str   # "none" | "ssl" | "starttls"
    timeout: int         # seconds

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.host not in ("localhost", "smtp.example.com", ""))

    @property
    def use_tls(self) -> bool:
        """True when mode is ssl (implicit TLS from connect)."""
        return self.security_mode == MODE_SSL

    @property
    def start_tls(self) -> bool:
        """True when mode is starttls (upgrade after connect)."""
        return self.security_mode == MODE_STARTTLS

    @property
    def from_header(self) -> str:
        if self.from_name:
            return f"{self.from_name} <{self.from_addr}>"
        return self.from_addr


async def _load_config(db) -> SmtpConfig:
    """Load SMTP config, merging DB values over .env defaults."""
    from app.crud import system_settings as ss_crud

    db_cfg = await ss_crud.get_smtp_config_from_db(db)

    def _val(db_key: str, env_fallback) -> str:
        return db_cfg.get(db_key) or str(env_fallback)

    raw_mode = _val(ss_crud.SMTP_SECURITY_MODE, "starttls" if _settings.SMTP_TLS else "none")
    mode = raw_mode.lower().strip() if raw_mode.lower().strip() in VALID_MODES else MODE_STARTTLS

    raw_timeout = _val(ss_crud.SMTP_TIMEOUT, "15")
    try:
        timeout = max(5, min(60, int(raw_timeout)))
    except ValueError:
        timeout = 15

    return SmtpConfig(
        host=_val(ss_crud.SMTP_HOST, _settings.SMTP_HOST),
        port=int(_val(ss_crud.SMTP_PORT, str(_settings.SMTP_PORT))),
        user=_val(ss_crud.SMTP_USER, _settings.SMTP_USER),
        password=_val(ss_crud.SMTP_PASSWORD, _settings.SMTP_PASSWORD),
        from_addr=_val(ss_crud.SMTP_FROM, _settings.SMTP_FROM),
        from_name=db_cfg.get(ss_crud.SMTP_FROM_NAME, ""),
        security_mode=mode,
        timeout=timeout,
    )


# ── Diagnostics ────────────────────────────────────────────────────────────────

@dataclass
class DiagResult:
    ok: bool
    stage: str          # "dns" | "connect" | "tls" | "auth" | "done"
    message: str
    details: str = ""


async def diagnose_smtp(cfg: SmtpConfig) -> DiagResult:
    """
    Stage-by-stage SMTP connection check.
    Returns a DiagResult describing exactly where a failure occurred.
    Does NOT send any email.
    """
    # Stage 1 — DNS resolution
    try:
        await asyncio.get_event_loop().run_in_executor(
            None, socket.getaddrinfo, cfg.host, cfg.port
        )
    except socket.gaierror as exc:
        return DiagResult(
            ok=False, stage="dns",
            message=f"DNS lookup failed for '{cfg.host}'",
            details=str(exc),
        )

    # Stage 2 — TCP connect + TLS (if ssl mode)
    smtp: Optional[aiosmtplib.SMTP] = None
    try:
        smtp = aiosmtplib.SMTP(
            hostname=cfg.host,
            port=cfg.port,
            use_tls=cfg.use_tls,
            timeout=cfg.timeout,
        )
        await smtp.connect()
    except aiosmtplib.SMTPConnectError as exc:
        return DiagResult(
            ok=False, stage="connect",
            message=f"Could not connect to {cfg.host}:{cfg.port}",
            details=str(exc),
        )
    except aiosmtplib.SMTPException as exc:
        return DiagResult(
            ok=False, stage="connect",
            message=f"SMTP error connecting to {cfg.host}:{cfg.port}",
            details=str(exc),
        )
    except Exception as exc:
        return DiagResult(
            ok=False, stage="connect",
            message=f"Unexpected error connecting to {cfg.host}:{cfg.port}",
            details=str(exc),
        )

    # Stage 3 — STARTTLS upgrade (if starttls mode)
    if cfg.start_tls:
        try:
            await smtp.starttls()
        except aiosmtplib.SMTPException as exc:
            await _quit_safe(smtp)
            return DiagResult(
                ok=False, stage="tls",
                message=f"STARTTLS upgrade failed on {cfg.host}:{cfg.port}",
                details=str(exc),
            )

    # Stage 4 — Authentication
    if cfg.user:
        try:
            await smtp.login(cfg.user, cfg.password)
        except aiosmtplib.SMTPAuthenticationError as exc:
            await _quit_safe(smtp)
            return DiagResult(
                ok=False, stage="auth",
                message=f"Authentication failed for user '{cfg.user}'",
                details=str(exc),
            )
        except aiosmtplib.SMTPException as exc:
            await _quit_safe(smtp)
            return DiagResult(
                ok=False, stage="auth",
                message="SMTP error during authentication",
                details=str(exc),
            )

    await _quit_safe(smtp)

    auth_note = f"authenticated as {cfg.user}" if cfg.user else "no auth required"
    return DiagResult(
        ok=True, stage="done",
        message=(
            f"Connected to {cfg.host}:{cfg.port} "
            f"[{cfg.security_mode.upper()}] — {auth_note}"
        ),
    )


async def _quit_safe(smtp: aiosmtplib.SMTP) -> None:
    try:
        await smtp.quit()
    except Exception:
        pass


# ── Email templates ────────────────────────────────────────────────────────────

def _build_alert_email(
    *,
    to_address: str,
    username: str,
    trading_pair: str,
    condition: str,
    threshold: float,
    triggered_price: float,
    message: str,
    timestamp: datetime,
    from_header: str,
    severity: str = "normal",
) -> MIMEMultipart:
    direction = "above ▲" if condition == "above" else "below ▼"

    _subject_prefixes = {
        "normal":   "🔔",
        "elevated": "🔶 [ELEVATED]",
        "critical": "🚨 [CRITICAL]",
    }
    prefix = _subject_prefixes.get(severity, "🔔")
    subject = (
        f"{prefix} [{trading_pair}] Price alert: ${triggered_price:,.2f} crossed "
        f"{'above' if condition == 'above' else 'below'} ${threshold:,.2f}"
    )

    _severity_colours = {
        "normal":   "#7c6af7",
        "elevated": "#f39c12",
        "critical": "#e74c3c",
    }
    header_colour = _severity_colours.get(severity, "#7c6af7")
    severity_banner = ""
    if severity != "normal":
        severity_label = severity.upper()
        severity_banner = (
            f'<div style="background:{header_colour};padding:.6rem 2rem;'
            f'font-size:.85rem;color:#fff;font-weight:700;letter-spacing:.05em">'
            f'⚠ SEVERITY: {severity_label}</div>'
        )

    ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:system-ui,sans-serif;background:#0f1117;color:#e0e0e0;padding:0;margin:0">
  <div style="max-width:560px;margin:2rem auto;background:#1a1d27;border:1px solid #2a2d3e;border-radius:10px;overflow:hidden">
    <div style="background:{header_colour};padding:1.2rem 2rem">
      <h1 style="margin:0;font-size:1.3rem;color:#fff">🔔 Crypto Alert Triggered</h1>
    </div>
    {severity_banner}
    <div style="padding:2rem">
      <p style="margin:0 0 1.5rem;color:#ccc">Hi <strong>{username}</strong>,</p>
      <p style="margin:0 0 1.5rem;color:#ccc">Your price alert for <strong>{trading_pair}</strong> has been triggered.</p>
      <table style="width:100%;border-collapse:collapse;margin-bottom:1.5rem">
        <tr style="background:#0f1117">
          <td style="padding:.6rem 1rem;color:#999;font-size:.85rem;text-transform:uppercase">Asset</td>
          <td style="padding:.6rem 1rem;font-weight:700;color:#fff">{trading_pair}</td>
        </tr>
        <tr>
          <td style="padding:.6rem 1rem;color:#999;font-size:.85rem;text-transform:uppercase">Current Price</td>
          <td style="padding:.6rem 1rem;font-weight:700;color:#7c6af7;font-size:1.2rem">${triggered_price:,.2f}</td>
        </tr>
        <tr style="background:#0f1117">
          <td style="padding:.6rem 1rem;color:#999;font-size:.85rem;text-transform:uppercase">Condition</td>
          <td style="padding:.6rem 1rem;color:#e0e0e0">{direction} ${threshold:,.2f}</td>
        </tr>
        <tr>
          <td style="padding:.6rem 1rem;color:#999;font-size:.85rem;text-transform:uppercase">Triggered At</td>
          <td style="padding:.6rem 1rem;color:#e0e0e0">{ts_str}</td>
        </tr>
      </table>
      <div style="background:#0f1117;border-left:4px solid #7c6af7;padding:1rem;border-radius:0 6px 6px 0;margin-bottom:1.5rem">
        <p style="margin:0;color:#ccc;font-size:.9rem">{message}</p>
      </div>
    </div>
    <div style="padding:1rem 2rem;border-top:1px solid #2a2d3e;text-align:center">
      <p style="margin:0;color:#555;font-size:.78rem">Crypto Alert System — self-hosted</p>
    </div>
  </div>
</body>
</html>"""

    text = (
        f"Crypto Alert — {trading_pair}\n"
        f"{'=' * 40}\n"
        f"Current price : ${triggered_price:,.2f}\n"
        f"Condition     : {direction} ${threshold:,.2f}\n"
        f"Triggered at  : {ts_str}\n\n"
        f"{message}\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = to_address
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


def _build_test_email(*, to_address: str, from_header: str) -> MIMEMultipart:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    html = f"""<!DOCTYPE html>
<html>
<body style="font-family:system-ui,sans-serif;background:#0f1117;color:#e0e0e0;padding:2rem">
  <div style="max-width:480px;margin:0 auto;background:#1a1d27;border:1px solid #2a2d3e;border-radius:10px;padding:2rem">
    <h1 style="color:#2ecc71">✅ SMTP Test Successful</h1>
    <p>Your Crypto Alert System SMTP configuration is working correctly.</p>
    <p style="color:#888;font-size:.85rem">Sent at {ts}</p>
  </div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "✅ Crypto Alert System — SMTP test"
    msg["From"] = from_header
    msg["To"] = to_address
    msg.attach(MIMEText(f"SMTP test successful. Sent at {ts}", "plain"))
    msg.attach(MIMEText(html, "html"))
    return msg


# ── Core send ──────────────────────────────────────────────────────────────────

async def _send_message(msg: MIMEMultipart, cfg: SmtpConfig) -> None:
    """
    Send *msg* using the correct TLS mode for *cfg*.

    ssl      → use_tls=True  (implicit SSL, port 465)
    starttls → start_tls=True (STARTTLS upgrade, port 587)
    none     → no TLS
    """
    logger.debug(
        f"SMTP send: host={cfg.host} port={cfg.port} "
        f"mode={cfg.security_mode} user={cfg.user or '(none)'}"
    )
    await aiosmtplib.send(
        msg,
        hostname=cfg.host,
        port=cfg.port,
        username=cfg.user or None,
        password=cfg.password or None,
        use_tls=cfg.use_tls,
        start_tls=cfg.start_tls,
        timeout=cfg.timeout,
    )


async def _send_with_retry(msg: MIMEMultipart, cfg: SmtpConfig) -> None:
    """Attempt to send *msg* up to MAX_RETRIES times with exponential back-off."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            await _send_message(msg, cfg)
            return
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY ** attempt
                logger.warning(
                    f"SMTP send attempt {attempt}/{MAX_RETRIES} failed: {exc}. "
                    f"Retrying in {delay}s…"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"SMTP send failed after {MAX_RETRIES} attempts: {exc}")

    raise last_exc  # type: ignore[misc]


# ── Public interface ───────────────────────────────────────────────────────────

class EmailService:
    """Sends alert and test emails via SMTP."""

    async def send_alert_email(
        self,
        db,
        *,
        to_address: str,
        username: str,
        trading_pair: str,
        condition: str,
        threshold: float,
        triggered_price: float,
        message: str,
        timestamp: Optional[datetime] = None,
        severity: str = "normal",
    ) -> bool:
        """
        Send an alert notification email.
        Returns True on success, False on failure (error already logged).
        """
        cfg = await _load_config(db)
        if not cfg.is_configured:
            logger.warning(
                f"SMTP not configured — skipping email to {to_address}. "
                "Set SMTP_HOST in .env or via /settings/smtp."
            )
            return False

        ts = timestamp or datetime.now(timezone.utc)
        msg = _build_alert_email(
            to_address=to_address,
            username=username,
            trading_pair=trading_pair,
            condition=condition,
            threshold=threshold,
            triggered_price=triggered_price,
            message=message,
            timestamp=ts,
            from_header=cfg.from_header,
            severity=severity,
        )
        try:
            await _send_with_retry(msg, cfg)
            logger.info(f"Alert email sent → {to_address} [{trading_pair}]")
            # Record last successful send
            from app.crud import system_settings as ss_crud
            await ss_crud.save_smtp_config(db, {
                ss_crud.SMTP_LAST_SEND_OK: datetime.now(timezone.utc).isoformat(),
            })
            return True
        except Exception as exc:
            logger.error(f"Alert email delivery failed for {to_address}: {exc}")
            return False

    async def send_test(self, db, *, to_address: str) -> tuple[bool, str]:
        """
        Send a test email. Returns (success, message).
        Also stores the result in system_settings for the diagnostics panel.
        """
        cfg = await _load_config(db)
        if not cfg.host:
            return False, "SMTP host is not configured."

        from app.crud import system_settings as ss_crud

        msg = _build_test_email(to_address=to_address, from_header=cfg.from_header)
        try:
            await _send_message(msg, cfg)
            result_msg = f"Test email sent to {to_address} via {cfg.host}:{cfg.port} [{cfg.security_mode.upper()}]"
            logger.info(result_msg)
            await ss_crud.save_smtp_config(db, {
                ss_crud.SMTP_LAST_TEST_STATUS:  "ok",
                ss_crud.SMTP_LAST_TEST_MESSAGE: result_msg,
                ss_crud.SMTP_LAST_TEST_OK:      datetime.now(timezone.utc).isoformat(),
            })
            return True, result_msg
        except Exception as exc:
            err = f"SMTP error [{cfg.security_mode.upper()}] {cfg.host}:{cfg.port}: {exc}"
            logger.error(f"Test email failed → {to_address}: {err}")
            await ss_crud.save_smtp_config(db, {
                ss_crud.SMTP_LAST_TEST_STATUS:  "failed",
                ss_crud.SMTP_LAST_TEST_MESSAGE: err,
            })
            return False, err

    async def validate_config(self, db) -> tuple[bool, str]:
        """
        Stage-by-stage SMTP connection test (no email sent).
        Returns (ok, human-readable message with stage info).
        """
        cfg = await _load_config(db)
        if not cfg.host:
            return False, "SMTP host is not configured."

        logger.info(
            f"SMTP validate: host={cfg.host} port={cfg.port} "
            f"mode={cfg.security_mode} user={cfg.user or '(none)'}"
        )

        result = await diagnose_smtp(cfg)

        from app.crud import system_settings as ss_crud
        status = "ok" if result.ok else "failed"
        detail = f"{result.message}" + (f" — {result.details}" if result.details else "")
        await ss_crud.save_smtp_config(db, {
            ss_crud.SMTP_LAST_TEST_STATUS:  status,
            ss_crud.SMTP_LAST_TEST_MESSAGE: detail,
            **(
                {ss_crud.SMTP_LAST_TEST_OK: datetime.now(timezone.utc).isoformat()}
                if result.ok else {}
            ),
        })

        if result.ok:
            logger.info(f"SMTP validate OK: {result.message}")
            return True, f"✅ {result.message}"
        else:
            logger.warning(
                f"SMTP validate FAILED at stage '{result.stage}': "
                f"{result.message} — {result.details}"
            )
            stage_hints = {
                "dns":     "Check that the hostname is correct and reachable from this server.",
                "connect": "Check the port number and that the server is accepting connections.",
                "tls":     (
                    "TLS negotiation failed. "
                    "If using port 465, select SSL/TLS mode. "
                    "If using port 587, select STARTTLS mode."
                ),
                "auth":    "Check the username and password.",
            }
            hint = stage_hints.get(result.stage, "")
            msg = f"❌ Failed at stage: {result.stage.upper()} — {result.message}"
            if result.details:
                msg += f"\nDetail: {result.details}"
            if hint:
                msg += f"\nHint: {hint}"
            return False, msg


email_service = EmailService()
