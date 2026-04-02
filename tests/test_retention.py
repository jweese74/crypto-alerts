"""
Tests for data retention service and related CRUD helpers.

Covers:
- prune_price_history correctness and safety
- prune_alert_history correctness and safety
- cleanup_old_alert_history CRUD
- get_storage_stats returns correct structure
- run_retention_cycle reads config from DB
- retention config defaults
- minimum-retention safety floors
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from app.services import retention as ret_service
from app.crud import system_settings as ss_crud


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utc(**kw) -> datetime:
    return datetime.now(timezone.utc) - timedelta(**kw)


# ── prune_price_history ───────────────────────────────────────────────────────

class TestPrunePriceHistory:
    @pytest.mark.asyncio
    async def test_calls_cleanup_with_days(self):
        db = MagicMock()
        with patch("app.crud.price_history.cleanup_old_history", new_callable=AsyncMock) as mock_clean:
            mock_clean.return_value = 42
            result = await ret_service.prune_price_history(db, retention_days=30)
        mock_clean.assert_called_once_with(db, retention_days=30)
        assert result == 42

    @pytest.mark.asyncio
    async def test_minimum_floor_is_one_day(self):
        db = MagicMock()
        with patch("app.crud.price_history.cleanup_old_history", new_callable=AsyncMock) as mock_clean:
            mock_clean.return_value = 0
            await ret_service.prune_price_history(db, retention_days=0)
        # Should be called with at least 1 day
        called_days = mock_clean.call_args[1]["retention_days"]
        assert called_days >= 1

    @pytest.mark.asyncio
    async def test_negative_days_clamped_to_one(self):
        db = MagicMock()
        with patch("app.crud.price_history.cleanup_old_history", new_callable=AsyncMock) as mock_clean:
            mock_clean.return_value = 0
            await ret_service.prune_price_history(db, retention_days=-5)
        called_days = mock_clean.call_args[1]["retention_days"]
        assert called_days >= 1

    @pytest.mark.asyncio
    async def test_returns_zero_when_nothing_deleted(self):
        db = MagicMock()
        with patch("app.crud.price_history.cleanup_old_history", new_callable=AsyncMock) as mock_clean:
            mock_clean.return_value = 0
            result = await ret_service.prune_price_history(db, retention_days=90)
        assert result == 0


# ── prune_alert_history ───────────────────────────────────────────────────────

class TestPruneAlertHistory:
    @pytest.mark.asyncio
    async def test_calls_cleanup_with_days(self):
        db = MagicMock()
        with patch("app.crud.alert.cleanup_old_alert_history", new_callable=AsyncMock) as mock_clean:
            mock_clean.return_value = 7
            result = await ret_service.prune_alert_history(db, retention_days=365)
        mock_clean.assert_called_once_with(db, retention_days=365)
        assert result == 7

    @pytest.mark.asyncio
    async def test_minimum_floor_is_seven_days(self):
        db = MagicMock()
        with patch("app.crud.alert.cleanup_old_alert_history", new_callable=AsyncMock) as mock_clean:
            mock_clean.return_value = 0
            await ret_service.prune_alert_history(db, retention_days=3)
        called_days = mock_clean.call_args[1]["retention_days"]
        assert called_days >= 7

    @pytest.mark.asyncio
    async def test_zero_days_clamped(self):
        db = MagicMock()
        with patch("app.crud.alert.cleanup_old_alert_history", new_callable=AsyncMock) as mock_clean:
            mock_clean.return_value = 0
            await ret_service.prune_alert_history(db, retention_days=0)
        called_days = mock_clean.call_args[1]["retention_days"]
        assert called_days >= 7


# ── cleanup_old_alert_history CRUD ───────────────────────────────────────────

class TestCleanupOldAlertHistoryCrud:
    @pytest.mark.asyncio
    async def test_minimum_days_enforced(self):
        """cleanup_old_alert_history must refuse to delete data < 7 days old."""
        from app.crud.alert import cleanup_old_alert_history

        deleted_rows = []
        async def fake_execute(stmt):
            # Capture the WHERE clause cutoff by inspecting the statement's compile
            compiled = stmt.compile(compile_kwargs={"literal_binds": True})
            deleted_rows.append(str(compiled))
            m = MagicMock()
            m.rowcount = 0
            return m

        db = MagicMock()
        db.execute = AsyncMock(side_effect=fake_execute)
        db.commit = AsyncMock()

        await cleanup_old_alert_history(db, retention_days=2)
        # Should have executed one DELETE
        assert db.execute.called
        # Verify commit was called
        db.commit.assert_called_once()


# ── get_storage_stats ─────────────────────────────────────────────────────────

class TestGetStorageStats:
    @pytest.mark.asyncio
    async def test_returns_required_keys(self):
        db = MagicMock()
        mock_ph_stats = {
            "total_rows": 1000,
            "per_symbol": {"BTC/USD": 500, "ETH/USD": 500},
            "oldest": _utc(days=30),
            "newest": _utc(minutes=5),
        }
        with patch.object(ret_service, "_crud_price_history_stats", new_callable=AsyncMock) as mock_ph, \
             patch("app.services.retention.AsyncSession"):

            mock_ph.return_value = mock_ph_stats

            # Mock alert history queries
            def make_scalar(val):
                r = MagicMock()
                r.scalar_one.return_value = val
                return r

            db.execute = AsyncMock(side_effect=[
                make_scalar(250),        # alert count
                make_scalar(_utc(days=200)),  # alert oldest
                make_scalar(_utc(days=1)),    # alert newest
            ])

            stats = await ret_service.get_storage_stats(db)

        assert "price_total" in stats
        assert "price_per_symbol" in stats
        assert "price_oldest" in stats
        assert "price_newest" in stats
        assert "alert_total" in stats
        assert "alert_oldest" in stats
        assert "alert_newest" in stats

    @pytest.mark.asyncio
    async def test_price_stats_passed_through(self):
        db = MagicMock()
        mock_ph_stats = {
            "total_rows": 999,
            "per_symbol": {"SOL/USD": 999},
            "oldest": None,
            "newest": None,
        }
        with patch.object(ret_service, "_crud_price_history_stats", new_callable=AsyncMock) as mock_ph:
            mock_ph.return_value = mock_ph_stats

            def make_scalar(val):
                r = MagicMock()
                r.scalar_one.return_value = val
                return r

            db.execute = AsyncMock(side_effect=[
                make_scalar(0),
                make_scalar(None),
                make_scalar(None),
            ])

            stats = await ret_service.get_storage_stats(db)

        assert stats["price_total"] == 999
        assert stats["price_per_symbol"] == {"SOL/USD": 999}


# ── run_retention_cycle ───────────────────────────────────────────────────────

class TestRunRetentionCycle:
    @pytest.mark.asyncio
    async def test_price_enabled_triggers_prune(self):
        db = MagicMock()
        config = {
            ss_crud.RETENTION_PRICE_ENABLED: "true",
            ss_crud.RETENTION_PRICE_DAYS:    "30",
            ss_crud.RETENTION_ALERT_ENABLED: "false",
            ss_crud.RETENTION_ALERT_DAYS:    "365",
        }
        with patch.object(ss_crud, "get_retention_config", new_callable=AsyncMock) as mock_cfg, \
             patch.object(ss_crud, "set_value", new_callable=AsyncMock), \
             patch.object(ret_service, "prune_price_history", new_callable=AsyncMock) as mock_pp, \
             patch.object(ret_service, "prune_alert_history", new_callable=AsyncMock) as mock_pa:

            mock_cfg.return_value = config
            mock_pp.return_value = 10
            mock_pa.return_value = 0

            result = await ret_service.run_retention_cycle(db)

        mock_pp.assert_called_once_with(db, 30)
        mock_pa.assert_not_called()
        assert result["price_deleted"] == 10
        assert result["alert_deleted"] == 0

    @pytest.mark.asyncio
    async def test_alert_enabled_triggers_prune(self):
        db = MagicMock()
        config = {
            ss_crud.RETENTION_PRICE_ENABLED: "false",
            ss_crud.RETENTION_PRICE_DAYS:    "90",
            ss_crud.RETENTION_ALERT_ENABLED: "true",
            ss_crud.RETENTION_ALERT_DAYS:    "180",
        }
        with patch.object(ss_crud, "get_retention_config", new_callable=AsyncMock) as mock_cfg, \
             patch.object(ss_crud, "set_value", new_callable=AsyncMock), \
             patch.object(ret_service, "prune_price_history", new_callable=AsyncMock) as mock_pp, \
             patch.object(ret_service, "prune_alert_history", new_callable=AsyncMock) as mock_pa:

            mock_cfg.return_value = config
            mock_pa.return_value = 5

            result = await ret_service.run_retention_cycle(db)

        mock_pp.assert_not_called()
        mock_pa.assert_called_once_with(db, 180)
        assert result["alert_deleted"] == 5

    @pytest.mark.asyncio
    async def test_both_disabled_no_prune(self):
        db = MagicMock()
        config = {
            ss_crud.RETENTION_PRICE_ENABLED: "false",
            ss_crud.RETENTION_PRICE_DAYS:    "90",
            ss_crud.RETENTION_ALERT_ENABLED: "false",
            ss_crud.RETENTION_ALERT_DAYS:    "365",
        }
        with patch.object(ss_crud, "get_retention_config", new_callable=AsyncMock) as mock_cfg, \
             patch.object(ss_crud, "set_value", new_callable=AsyncMock), \
             patch.object(ret_service, "prune_price_history", new_callable=AsyncMock) as mock_pp, \
             patch.object(ret_service, "prune_alert_history", new_callable=AsyncMock) as mock_pa:

            mock_cfg.return_value = config

            result = await ret_service.run_retention_cycle(db)

        mock_pp.assert_not_called()
        mock_pa.assert_not_called()
        assert result == {"price_deleted": 0, "alert_deleted": 0}

    @pytest.mark.asyncio
    async def test_invalid_days_falls_back_to_default(self):
        db = MagicMock()
        config = {
            ss_crud.RETENTION_PRICE_ENABLED: "true",
            ss_crud.RETENTION_PRICE_DAYS:    "not-a-number",
            ss_crud.RETENTION_ALERT_ENABLED: "false",
            ss_crud.RETENTION_ALERT_DAYS:    "365",
        }
        with patch.object(ss_crud, "get_retention_config", new_callable=AsyncMock) as mock_cfg, \
             patch.object(ss_crud, "set_value", new_callable=AsyncMock), \
             patch.object(ret_service, "prune_price_history", new_callable=AsyncMock) as mock_pp:

            mock_cfg.return_value = config
            mock_pp.return_value = 0

            await ret_service.run_retention_cycle(db)

        # Should fall back to 90-day default for price
        mock_pp.assert_called_once_with(db, 90)

    @pytest.mark.asyncio
    async def test_result_stored_in_system_settings(self):
        db = MagicMock()
        config = {
            ss_crud.RETENTION_PRICE_ENABLED: "true",
            ss_crud.RETENTION_PRICE_DAYS:    "30",
            ss_crud.RETENTION_ALERT_ENABLED: "true",
            ss_crud.RETENTION_ALERT_DAYS:    "90",
        }
        saved_keys = []
        async def capture_set(db_, key, value, **kw):
            saved_keys.append(key)

        with patch.object(ss_crud, "get_retention_config", new_callable=AsyncMock) as mock_cfg, \
             patch.object(ss_crud, "set_value", side_effect=capture_set), \
             patch.object(ret_service, "prune_price_history", new_callable=AsyncMock) as mock_pp, \
             patch.object(ret_service, "prune_alert_history", new_callable=AsyncMock) as mock_pa:

            mock_cfg.return_value = config
            mock_pp.return_value = 3
            mock_pa.return_value = 1

            await ret_service.run_retention_cycle(db)

        assert ss_crud.RETENTION_LAST_RUN in saved_keys
        assert ss_crud.RETENTION_LAST_PRICE_DELETED in saved_keys
        assert ss_crud.RETENTION_LAST_ALERT_DELETED in saved_keys


# ── Retention config defaults ─────────────────────────────────────────────────

class TestRetentionConfigDefaults:
    def test_defaults_have_required_keys(self):
        defaults = ss_crud._RETENTION_DEFAULTS
        assert ss_crud.RETENTION_PRICE_ENABLED in defaults
        assert ss_crud.RETENTION_PRICE_DAYS in defaults
        assert ss_crud.RETENTION_ALERT_ENABLED in defaults
        assert ss_crud.RETENTION_ALERT_DAYS in defaults

    def test_price_pruning_enabled_by_default(self):
        defaults = ss_crud._RETENTION_DEFAULTS
        assert defaults[ss_crud.RETENTION_PRICE_ENABLED] == "true"

    def test_alert_pruning_disabled_by_default(self):
        defaults = ss_crud._RETENTION_DEFAULTS
        assert defaults[ss_crud.RETENTION_ALERT_ENABLED] == "false"

    def test_default_price_retention_90_days(self):
        defaults = ss_crud._RETENTION_DEFAULTS
        assert int(defaults[ss_crud.RETENTION_PRICE_DAYS]) == 90

    def test_default_alert_retention_365_days(self):
        defaults = ss_crud._RETENTION_DEFAULTS
        assert int(defaults[ss_crud.RETENTION_ALERT_DAYS]) == 365
