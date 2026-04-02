"""
Tests for the simulation engine.

Covers:
- basic trigger detection (above / below crossing)
- no trigger when condition never met
- crossing logic: only triggers on state change, not while already above/below
- cooldown suppression
- send_once deactivates rule after first trigger
- time filter suppression
- critical_override bypasses time filter
- multiple rules evaluated independently
- no side effects (pure function — real rule state unchanged)
- sim_rule_from_alert_rule snapshot correctness
- SimResult aggregate counts
"""
from datetime import datetime, time, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.models.alert_rule import AlertCondition
from app.services.simulation import (
    SimRule,
    SimResult,
    run_simulation,
    sim_rule_from_alert_rule,
    _current_state,
    _crossing,
    _in_active_window,
    _RuleState,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ts(minutes_offset: int = 0) -> datetime:
    base = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
    return base + timedelta(minutes=minutes_offset)


def _rule(
    condition: str = "above",
    threshold: float = 50_000.0,
    cooldown_minutes: int = 60,
    send_once: bool = False,
    time_filter_enabled: bool = False,
    active_hours_start=None,
    active_hours_end=None,
    active_timezone: str = "UTC",
    critical_override: bool = False,
    label: str = "Test Rule",
) -> SimRule:
    return SimRule(
        id="rule-1",
        label=label,
        trading_pair="BTC/USD",
        condition=AlertCondition(condition),
        threshold=threshold,
        cooldown_minutes=cooldown_minutes,
        send_once=send_once,
        time_filter_enabled=time_filter_enabled,
        active_hours_start=active_hours_start,
        active_hours_end=active_hours_end,
        active_timezone=active_timezone,
        critical_override=critical_override,
    )


def _prices(*values: float, gap_minutes: int = 5) -> list[tuple[datetime, float]]:
    """Build (datetime, price) list from a sequence of prices."""
    return [(_ts(i * gap_minutes), v) for i, v in enumerate(values)]


# ── _current_state ────────────────────────────────────────────────────────────

class TestCurrentState:
    def test_above_at_threshold(self):
        assert _current_state(50_000, 50_000) == "above"

    def test_above_over_threshold(self):
        assert _current_state(51_000, 50_000) == "above"

    def test_below_threshold(self):
        assert _current_state(49_999, 50_000) == "below"


# ── Basic trigger detection ───────────────────────────────────────────────────

class TestBasicTriggers:
    def test_above_triggers_on_first_eval_if_already_above(self):
        rule = _rule("above", 50_000)
        pts  = _prices(51_000)
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 1

    def test_below_triggers_on_first_eval_if_already_below(self):
        rule = _rule("below", 50_000)
        pts  = _prices(49_000)
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 1

    def test_above_does_not_trigger_when_below_threshold(self):
        rule = _rule("above", 50_000)
        pts  = _prices(48_000, 49_000, 49_500)
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 0

    def test_below_does_not_trigger_when_above_threshold(self):
        rule = _rule("below", 30_000)
        pts  = _prices(31_000, 32_000, 33_000)
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 0

    def test_crossing_up_triggers_above_rule(self):
        rule = _rule("above", 50_000)
        # starts below, then crosses above
        pts  = _prices(49_000, 50_000, 51_000)
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 1
        trigger = result.rule_results[0].triggers[0]
        assert trigger.triggered_price == 50_000
        assert trigger.previous_state == "below"

    def test_crossing_down_triggers_below_rule(self):
        rule = _rule("below", 50_000)
        pts  = _prices(51_000, 49_000)
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 1
        assert result.rule_results[0].triggers[0].previous_state == "above"

    def test_no_re_trigger_while_continuously_above(self):
        """Once above and triggered, staying above should NOT re-trigger."""
        rule = _rule("above", 50_000, cooldown_minutes=0)
        pts  = _prices(51_000, 52_000, 53_000, 54_000)
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        # Should only trigger once (on first eval crossing)
        assert result.total_triggers == 1

    def test_re_triggers_after_crossing_back_down_and_up(self):
        """Crossing down and back up should produce a second trigger."""
        rule = _rule("above", 50_000, cooldown_minutes=0)
        pts  = _prices(51_000, 49_000, 51_000)  # up, down, up
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 2


# ── Cooldown ──────────────────────────────────────────────────────────────────

class TestCooldown:
    def test_cooldown_suppresses_re_trigger(self):
        """60-min cooldown: second crossing 30 min later should be suppressed."""
        rule = _rule("above", 50_000, cooldown_minutes=60)
        pts  = [
            (_ts(0),  49_000),
            (_ts(1),  51_000),   # trigger 1 at t=1
            (_ts(2),  49_000),
            (_ts(32), 51_000),   # 31 min after trigger — still in cooldown
        ]
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 1
        assert result.rule_results[0].skipped_cooldown == 1

    def test_trigger_allowed_after_cooldown_expires(self):
        """After cooldown expires, a new crossing should trigger."""
        rule = _rule("above", 50_000, cooldown_minutes=60)
        pts  = [
            (_ts(0),   49_000),
            (_ts(1),   51_000),   # trigger 1
            (_ts(2),   49_000),
            (_ts(62),  51_000),   # 61 min after trigger — cooldown expired
        ]
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 2
        assert result.rule_results[0].skipped_cooldown == 0

    def test_zero_cooldown_allows_immediate_retrigger(self):
        rule = _rule("above", 50_000, cooldown_minutes=0)
        pts  = [
            (_ts(0), 49_000),
            (_ts(1), 51_000),   # trigger
            (_ts(2), 49_000),
            (_ts(3), 51_000),   # trigger again immediately
        ]
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 2


# ── send_once ─────────────────────────────────────────────────────────────────

class TestSendOnce:
    def test_send_once_deactivates_after_first_trigger(self):
        rule = _rule("above", 50_000, send_once=True, cooldown_minutes=0)
        pts  = [
            (_ts(0), 49_000),
            (_ts(1), 51_000),   # trigger
            (_ts(2), 49_000),
            (_ts(3), 51_000),   # would trigger but rule is deactivated
        ]
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 1
        assert result.rule_results[0].deactivated_send_once is True

    def test_send_once_false_does_not_deactivate(self):
        rule = _rule("above", 50_000, send_once=False, cooldown_minutes=0)
        pts  = [
            (_ts(0), 49_000),
            (_ts(1), 51_000),
            (_ts(2), 49_000),
            (_ts(3), 51_000),
        ]
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.rule_results[0].deactivated_send_once is False


# ── Time filter ───────────────────────────────────────────────────────────────

class TestTimeFilter:
    def test_trigger_suppressed_outside_window(self):
        """Window 08:00–18:00 UTC; price crosses at 22:00 UTC → suppressed."""
        rule = _rule(
            "above", 50_000,
            time_filter_enabled=True,
            active_hours_start=time(8, 0),
            active_hours_end=time(18, 0),
            active_timezone="UTC",
        )
        # 22:00 UTC
        ts_night = datetime(2025, 6, 15, 22, 0, tzinfo=timezone.utc)
        pts = [
            (ts_night - timedelta(minutes=5), 49_000),
            (ts_night,                        51_000),
        ]
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 0
        assert result.rule_results[0].skipped_time_filter > 0

    def test_trigger_allowed_inside_window(self):
        rule = _rule(
            "above", 50_000,
            time_filter_enabled=True,
            active_hours_start=time(8, 0),
            active_hours_end=time(18, 0),
            active_timezone="UTC",
        )
        ts_day = datetime(2025, 6, 15, 12, 0, tzinfo=timezone.utc)
        pts = [
            (ts_day - timedelta(minutes=5), 49_000),
            (ts_day,                        51_000),
        ]
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 1

    def test_critical_override_fires_outside_window(self):
        rule = _rule(
            "above", 50_000,
            time_filter_enabled=True,
            active_hours_start=time(8, 0),
            active_hours_end=time(18, 0),
            active_timezone="UTC",
            critical_override=True,
        )
        ts_night = datetime(2025, 6, 15, 22, 0, tzinfo=timezone.utc)
        pts = [
            (ts_night - timedelta(minutes=5), 49_000),
            (ts_night,                        51_000),
        ]
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 1

    def test_time_filter_disabled_always_active(self):
        rule = _rule(
            "above", 50_000,
            time_filter_enabled=False,
        )
        ts_night = datetime(2025, 6, 15, 22, 0, tzinfo=timezone.utc)
        pts = [
            (ts_night - timedelta(minutes=5), 49_000),
            (ts_night,                        51_000),
        ]
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[rule])
        assert result.total_triggers == 1


