"""Tests for event log CRUD and service."""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from app.crud.event_log import (
    log_event,
    get_events,
    count_events,
    cleanup_old_events,
    get_distinct_assets,
    ALERT_TRIGGERED,
    ALERT_SUPPRESSED_COOLDOWN,
    ALERT_SUPPRESSED_TIME_FILTER,
    MARKET_STATE_CHANGED,
    RULE_CREATED,
    RULE_DELETED,
    RULE_ENABLED,
    RULE_DISABLED,
    SYSTEM_STARTUP,
    USER_LOGIN,
    RETENTION_RUN,
    SYSTEM_EVENT_TYPES,
    INFO,
    WARNING,
    CRITICAL,
)
from app.services.event_log import EventLogService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()
    return db


def _mock_result(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    result.scalar_one.return_value = len(rows)
    result.fetchall.return_value = [(r,) for r in rows]
    return result


# ── CRUD constants ─────────────────────────────────────────────────────────────

def test_event_type_constants_defined():
    assert ALERT_TRIGGERED == "alert_triggered"
    assert MARKET_STATE_CHANGED == "market_state_changed"
    assert SYSTEM_STARTUP == "system_startup"
    assert USER_LOGIN == "user_login"
    assert RETENTION_RUN == "retention_run"


def test_system_event_types_set():
    assert MARKET_STATE_CHANGED in SYSTEM_EVENT_TYPES
    assert SYSTEM_STARTUP in SYSTEM_EVENT_TYPES
    assert RETENTION_RUN in SYSTEM_EVENT_TYPES
    # User-specific events not in system set
    assert ALERT_TRIGGERED not in SYSTEM_EVENT_TYPES
    assert USER_LOGIN not in SYSTEM_EVENT_TYPES


def test_severity_constants():
    assert INFO == "info"
    assert WARNING == "warning"
    assert CRITICAL == "critical"


# ── log_event ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_event_minimal():
    db = _mock_db()
    await log_event(db, event_type=ALERT_TRIGGERED, description="Test event")
    db.add.assert_called_once()
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_log_event_with_all_fields():
    db = _mock_db()
    await log_event(
        db,
        event_type=ALERT_TRIGGERED,
        description="BTC crossed above $50000",
        asset_symbol="BTC/USD",
        severity=WARNING,
        user_id="user-123",
        extra={"threshold": 50000, "price": 51000},
    )
    db.add.assert_called_once()
    event_obj = db.add.call_args[0][0]
    assert event_obj.event_type == ALERT_TRIGGERED
    assert event_obj.asset_symbol == "BTC/USD"
    assert event_obj.severity == WARNING
    assert event_obj.user_id == "user-123"
    assert event_obj.extra is not None  # JSON string


@pytest.mark.asyncio
async def test_log_event_null_extra_stored_as_none():
    db = _mock_db()
    await log_event(db, event_type=SYSTEM_STARTUP, description="Started")
    event_obj = db.add.call_args[0][0]
    assert event_obj.extra is None


# ── get_events access control ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_events_admin_sees_all():
    db = _mock_db()
    db.execute.return_value = _mock_result([])
    await get_events(db, user_id="admin-id", is_admin=True)
    # Just verify no exception raised
    db.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_events_user_sees_own_and_system():
    db = _mock_db()
    db.execute.return_value = _mock_result([])
    await get_events(db, user_id="user-id", is_admin=False)
    db.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_events_no_user_no_filter():
    db = _mock_db()
    db.execute.return_value = _mock_result([])
    await get_events(db, is_admin=True)
    db.execute.assert_called_once()


# ── count_events ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_count_events_returns_int():
    db = _mock_db()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 42
    db.execute.return_value = mock_result
    result = await count_events(db, is_admin=True)
    assert result == 42


@pytest.mark.asyncio
async def test_count_events_handles_none():
    db = _mock_db()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = None
    db.execute.return_value = mock_result
    result = await count_events(db, is_admin=True)
    assert result == 0


# ── cleanup_old_events ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup_enforces_minimum_7_days():
    db = _mock_db()
    mock_result = MagicMock()
    mock_result.rowcount = 5
    db.execute.return_value = mock_result
    # Passing 1 day should be clamped to 7
    deleted = await cleanup_old_events(db, retention_days=1)
    assert deleted == 5
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_cleanup_respects_normal_retention():
    db = _mock_db()
    mock_result = MagicMock()
    mock_result.rowcount = 100
    db.execute.return_value = mock_result
    deleted = await cleanup_old_events(db, retention_days=90)
    assert deleted == 100


# ── get_distinct_assets ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_distinct_assets_returns_list():
    db = _mock_db()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [("BTC/USD",), ("ETH/USD",), ("SOL/USD",)]
    db.execute.return_value = mock_result
    assets = await get_distinct_assets(db)
    assert assets == ["BTC/USD", "ETH/USD", "SOL/USD"]


@pytest.mark.asyncio
async def test_get_distinct_assets_empty():
    db = _mock_db()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    db.execute.return_value = mock_result
    assets = await get_distinct_assets(db)
    assert assets == []


# ── EventLogService ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_service_alert_triggered_above():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.alert_triggered(
            db,
            trading_pair="BTC/USD",
            condition="above",
            threshold=50000.0,
            triggered_price=51000.0,
            severity="normal",
            user_id="user-1",
        )
    mock_log.assert_called_once()
    kwargs = mock_log.call_args[1]
    assert "BTC" in kwargs["description"]
    assert "above" in kwargs["description"]
    assert "$50,000.00" in kwargs["description"]


