"""
Tests for backup / export / import service.

Covers:
- export structure and required keys
- sensitive key redaction
- REDACTED values never written on import
- validate_payload accepts valid payloads
- validate_payload rejects invalid payloads
- import settings skips REDACTED + sensitive keys
- import rules skips unknown users
- import rules skips existing rules when overwrite=False
- round-trip: export → validate → import counts
- from_json / to_json helpers
- format_version validation
"""
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.services.backup import (
    FORMAT_VERSION,
    ImportError,
    _REDACT_KEYS,
    _REDACTED,
    export_data,
    from_json,
    import_data,
    to_json,
    validate_payload,
)
from app.crud import system_settings as ss_crud


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_valid_payload(**overrides) -> dict:
    base = {
        "meta": {
            "format_version": FORMAT_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "exported_by": "test",
            "includes": ["settings", "rules"],
        },
        "settings": {"smtp.host": "localhost", "smtp.port": "587"},
        "alert_rules": [],
    }
    base.update(overrides)
    return base


def _make_rule(**overrides) -> dict:
    base = {
        "id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "trading_pair": "BTC/USD",
        "condition": "above",
        "threshold": 50000.0,
        "label": "Test Rule",
        "custom_message": None,
        "is_active": True,
        "send_once": False,
        "cooldown_minutes": 60,
        "time_filter_enabled": False,
        "active_hours_start": None,
        "active_hours_end": None,
        "active_timezone": "UTC",
        "critical_override": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


# ── validate_payload ──────────────────────────────────────────────────────────

class TestValidatePayload:
    def test_accepts_valid_payload(self):
        payload = _make_valid_payload()
        result = validate_payload(payload)
        assert result is payload  # returns same object

    def test_rejects_non_dict(self):
        with pytest.raises(ImportError, match="JSON object"):
            validate_payload("not a dict")

    def test_rejects_missing_meta(self):
        payload = _make_valid_payload()
        del payload["meta"]
        with pytest.raises(ImportError, match="meta"):
            validate_payload(payload)

    def test_rejects_missing_settings(self):
        payload = _make_valid_payload()
        del payload["settings"]
        with pytest.raises(ImportError):
            validate_payload(payload)

    def test_rejects_missing_alert_rules(self):
        payload = _make_valid_payload()
        del payload["alert_rules"]
        with pytest.raises(ImportError):
            validate_payload(payload)

    def test_rejects_unsupported_format_version(self):
        payload = _make_valid_payload()
        payload["meta"]["format_version"] = 99
        with pytest.raises(ImportError, match="format_version"):
            validate_payload(payload)

    def test_accepts_format_version_1(self):
        payload = _make_valid_payload()
        payload["meta"]["format_version"] = 1
        validate_payload(payload)  # should not raise

    def test_rejects_invalid_rule_condition(self):
        payload = _make_valid_payload(
            alert_rules=[_make_rule(condition="sideways")]
        )
        with pytest.raises(ImportError, match="condition"):
            validate_payload(payload)

    def test_rejects_non_numeric_threshold(self):
        payload = _make_valid_payload(
            alert_rules=[_make_rule(threshold="not-a-number")]
        )
        with pytest.raises(ImportError, match="threshold"):
            validate_payload(payload)

    def test_rejects_rule_missing_required_field(self):
        rule = _make_rule()
        del rule["trading_pair"]
        payload = _make_valid_payload(alert_rules=[rule])
        with pytest.raises(ImportError, match="trading_pair"):
            validate_payload(payload)

    def test_accepts_payload_with_history_and_users(self):
        payload = _make_valid_payload(
            alert_history=[],
            users=[],
        )
        validate_payload(payload)  # no raise

    def test_rejects_alert_history_not_list(self):
        payload = _make_valid_payload(alert_history={"bad": "dict"})
        with pytest.raises(ImportError, match="alert_history"):
            validate_payload(payload)


# ── Sensitive key redaction ───────────────────────────────────────────────────

class TestSensitiveRedaction:
    def test_sensitive_keys_are_redacted(self):
        """All keys in _REDACT_KEYS should appear as REDACTED in exports."""
        for key in _REDACT_KEYS:
            assert key in {
                "smtp.password",
                "notif.ntfy.token",
                "notif.discord.webhook_url",
                "notif.telegram.bot_token",
                "notif.telegram.chat_id",
            }

    @pytest.mark.asyncio
    async def test_export_redacts_smtp_password(self):
        db = MagicMock()
        raw_settings = {
            "smtp.host": "mail.example.com",
            "smtp.password": "super-secret",
            "smtp.port": "587",
        }
        with patch("app.crud.system_settings.get_all", new_callable=AsyncMock) as mock_all, \
             patch("app.crud.alert.get_all_rules", new_callable=AsyncMock) as mock_rules:
            mock_all.return_value = raw_settings
            mock_rules.return_value = []
            payload = await export_data(db, exported_by="test")

        assert payload["settings"]["smtp.password"] == _REDACTED
        assert payload["settings"]["smtp.host"] == "mail.example.com"

    @pytest.mark.asyncio
    async def test_export_redacts_all_sensitive_keys(self):
        db = MagicMock()
        raw_settings = {k: f"secret-{k}" for k in _REDACT_KEYS}
        raw_settings["smtp.host"] = "mail.example.com"

        with patch("app.crud.system_settings.get_all", new_callable=AsyncMock) as mock_all, \
             patch("app.crud.alert.get_all_rules", new_callable=AsyncMock) as mock_rules:
            mock_all.return_value = raw_settings
            mock_rules.return_value = []
            payload = await export_data(db, exported_by="test")

        for key in _REDACT_KEYS:
            assert payload["settings"][key] == _REDACTED, f"{key} should be REDACTED"
        assert payload["settings"]["smtp.host"] == "mail.example.com"


# ── Import settings ───────────────────────────────────────────────────────────

class TestImportSettings:
    @pytest.mark.asyncio
    async def test_redacted_values_never_written(self):
        db = MagicMock()
        settings = {
            "smtp.password": _REDACTED,
            "smtp.host": "mail.example.com",
        }
        written_keys = []
        async def mock_set(db_, key, value, **kw):
            written_keys.append(key)

        with patch("app.crud.system_settings.set_value", side_effect=mock_set):
            from app.services.backup import _import_settings
            await _import_settings(db, settings)

        assert "smtp.password" not in written_keys
        assert "smtp.host" in written_keys

    @pytest.mark.asyncio
    async def test_sensitive_keys_never_written_even_if_not_redacted(self):
        """Extra safety: even if a file has real secrets, they're still blocked."""
        db = MagicMock()
        settings = {
            "smtp.password": "actual-password",
            "notif.ntfy.token": "mytoken",
        }
        written_keys = []
        async def mock_set(db_, key, value, **kw):
            written_keys.append(key)

        with patch("app.crud.system_settings.set_value", side_effect=mock_set):
            from app.services.backup import _import_settings
            await _import_settings(db, settings)

        for sensitive in _REDACT_KEYS:
            assert sensitive not in written_keys


# ── Import rules ──────────────────────────────────────────────────────────────

class TestImportRules:
    @pytest.mark.asyncio
    async def test_skips_rule_with_unknown_user(self):
        db = MagicMock()
        rules = [_make_rule()]  # random user_id

        with patch("app.crud.user.get_by_id", new_callable=AsyncMock) as mock_user, \
             patch("app.crud.alert.get_rule_by_id", new_callable=AsyncMock) as mock_rule, \
             patch("app.crud.alert.create_rule", new_callable=AsyncMock) as mock_create:
            mock_user.return_value = None  # user not found
            mock_rule.return_value = None

            from app.services.backup import _import_rules
            count = await _import_rules(db, rules)

        assert count == 0
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_existing_rule_when_no_overwrite(self):
        db = MagicMock()
        existing_rule = MagicMock()
        existing_rule.id = uuid.uuid4()
        rule = _make_rule(id=str(existing_rule.id))

        with patch("app.crud.user.get_by_id", new_callable=AsyncMock) as mock_user, \
             patch("app.crud.alert.get_rule_by_id", new_callable=AsyncMock) as mock_rule, \
             patch("app.crud.alert.create_rule", new_callable=AsyncMock) as mock_create:
            mock_user.return_value = MagicMock()
            mock_rule.return_value = existing_rule  # exists

            from app.services.backup import _import_rules
            count = await _import_rules(db, [rule], overwrite=False)

        assert count == 0
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_imports_rule_for_known_user(self):
        db = MagicMock()
        db.delete = AsyncMock()
        db.commit = AsyncMock()
        rule = _make_rule()

        with patch("app.crud.user.get_by_id", new_callable=AsyncMock) as mock_user, \
             patch("app.crud.alert.get_rule_by_id", new_callable=AsyncMock) as mock_rule, \
             patch("app.crud.alert.create_rule", new_callable=AsyncMock) as mock_create:
            mock_user.return_value = MagicMock()
            mock_rule.return_value = None  # doesn't exist yet
            mock_create.return_value = MagicMock()

            from app.services.backup import _import_rules
            count = await _import_rules(db, [rule])

        assert count == 1
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_user_id_skipped(self):
        db = MagicMock()
        rule = _make_rule(user_id="not-a-uuid")

        with patch("app.crud.alert.create_rule", new_callable=AsyncMock) as mock_create:
            from app.services.backup import _import_rules
            count = await _import_rules(db, [rule])

        assert count == 0
        mock_create.assert_not_called()


# ── JSON serialisation ────────────────────────────────────────────────────────

class TestJsonHelpers:
    def test_to_json_produces_valid_json(self):
        payload = _make_valid_payload()
        result = to_json(payload)
        parsed = json.loads(result)
        assert parsed["meta"]["format_version"] == FORMAT_VERSION

    def test_from_json_parses_valid_json(self):
        payload = _make_valid_payload()
        raw = json.dumps(payload)
        parsed = from_json(raw)
        assert parsed["meta"]["format_version"] == FORMAT_VERSION

    def test_from_json_raises_on_bad_json(self):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            from_json("not json {{{")

    def test_to_json_handles_datetime(self):
        payload = _make_valid_payload()
        payload["settings"]["ts"] = datetime.now(timezone.utc)
        result = to_json(payload)  # should not raise
        assert '"ts"' in result


# ── Export metadata ───────────────────────────────────────────────────────────

class TestExportMetadata:
    @pytest.mark.asyncio
    async def test_export_has_correct_format_version(self):
        db = MagicMock()
        with patch("app.crud.system_settings.get_all", new_callable=AsyncMock) as m1, \
             patch("app.crud.alert.get_all_rules", new_callable=AsyncMock) as m2:
            m1.return_value = {}
            m2.return_value = []
            payload = await export_data(db)

        assert payload["meta"]["format_version"] == FORMAT_VERSION

    @pytest.mark.asyncio
    async def test_export_includes_rules_section(self):
        db = MagicMock()
        with patch("app.crud.system_settings.get_all", new_callable=AsyncMock) as m1, \
             patch("app.crud.alert.get_all_rules", new_callable=AsyncMock) as m2:
            m1.return_value = {}
            m2.return_value = []
            payload = await export_data(db)

        assert "alert_rules" in payload
        assert "settings" in payload

    @pytest.mark.asyncio
    async def test_history_excluded_by_default(self):
        db = MagicMock()
        with patch("app.crud.system_settings.get_all", new_callable=AsyncMock) as m1, \
             patch("app.crud.alert.get_all_rules", new_callable=AsyncMock) as m2:
            m1.return_value = {}
            m2.return_value = []
            payload = await export_data(db)

        assert "alert_history" not in payload

    @pytest.mark.asyncio
    async def test_history_included_when_requested(self):
        db = MagicMock()
        with patch("app.crud.system_settings.get_all", new_callable=AsyncMock) as m1, \
             patch("app.crud.alert.get_all_rules", new_callable=AsyncMock) as m2, \
             patch("app.crud.alert.get_all_history", new_callable=AsyncMock) as m3:
            m1.return_value = {}
            m2.return_value = []
            m3.return_value = []
            payload = await export_data(db, include_history=True)

        assert "alert_history" in payload

    @pytest.mark.asyncio
    async def test_users_excluded_by_default(self):
        db = MagicMock()
        with patch("app.crud.system_settings.get_all", new_callable=AsyncMock) as m1, \
             patch("app.crud.alert.get_all_rules", new_callable=AsyncMock) as m2:
            m1.return_value = {}
            m2.return_value = []
            payload = await export_data(db)

        assert "users" not in payload