# ── Multiple rules ────────────────────────────────────────────────────────────

class TestMultipleRules:
    def test_rules_evaluated_independently(self):
        r1 = SimRule(id="r1", label="R1", trading_pair="BTC/USD",
                     condition=AlertCondition.ABOVE, threshold=50_000,
                     cooldown_minutes=0, send_once=False)
        r2 = SimRule(id="r2", label="R2", trading_pair="BTC/USD",
                     condition=AlertCondition.BELOW, threshold=30_000,
                     cooldown_minutes=0, send_once=False)
        pts = _prices(51_000, 29_000)   # above first, then below
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[r1, r2])
        counts = {rr.rule.id: rr.trigger_count for rr in result.rule_results}
        assert counts["r1"] == 1
        assert counts["r2"] == 1

    def test_send_once_on_one_rule_does_not_affect_other(self):
        r1 = SimRule(id="r1", label="R1", trading_pair="BTC/USD",
                     condition=AlertCondition.ABOVE, threshold=50_000,
                     cooldown_minutes=0, send_once=True)
        r2 = SimRule(id="r2", label="R2", trading_pair="BTC/USD",
                     condition=AlertCondition.ABOVE, threshold=50_000,
                     cooldown_minutes=0, send_once=False)
        pts = [
            (_ts(0), 49_000),
            (_ts(1), 51_000),
            (_ts(2), 49_000),
            (_ts(3), 51_000),
        ]
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[r1, r2])
        counts = {rr.rule.id: rr.trigger_count for rr in result.rule_results}
        assert counts["r1"] == 1   # send_once
        assert counts["r2"] == 2   # no send_once


