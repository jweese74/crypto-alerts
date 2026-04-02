"""
Tests for SMTP configuration, security modes, and email service.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timezone

from app.services.email_service import (
    SmtpConfig,
    DiagResult,
    EmailService,
    MODE_SSL,
    MODE_STARTTLS,
    MODE_NONE,
    VALID_MODES,
    _send_message,
    _build_test_email,
    _build_alert_email,
)
from app.crud.system_settings import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM,
    SMTP_FROM_NAME, SMTP_SECURITY_MODE, SMTP_TIMEOUT,
    SMTP_LAST_TEST_STATUS, SMTP_LAST_TEST_MESSAGE,
    _SMTP_TLS_LEGACY,
)


# ── SmtpConfig properties ──────────────────────────────────────────────────────

def _cfg(**kwargs) -> SmtpConfig:
    defaults = dict(
        host="smtp.example.com", port=587, user="u", password="p",
        from_addr="a@b.com", from_name="", security_mode=MODE_STARTTLS, timeout=15,
    )
    defaults.update(kwargs)
    return SmtpConfig(**defaults)


class TestSmtpConfig:
    def test_ssl_mode_use_tls_true(self):
        c = _cfg(security_mode=MODE_SSL)
        assert c.use_tls is True
        assert c.start_tls is False

    def test_starttls_mode_start_tls_true(self):
        c = _cfg(security_mode=MODE_STARTTLS)
        assert c.use_tls is False
        assert c.start_tls is True

    def test_none_mode_no_tls(self):
        c = _cfg(security_mode=MODE_NONE)
        assert c.use_tls is False
        assert c.start_tls is False

    def test_is_configured_with_real_host(self):
        assert _cfg(host="smtp.cogeco.ca").is_configured is True

    def test_is_configured_false_localhost(self):
        assert _cfg(host="localhost").is_configured is False

    def test_is_configured_false_example(self):
        assert _cfg(host="smtp.example.com").is_configured is False

    def test_is_configured_false_empty(self):
        assert _cfg(host="").is_configured is False

    def test_from_header_with_name(self):
        c = _cfg(from_addr="alerts@b.com", from_name="Crypto Bot")
        assert c.from_header == "Crypto Bot <alerts@b.com>"

    def test_from_header_without_name(self):
        c = _cfg(from_addr="alerts@b.com", from_name="")
        assert c.from_header == "alerts@b.com"

    def test_valid_modes_set(self):
        assert MODE_SSL in VALID_MODES
        assert MODE_STARTTLS in VALID_MODES
        assert MODE_NONE in VALID_MODES
        assert len(VALID_MODES) == 3


# ── _send_message — correct aiosmtplib parameters ─────────────────────────────

class TestSendMessageParams:
    @pytest.mark.asyncio
    async def test_ssl_mode_uses_use_tls(self):
        cfg = _cfg(security_mode=MODE_SSL, host="smtp.cogeco.ca", port=465)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await _send_message(MagicMock(), cfg)
        kwargs = mock_send.call_args[1]
        assert kwargs["use_tls"] is True
        assert kwargs["start_tls"] is False

    @pytest.mark.asyncio
    async def test_starttls_mode_uses_start_tls(self):
        cfg = _cfg(security_mode=MODE_STARTTLS, host="smtp.gmail.com", port=587)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await _send_message(MagicMock(), cfg)
        kwargs = mock_send.call_args[1]
        assert kwargs["use_tls"] is False
        assert kwargs["start_tls"] is True

    @pytest.mark.asyncio
    async def test_none_mode_no_tls(self):
        cfg = _cfg(security_mode=MODE_NONE, host="localhost", port=25)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await _send_message(MagicMock(), cfg)
        kwargs = mock_send.call_args[1]
        assert kwargs["use_tls"] is False
        assert kwargs["start_tls"] is False

    @pytest.mark.asyncio
    async def test_credentials_passed(self):
        cfg = _cfg(security_mode=MODE_SSL, user="myuser", password="mypass")
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await _send_message(MagicMock(), cfg)
        kwargs = mock_send.call_args[1]
        assert kwargs["username"] == "myuser"
        assert kwargs["password"] == "mypass"

    @pytest.mark.asyncio
    async def test_empty_credentials_sent_as_none(self):
        cfg = _cfg(user="", password="")
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await _send_message(MagicMock(), cfg)
        kwargs = mock_send.call_args[1]
        assert kwargs["username"] is None
        assert kwargs["password"] is None

    @pytest.mark.asyncio
    async def test_timeout_passed(self):
        cfg = _cfg(timeout=30)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await _send_message(MagicMock(), cfg)
        kwargs = mock_send.call_args[1]
        assert kwargs["timeout"] == 30

    @pytest.mark.asyncio
    async def test_hostname_and_port_passed(self):
        cfg = _cfg(host="smtp.cogeco.ca", port=465, security_mode=MODE_SSL)
        with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
            await _send_message(MagicMock(), cfg)
        kwargs = mock_send.call_args[1]
        assert kwargs["hostname"] == "smtp.cogeco.ca"
        assert kwargs["port"] == 465


# ── Config loading — legacy migration ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_config_legacy_tls_true_becomes_starttls():
    """Old smtp.tls=true should migrate to security_mode=starttls."""
    db = AsyncMock()
    mock_rows = {
        SMTP_HOST: "smtp.example.com",
        SMTP_PORT: "587",
        _SMTP_TLS_LEGACY: "true",
    }

    with patch(
        "app.crud.system_settings.get_smtp_config_from_db",
        new_callable=AsyncMock,
        return_value={**mock_rows, SMTP_SECURITY_MODE: "starttls"},  # migration already applied
    ):
        from app.services.email_service import _load_config
        cfg = await _load_config(db)
    assert cfg.security_mode == MODE_STARTTLS


@pytest.mark.asyncio
async def test_load_config_legacy_tls_false_becomes_none():
    db = AsyncMock()
    with patch(
        "app.crud.system_settings.get_smtp_config_from_db",
        new_callable=AsyncMock,
        return_value={_SMTP_TLS_LEGACY: "false", SMTP_SECURITY_MODE: "none"},
    ):
        from app.services.email_service import _load_config
        cfg = await _load_config(db)
    assert cfg.security_mode == MODE_NONE


@pytest.mark.asyncio
async def test_load_config_invalid_mode_defaults_starttls():
    db = AsyncMock()
    with patch(
        "app.crud.system_settings.get_smtp_config_from_db",
        new_callable=AsyncMock,
        return_value={SMTP_SECURITY_MODE: "garbage"},
    ):
        from app.services.email_service import _load_config
        cfg = await _load_config(db)
    assert cfg.security_mode == MODE_STARTTLS


@pytest.mark.asyncio
async def test_load_config_ssl_mode_preserved():
    db = AsyncMock()
    with patch(
        "app.crud.system_settings.get_smtp_config_from_db",
        new_callable=AsyncMock,
        return_value={SMTP_SECURITY_MODE: "ssl", SMTP_HOST: "smtp.cogeco.ca", SMTP_PORT: "465"},
    ):
        from app.services.email_service import _load_config
        cfg = await _load_config(db)
    assert cfg.security_mode == MODE_SSL
    assert cfg.use_tls is True


# ── system_settings CRUD migration ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_smtp_config_migrates_legacy_tls():
    """get_smtp_config_from_db should inject smtp.security_mode from smtp.tls."""
    db = AsyncMock()
    mock_result = MagicMock()
    # Simulate DB rows with only legacy smtp.tls, no smtp.security_mode
    from app.models.system_settings import SystemSettings
    row1 = MagicMock(spec=SystemSettings)
    row1.key = _SMTP_TLS_LEGACY
    row1.value = "true"
    mock_result.scalars.return_value.all.return_value = [row1]
    db.execute = AsyncMock(return_value=mock_result)

    from app.crud.system_settings import get_smtp_config_from_db
    result = await get_smtp_config_from_db(db)
    assert result.get(SMTP_SECURITY_MODE) == "starttls"


@pytest.mark.asyncio
async def test_get_smtp_config_no_migration_if_mode_present():
    """If smtp.security_mode already present, no migration needed."""
    db = AsyncMock()
    mock_result = MagicMock()
    from app.models.system_settings import SystemSettings
    row1 = MagicMock(spec=SystemSettings)
    row1.key = SMTP_SECURITY_MODE
    row1.value = "ssl"
    row2 = MagicMock(spec=SystemSettings)
    row2.key = _SMTP_TLS_LEGACY
    row2.value = "true"  # Would migrate to starttls if no mode present
    mock_result.scalars.return_value.all.return_value = [row1, row2]
    db.execute = AsyncMock(return_value=mock_result)

    from app.crud.system_settings import get_smtp_config_from_db
    result = await get_smtp_config_from_db(db)
    assert result[SMTP_SECURITY_MODE] == "ssl"  # Mode row wins, no migration


# ── Email service — send_test stores result ────────────────────────────────────

@pytest.mark.asyncio
async def test_send_test_records_success():
    svc = EmailService()
    db = AsyncMock()
    cfg = _cfg(host="smtp.cogeco.ca", port=465, security_mode=MODE_SSL,
               from_addr="a@b.com", from_name="")

    with patch("app.services.email_service._load_config", new_callable=AsyncMock, return_value=cfg), \
         patch("app.services.email_service._send_message", new_callable=AsyncMock), \
         patch("app.crud.system_settings.get_smtp_config_from_db",
               new_callable=AsyncMock, return_value={}), \
         patch("app.crud.system_settings.set_value", new_callable=AsyncMock):
        ok, msg = await svc.send_test(db, to_address="recipient@example.com")

    assert ok is True
    assert "smtp.cogeco.ca" in msg
    assert "465" in msg
    assert "SSL" in msg.upper()


@pytest.mark.asyncio
async def test_send_test_records_failure():
    import aiosmtplib
    svc = EmailService()
    db = AsyncMock()
    cfg = _cfg(host="smtp.cogeco.ca", port=465, security_mode=MODE_SSL,
               from_addr="a@b.com", from_name="")

    with patch("app.services.email_service._load_config", new_callable=AsyncMock, return_value=cfg), \
         patch("app.services.email_service._send_message",
               new_callable=AsyncMock, side_effect=Exception("Connection refused")), \
         patch("app.crud.system_settings.get_smtp_config_from_db",
               new_callable=AsyncMock, return_value={}), \
         patch("app.crud.system_settings.set_value", new_callable=AsyncMock):
        ok, msg = await svc.send_test(db, to_address="recipient@example.com")

    assert ok is False
    assert "SSL" in msg.upper()
    assert "465" in msg


# ── Email builders ─────────────────────────────────────────────────────────────

def test_build_test_email_structure():
    msg = _build_test_email(to_address="t@example.com", from_header="Alerts <a@b.com>")
    assert msg["Subject"] == "✅ Crypto Alert System — SMTP test"
    assert msg["To"] == "t@example.com"
    assert msg["From"] == "Alerts <a@b.com>"


def test_build_alert_email_above():
    ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    msg = _build_alert_email(
        to_address="user@example.com",
        username="Alice",
        trading_pair="BTC/USD",
        condition="above",
        threshold=50000.0,
        triggered_price=51000.0,
        message="Price crossed!",
        timestamp=ts,
        from_header="Alerts <a@b.com>",
        severity="normal",
    )
    assert "BTC/USD" in msg["Subject"]
    assert "above" in msg["Subject"]
    assert "$51,000.00" in msg["Subject"]


def test_build_alert_email_critical_subject():
    ts = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    msg = _build_alert_email(
        to_address="user@example.com",
        username="Alice",
        trading_pair="ETH/USD",
        condition="below",
        threshold=2000.0,
        triggered_price=1900.0,
        message="Low!",
        timestamp=ts,
        from_header="a@b.com",
        severity="critical",
    )
    assert "CRITICAL" in msg["Subject"]


# ── Diagnostics ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_diagnose_smtp_dns_failure():
    import socket
    cfg = _cfg(host="nonexistent.invalid", port=465, security_mode=MODE_SSL)
    with patch(
        "app.services.email_service.asyncio.get_event_loop",
        return_value=MagicMock(run_in_executor=AsyncMock(
            side_effect=socket.gaierror("Name not found")
        )),
    ):
        from app.services.email_service import diagnose_smtp
        result = await diagnose_smtp(cfg)
    assert result.ok is False
    assert result.stage == "dns"
    assert "nonexistent.invalid" in result.message


@pytest.mark.asyncio
async def test_diagnose_smtp_success():
    cfg = _cfg(host="smtp.example.com", port=587, security_mode=MODE_STARTTLS, user="u", password="p")
    import socket
    mock_smtp = AsyncMock()
    mock_smtp.connect = AsyncMock()
    mock_smtp.starttls = AsyncMock()
    mock_smtp.login = AsyncMock()
    mock_smtp.quit = AsyncMock()

    with patch("asyncio.get_event_loop") as mock_loop, \
         patch("aiosmtplib.SMTP", return_value=mock_smtp):
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=None)
        from app.services.email_service import diagnose_smtp
        result = await diagnose_smtp(cfg)

    assert result.ok is True
    assert result.stage == "done"


# ── get_setting / set_setting helpers ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_setting_returns_default_when_missing():
    db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_result)

    from app.crud.system_settings import get_setting
    val = await get_setting(db, "nonexistent.key", default="fallback")
    assert val == "fallback"


@pytest.mark.asyncio
async def test_get_setting_returns_stored_value():
    db = AsyncMock()
    mock_result = MagicMock()
    from app.models.system_settings import SystemSettings
    row = MagicMock(spec=SystemSettings)
    row.value = "smtp.cogeco.ca"
    mock_result.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=mock_result)

    from app.crud.system_settings import get_setting
    val = await get_setting(db, SMTP_HOST)
    assert val == "smtp.cogeco.ca"