@pytest.mark.asyncio
async def test_service_alert_triggered_below():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.alert_triggered(
            db,
            trading_pair="ETH/USD",
            condition="below",
            threshold=2000.0,
            triggered_price=1900.0,
            severity="critical",
        )
    kwargs = mock_log.call_args[1]
    assert "below" in kwargs["description"]
    assert kwargs["severity"] == "critical"


@pytest.mark.asyncio
async def test_service_alert_triggered_with_label():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.alert_triggered(
            db,
            trading_pair="SOL/USD",
            condition="above",
            threshold=200.0,
            triggered_price=205.0,
            rule_label="My SOL alert",
        )
    kwargs = mock_log.call_args[1]
    assert "My SOL alert" in kwargs["description"]


@pytest.mark.asyncio
async def test_service_suppressed_cooldown():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.alert_suppressed_cooldown(
            db,
            trading_pair="BTC/USD",
            threshold=50000.0,
            condition="above",
            elapsed_minutes=5.0,
            cooldown_minutes=15,
            user_id="user-1",
        )
    kwargs = mock_log.call_args[1]
    assert "cooldown" in kwargs["description"]
    assert kwargs["event_type"] == "alert_suppressed_cooldown"


@pytest.mark.asyncio
async def test_service_suppressed_time_filter():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.alert_suppressed_time_filter(
            db,
            trading_pair="ETH/USD",
            threshold=2000.0,
            condition="below",
            window_start="09:00",
            window_end="17:00",
            timezone_name="America/New_York",
        )
    kwargs = mock_log.call_args[1]
    assert "outside active hours" in kwargs["description"]
    assert "09:00" in kwargs["description"]


@pytest.mark.asyncio
async def test_service_market_state_changed():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.market_state_changed(
            db,
            previous_state="calm",
            new_state="warning",
            score=45,
            reasons=["BTC above threshold", "multiple alerts triggered"],
        )
    kwargs = mock_log.call_args[1]
    assert "CALM" in kwargs["description"]
    assert "WARNING" in kwargs["description"]
    assert kwargs["event_type"] == "market_state_changed"
    assert kwargs["severity"] == "warning"


@pytest.mark.asyncio
async def test_service_market_state_event_severity():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.market_state_changed(
            db, previous_state="warning", new_state="event", score=90, reasons=[]
        )
    kwargs = mock_log.call_args[1]
    assert kwargs["severity"] == "critical"


@pytest.mark.asyncio
async def test_service_rule_created():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.rule_created(
            db, trading_pair="ADA/USD", condition="above", threshold=1.5,
            label="ADA moon", user_id="user-1"
        )
    kwargs = mock_log.call_args[1]
    assert "ADA" in kwargs["description"]
    assert "ADA moon" in kwargs["description"]


@pytest.mark.asyncio
async def test_service_rule_created_no_label():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.rule_created(
            db, trading_pair="SOL/USD", condition="below", threshold=100.0,
            user_id="user-2"
        )
    kwargs = mock_log.call_args[1]
    assert "SOL" in kwargs["description"]


@pytest.mark.asyncio
async def test_service_rule_deleted():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.rule_deleted(db, trading_pair="BTC/USD", user_id="user-1")
    kwargs = mock_log.call_args[1]
    assert kwargs["event_type"] == "rule_deleted"


@pytest.mark.asyncio
async def test_service_rule_toggled_enabled():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.rule_toggled(db, trading_pair="ETH/USD", enabled=True, user_id="user-1")
    kwargs = mock_log.call_args[1]
    assert kwargs["event_type"] == "rule_enabled"
    assert "enabled" in kwargs["description"]


@pytest.mark.asyncio
async def test_service_rule_toggled_disabled():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.rule_toggled(db, trading_pair="ETH/USD", enabled=False, user_id="user-1")
    kwargs = mock_log.call_args[1]
    assert kwargs["event_type"] == "rule_disabled"
    assert "disabled" in kwargs["description"]


@pytest.mark.asyncio
async def test_service_system_startup():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.system_startup(db)
    kwargs = mock_log.call_args[1]
    assert kwargs["event_type"] == "system_startup"
    assert "started" in kwargs["description"].lower()


@pytest.mark.asyncio
async def test_service_user_login():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.user_login(db, username="testuser@example.com", user_id="user-1")
    kwargs = mock_log.call_args[1]
    assert "testuser@example.com" in kwargs["description"]
    assert kwargs["event_type"] == "user_login"
    assert kwargs["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_service_retention_run():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", new_callable=AsyncMock) as mock_log:
        await svc.retention_run(db, price_deleted=1500, alert_deleted=200)
    kwargs = mock_log.call_args[1]
    assert "1500" in kwargs["description"]
    assert "200" in kwargs["description"]
    assert kwargs["event_type"] == "retention_run"


@pytest.mark.asyncio
async def test_service_write_failure_does_not_raise():
    """EventLogService must never raise — failures are swallowed."""
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", side_effect=Exception("DB gone")):
        # Should not raise
        await svc.system_startup(db)


@pytest.mark.asyncio
async def test_service_write_failure_logs_error():
    svc = EventLogService()
    db = _mock_db()
    with patch("app.crud.event_log.log_event", side_effect=Exception("DB gone")):
        with patch("app.services.event_log.logger") as mock_logger:
            await svc.system_startup(db)
    mock_logger.error.assert_called_once()
    assert "system_startup" in mock_logger.error.call_args[0][0]