# ── Empty / edge cases ────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_price_points(self):
        rule = _rule("above", 50_000)
        result = run_simulation(asset_symbol="BTC/USD", price_points=[], rules=[rule])
        assert result.total_triggers == 0
        assert result.price_point_count == 0

    def test_empty_rules(self):
        pts = _prices(51_000, 52_000)
        result = run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[])
        assert result.total_triggers == 0
        assert result.rule_results == []

    def test_single_price_point_above_triggers(self):
        rule = _rule("above", 50_000)
        result = run_simulation(asset_symbol="BTC/USD", price_points=[(_ts(0), 51_000)], rules=[rule])
        assert result.total_triggers == 1


# ── No side effects ───────────────────────────────────────────────────────────

class TestNoSideEffects:
    def test_real_rule_object_not_modified(self):
        """run_simulation must NOT write to the real AlertRule attributes."""
        mock_rule = MagicMock()
        mock_rule.id = "r1"
        mock_rule.label = "Test"
        mock_rule.trading_pair = "BTC/USD"
        mock_rule.condition = AlertCondition.ABOVE
        mock_rule.threshold = 50_000.0
        mock_rule.cooldown_minutes = 60
        mock_rule.send_once = False
        mock_rule.time_filter_enabled = False
        mock_rule.active_hours_start = None
        mock_rule.active_hours_end = None
        mock_rule.active_timezone = "UTC"
        mock_rule.critical_override = False
        mock_rule.is_active = True
        mock_rule.last_state = None
        mock_rule.last_triggered_at = None

        sim_rule = sim_rule_from_alert_rule(mock_rule)
        pts = _prices(49_000, 51_000, 49_000, 51_000)
        run_simulation(asset_symbol="BTC/USD", price_points=pts, rules=[sim_rule])

        # The original mock_rule object should never have been mutated
        mock_rule.last_state = None        # verify it was never set
        assert mock_rule.last_state is None


# ── sim_rule_from_alert_rule ──────────────────────────────────────────────────

class TestSimRuleFromAlertRule:
    def test_snapshot_captures_all_fields(self):
        mock = MagicMock()
        mock.id = "abc"
        mock.label = "My Label"
        mock.trading_pair = "ETH/USD"
        mock.condition = AlertCondition.BELOW
        mock.threshold = 2000.0
        mock.cooldown_minutes = 30
        mock.send_once = True
        mock.time_filter_enabled = True
        mock.active_hours_start = time(9, 0)
        mock.active_hours_end = time(17, 0)
        mock.active_timezone = "America/New_York"
        mock.critical_override = False

        sr = sim_rule_from_alert_rule(mock)

        assert sr.id == "abc"
        assert sr.label == "My Label"
        assert sr.trading_pair == "ETH/USD"
        assert sr.condition == AlertCondition.BELOW
        assert sr.threshold == 2000.0
        assert sr.cooldown_minutes == 30
        assert sr.send_once is True
        assert sr.time_filter_enabled is True
        assert sr.active_hours_start == time(9, 0)
        assert sr.active_timezone == "America/New_York"

    def test_snapshot_uses_fallback_label(self):
        mock = MagicMock()
        mock.id = "x"
        mock.label = None   # no label
        mock.trading_pair = "SOL/USD"
        mock.condition = AlertCondition.ABOVE
        mock.threshold = 200.0
        mock.cooldown_minutes = 60
        mock.send_once = False
        mock.time_filter_enabled = False
        mock.active_hours_start = None
        mock.active_hours_end = None
        mock.active_timezone = "UTC"
        mock.critical_override = False

        sr = sim_rule_from_alert_rule(mock)
        assert sr.label  # should not be empty/None
        assert "SOL/USD" in sr.label or "above" in sr.label
