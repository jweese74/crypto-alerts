"""Tests for the multi-channel notification service.

These tests focus on pure logic (severity routing, message construction,
config loading, error isolation) without requiring live network connections.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.notification import (
    NotificationConfig,
    NotificationPayload,
    NotificationDispatcher,
    _make_title,
    _make_short_body,
    send_ntfy,
    send_discord,
    send_telegram,
    _test_payload,
    PUSH_SEVERITIES,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_payload():
    return NotificationPayload(
        trading_pair="BTC/USD",
        triggered_price=98_500.0,
        threshold=100_000.0,
        condition="above",
        severity="normal",
        timestamp=datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        to_address="user@example.com",
        username="testuser",
        message="BTC/USD price $98,500.00 crossed above threshold $100,000.00",
    )


@pytest.fixture
def elevated_payload(sample_payload):
    sample_payload.severity = "elevated"
    return sample_payload


@pytest.fixture
def critical_payload(sample_payload):
    sample_payload.severity = "critical"
    return sample_payload


@pytest.fixture
def ntfy_config():
    return NotificationConfig(
        ntfy_enabled=True,
        ntfy_server_url="https://ntfy.sh",
        ntfy_topic="my-alerts",
        ntfy_token="",
    )


@pytest.fixture
def discord_config():
    return NotificationConfig(
        discord_enabled=True,
        discord_webhook_url="https://discord.com/api/webhooks/123/abc",
    )


@pytest.fixture
def telegram_config():
    return NotificationConfig(
        telegram_enabled=True,
        telegram_bot_token="123456:ABCdef",
        telegram_chat_id="-100999888",
    )


# ── Message builders ──────────────────────────────────────────────────────────

class TestMessageBuilders:
    def test_title_above(self, sample_payload):
        title = _make_title(sample_payload)
        assert "BTC/USD" in title
        assert "▲" in title
        assert "98,500" in title

    def test_title_below(self, sample_payload):
        sample_payload.condition = "below"
        title = _make_title(sample_payload)
        assert "▼" in title

    def test_title_severity_emoji_normal(self, sample_payload):
        title = _make_title(sample_payload)
        assert "🔔" in title

    def test_title_severity_emoji_elevated(self, elevated_payload):
        title = _make_title(elevated_payload)
        assert "🔶" in title

    def test_title_severity_emoji_critical(self, critical_payload):
        title = _make_title(critical_payload)
        assert "🚨" in title

    def test_short_body_contains_pair(self, sample_payload):
        body = _make_short_body(sample_payload)
        assert "BTC/USD" in body

    def test_short_body_contains_threshold(self, sample_payload):
        body = _make_short_body(sample_payload)
        assert "100,000" in body

    def test_short_body_contains_severity(self, sample_payload):
        body = _make_short_body(sample_payload)
        assert "NORMAL" in body

    def test_short_body_contains_timestamp(self, sample_payload):
        body = _make_short_body(sample_payload)
        assert "2025-01-15" in body


# ── Severity routing ──────────────────────────────────────────────────────────

class TestSeverityRouting:
    def test_push_severities_defined(self):
        assert "elevated" in PUSH_SEVERITIES
        assert "critical" in PUSH_SEVERITIES
        assert "normal" not in PUSH_SEVERITIES

    @pytest.mark.asyncio
    async def test_normal_severity_email_only(self, sample_payload):
        """NORMAL alerts must not trigger push channels."""
        dispatcher = NotificationDispatcher()

        mock_email_ok = AsyncMock(return_value=True)
        mock_load_cfg = AsyncMock(return_value=NotificationConfig(
            ntfy_enabled=True, ntfy_topic="t", discord_enabled=True, telegram_enabled=True,
        ))

        with patch("app.services.notification._load_config", mock_load_cfg), \
             patch("app.services.email_service.email_service.send_alert_email", mock_email_ok):
            results = await dispatcher.send_alert(
                db=AsyncMock(),
                to_address="a@b.com",
                username="u",
                trading_pair="BTC/USD",
                condition="above",
                threshold=100_000.0,
                triggered_price=98_000.0,
                message="msg",
                timestamp=datetime.now(timezone.utc),
                severity="normal",
            )

        assert "email" in results
        assert "ntfy" not in results
        assert "discord" not in results
        assert "telegram" not in results
        # _load_config should NOT have been called for normal
        mock_load_cfg.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_elevated_triggers_push_channels(self, elevated_payload):
        """ELEVATED alerts must attempt all enabled push channels."""
        dispatcher = NotificationDispatcher()

        config = NotificationConfig(
            ntfy_enabled=True,
            ntfy_server_url="https://ntfy.sh",
            ntfy_topic="alerts",
            discord_enabled=True,
            discord_webhook_url="https://discord.com/api/webhooks/x/y",
            telegram_enabled=True,
            telegram_bot_token="tok",
            telegram_chat_id="123",
        )

        with patch("app.services.notification._load_config", AsyncMock(return_value=config)), \
             patch("app.services.notification.send_ntfy", AsyncMock(return_value=True)) as mn, \
             patch("app.services.notification.send_discord", AsyncMock(return_value=True)) as md, \
             patch("app.services.notification.send_telegram", AsyncMock(return_value=True)) as mt, \
             patch("app.services.email_service.email_service.send_alert_email", AsyncMock(return_value=True)):
            results = await dispatcher.send_alert(
                db=AsyncMock(),
                to_address="u@example.com",
                username="u",
                trading_pair="ETH/USD",
                condition="below",
                threshold=3_000.0,
                triggered_price=2_900.0,
                message="msg",
                timestamp=datetime.now(timezone.utc),
                severity="elevated",
            )

        assert results.get("email") is True
        assert results.get("ntfy") is True
        assert results.get("discord") is True
        assert results.get("telegram") is True
        mn.assert_awaited_once()
        md.assert_awaited_once()
        mt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_critical_triggers_push_channels(self):
        """CRITICAL behaves the same as ELEVATED for channel routing."""
        dispatcher = NotificationDispatcher()
        config = NotificationConfig(
            ntfy_enabled=True, ntfy_topic="t", ntfy_server_url="https://ntfy.sh",
        )
        with patch("app.services.notification._load_config", AsyncMock(return_value=config)), \
             patch("app.services.notification.send_ntfy", AsyncMock(return_value=True)) as mn, \
             patch("app.services.email_service.email_service.send_alert_email", AsyncMock(return_value=True)):
            results = await dispatcher.send_alert(
                db=AsyncMock(),
                to_address="u@example.com",
                username="u",
                trading_pair="SOL/USD",
                condition="above",
                threshold=200.0,
                triggered_price=210.0,
                message="msg",
                timestamp=datetime.now(timezone.utc),
                severity="critical",
            )
        mn.assert_awaited_once()
        assert results.get("ntfy") is True

    @pytest.mark.asyncio
    async def test_disabled_channels_not_called(self):
        """Channels with enabled=False must not be called even at elevated severity."""
        dispatcher = NotificationDispatcher()
        config = NotificationConfig(
            ntfy_enabled=False,
            discord_enabled=False,
            telegram_enabled=False,
        )
        with patch("app.services.notification._load_config", AsyncMock(return_value=config)), \
             patch("app.services.notification.send_ntfy", AsyncMock()) as mn, \
             patch("app.services.notification.send_discord", AsyncMock()) as md, \
             patch("app.services.notification.send_telegram", AsyncMock()) as mt, \
             patch("app.services.email_service.email_service.send_alert_email", AsyncMock(return_value=True)):
            results = await dispatcher.send_alert(
                db=AsyncMock(),
                to_address="u@example.com",
                username="u",
                trading_pair="BTC/USD",
                condition="above",
                threshold=100_000.0,
                triggered_price=105_000.0,
                message="msg",
                timestamp=datetime.now(timezone.utc),
                severity="elevated",
            )
        mn.assert_not_awaited()
        md.assert_not_awaited()
        mt.assert_not_awaited()
        assert "ntfy" not in results
        assert "discord" not in results
        assert "telegram" not in results


# ── Error isolation ───────────────────────────────────────────────────────────

class TestErrorIsolation:
    @pytest.mark.asyncio
    async def test_ntfy_failure_does_not_stop_discord(self):
        """A failing ntfy channel must not prevent Discord from being called."""
        dispatcher = NotificationDispatcher()
        config = NotificationConfig(
            ntfy_enabled=True, ntfy_topic="t", ntfy_server_url="https://ntfy.sh",
            discord_enabled=True, discord_webhook_url="https://discord.com/api/webhooks/x/y",
        )
        with patch("app.services.notification._load_config", AsyncMock(return_value=config)), \
             patch("app.services.notification.send_ntfy", AsyncMock(side_effect=Exception("ntfy down"))), \
             patch("app.services.notification.send_discord", AsyncMock(return_value=True)) as md, \
             patch("app.services.email_service.email_service.send_alert_email", AsyncMock(return_value=True)):
            results = await dispatcher.send_alert(
                db=AsyncMock(),
                to_address="u@example.com",
                username="u",
                trading_pair="BTC/USD",
                condition="above",
                threshold=100_000.0,
                triggered_price=105_000.0,
                message="msg",
                timestamp=datetime.now(timezone.utc),
                severity="elevated",
            )
        assert results["ntfy"] is False   # failed gracefully
        assert results["discord"] is True  # still delivered
        md.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_email_failure_does_not_stop_push(self):
        """A failing email must not prevent push channels from firing."""
        dispatcher = NotificationDispatcher()
        config = NotificationConfig(
            ntfy_enabled=True, ntfy_topic="t", ntfy_server_url="https://ntfy.sh",
        )
        with patch("app.services.notification._load_config", AsyncMock(return_value=config)), \
             patch("app.services.notification.send_ntfy", AsyncMock(return_value=True)) as mn, \
             patch("app.services.email_service.email_service.send_alert_email",
                   AsyncMock(side_effect=Exception("smtp down"))):
            results = await dispatcher.send_alert(
                db=AsyncMock(),
                to_address="u@example.com",
                username="u",
                trading_pair="BTC/USD",
                condition="above",
                threshold=100_000.0,
                triggered_price=105_000.0,
                message="msg",
                timestamp=datetime.now(timezone.utc),
                severity="elevated",
            )
        assert results["email"] is False   # failed gracefully
        assert results["ntfy"] is True     # still delivered
        mn.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_email_address_skips_email_channel(self):
        """When to_address is empty, email channel is skipped (no crash)."""
        dispatcher = NotificationDispatcher()
        config = NotificationConfig()  # no push channels enabled
        with patch("app.services.notification._load_config", AsyncMock(return_value=config)), \
             patch("app.services.email_service.email_service.send_alert_email", AsyncMock()) as me:
            results = await dispatcher.send_alert(
                db=AsyncMock(),
                to_address="",
                username="u",
                trading_pair="BTC/USD",
                condition="above",
                threshold=100_000.0,
                triggered_price=105_000.0,
                message="msg",
                timestamp=datetime.now(timezone.utc),
                severity="normal",
            )
        me.assert_not_awaited()
        assert "email" not in results


# ── Channel senders (unit) ────────────────────────────────────────────────────

class TestNtfyChannel:
    @pytest.mark.asyncio
    async def test_ntfy_no_topic_returns_false(self, sample_payload, ntfy_config):
        ntfy_config.ntfy_topic = ""
        result = await send_ntfy(sample_payload, ntfy_config)
        assert result is False

    @pytest.mark.asyncio
    async def test_ntfy_sends_with_token(self, sample_payload, ntfy_config):
        ntfy_config.ntfy_token = "mytoken"
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            result = await send_ntfy(sample_payload, ntfy_config)
        assert result is True
        call_kwargs = MockClient.return_value.__aenter__.return_value.post.call_args
        assert "Authorization" in call_kwargs.kwargs.get("headers", {})

    @pytest.mark.asyncio
    async def test_ntfy_priority_critical(self, critical_payload, ntfy_config):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            await send_ntfy(critical_payload, ntfy_config)
        call_kwargs = MockClient.return_value.__aenter__.return_value.post.call_args
        assert call_kwargs.kwargs["headers"]["Priority"] == "5"


class TestDiscordChannel:
    @pytest.mark.asyncio
    async def test_discord_no_url_returns_false(self, sample_payload, discord_config):
        discord_config.discord_webhook_url = ""
        result = await send_discord(sample_payload, discord_config)
        assert result is False

    @pytest.mark.asyncio
    async def test_discord_sends_embed(self, elevated_payload, discord_config):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            result = await send_discord(elevated_payload, discord_config)
        assert result is True
        call_kwargs = MockClient.return_value.__aenter__.return_value.post.call_args
        payload_json = call_kwargs.kwargs["json"]
        assert "embeds" in payload_json
        assert payload_json["embeds"][0]["color"] == 0xF39C12   # orange for elevated


class TestTelegramChannel:
    @pytest.mark.asyncio
    async def test_telegram_no_token_returns_false(self, sample_payload, telegram_config):
        telegram_config.telegram_bot_token = ""
        result = await send_telegram(sample_payload, telegram_config)
        assert result is False

    @pytest.mark.asyncio
    async def test_telegram_no_chat_id_returns_false(self, sample_payload, telegram_config):
        telegram_config.telegram_chat_id = ""
        result = await send_telegram(sample_payload, telegram_config)
        assert result is False

    @pytest.mark.asyncio
    async def test_telegram_sends_html_message(self, elevated_payload, telegram_config):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.AsyncClient") as MockClient:
            MockClient.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_resp)
            result = await send_telegram(elevated_payload, telegram_config)
        assert result is True
        call_kwargs = MockClient.return_value.__aenter__.return_value.post.call_args
        payload_json = call_kwargs.kwargs["json"]
        assert payload_json["parse_mode"] == "HTML"
        assert "<b>" in payload_json["text"]
        assert "BTC/USD" in payload_json["text"]


# ── Config loading ────────────────────────────────────────────────────────────

class TestConfigLoading:
    @pytest.mark.asyncio
    async def test_default_config_when_db_empty(self):
        from app.services.notification import _load_config
        from app import crud
        with patch("app.crud.system_settings.get_notification_config", AsyncMock(return_value={})):
            config = await _load_config(AsyncMock())
        assert config.ntfy_enabled is False
        assert config.ntfy_server_url == "https://ntfy.sh"
        assert config.discord_enabled is False
        assert config.telegram_enabled is False

    @pytest.mark.asyncio
    async def test_config_loaded_from_db(self):
        from app.services.notification import _load_config
        from app.crud import system_settings as ss_crud

        db_values = {
            ss_crud.NOTIF_NTFY_ENABLED:     "true",
            ss_crud.NOTIF_NTFY_SERVER_URL:  "https://myserver.example.com",
            ss_crud.NOTIF_NTFY_TOPIC:       "my-topic",
            ss_crud.NOTIF_DISCORD_ENABLED:  "true",
            ss_crud.NOTIF_DISCORD_WEBHOOK_URL: "https://discord.com/api/webhooks/x",
            ss_crud.NOTIF_TELEGRAM_ENABLED: "false",
        }
        with patch("app.crud.system_settings.get_notification_config", AsyncMock(return_value=db_values)):
            config = await _load_config(AsyncMock())

        assert config.ntfy_enabled is True
        assert config.ntfy_server_url == "https://myserver.example.com"
        assert config.ntfy_topic == "my-topic"
        assert config.discord_enabled is True
        assert config.telegram_enabled is False


# ── Test payload helper ───────────────────────────────────────────────────────

class TestTestPayload:
    def test_payload_is_valid(self):
        p = _test_payload()
        assert p.trading_pair == "BTC/USD"
        assert p.severity == "elevated"
        assert p.triggered_price > 0
        assert p.message
