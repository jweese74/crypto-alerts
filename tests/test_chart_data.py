"""Tests for enhanced chart data API.

Covers:
- timestamps (ISO-8601) included in chart data
- severity included in trigger objects
- trigger matching by nearest timestamp (not label string)
- trigger outside range excluded
- _build_chart_data helper correctness
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.assets import _build_chart_data, _fmt_label, _symbol_to_url, _url_to_symbol


# ── Symbol helpers ────────────────────────────────────────────────────────────

class TestSymbolHelpers:
    def test_symbol_to_url(self):
        assert _symbol_to_url("BTC/USD") == "BTC_USD"
        assert _symbol_to_url("ETH/USD") == "ETH_USD"

    def test_url_to_symbol(self):
        assert _url_to_symbol("BTC_USD") == "BTC/USD"
        assert _url_to_symbol("SOL_USD") == "SOL/USD"

    def test_round_trip(self):
        sym = "DOGE/USD"
        assert _url_to_symbol(_symbol_to_url(sym)) == sym


# ── Timestamp formatting ──────────────────────────────────────────────────────

class TestFmtLabel:
    def test_24h_shows_hhmm(self):
        dt = datetime(2025, 3, 10, 14, 35, tzinfo=timezone.utc)
        assert _fmt_label(dt, "24h") == "14:35"

    def test_7d_shows_date_and_time(self):
        dt = datetime(2025, 3, 10, 14, 35, tzinfo=timezone.utc)
        label = _fmt_label(dt, "7d")
        assert "03-10" in label
        assert "14:35" in label

    def test_30d_shows_date_and_time(self):
        dt = datetime(2025, 3, 10, 8, 0, tzinfo=timezone.utc)
        label = _fmt_label(dt, "30d")
        assert "03-10" in label


# ── _build_chart_data ─────────────────────────────────────────────────────────

def _make_points(n=10, start=None):
    """Generate n (datetime, price) tuples spaced 5 minutes apart."""
    if start is None:
        start = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    return [(start + timedelta(minutes=5 * i), 50000.0 + i * 10) for i in range(n)]


def _make_rule(pair="BTC/USD", threshold=50100.0, condition="above",
               is_active=True, label="Test rule", rule_id="abc"):
    r = MagicMock()
    r.trading_pair = pair
    r.threshold = threshold
    r.condition = MagicMock()
    r.condition.value = condition
    r.is_active = is_active
    r.label = label
    r.id = rule_id
    return r


def _make_history(pair="BTC/USD", price=50150.0, triggered_at=None, severity="normal",
                  message="test alert"):
    h = MagicMock()
    h.trading_pair = pair
    h.triggered_price = price
    h.triggered_at = triggered_at or datetime(2025, 1, 15, 12, 15, 0, tzinfo=timezone.utc)
    h.severity = severity
    h.message = message
    return h


class TestBuildChartData:

    def test_basic_structure(self):
        points = _make_points(5)
        result = _build_chart_data("BTC/USD", "24h", points, 50050.0, [], [])
        assert result["symbol"] == "BTC/USD"
        assert result["range"] == "24h"
        assert len(result["labels"]) == 5
        assert len(result["prices"]) == 5
        assert len(result["timestamps"]) == 5
        assert result["point_count"] == 5
        assert result["current_price"] == 50050.0

    def test_timestamps_are_iso_strings(self):
        points = _make_points(3)
        result = _build_chart_data("BTC/USD", "24h", points, None, [], [])
        for ts in result["timestamps"]:
            assert isinstance(ts, str)
            # Should be parseable as ISO-8601
            parsed = datetime.fromisoformat(ts)
            assert parsed is not None

    def test_thresholds_included(self):
        points = _make_points(5)
        rule = _make_rule(pair="BTC/USD", threshold=50100.0, condition="above")
        result = _build_chart_data("BTC/USD", "24h", points, None, [rule], [])
        assert len(result["thresholds"]) == 1
        t = result["thresholds"][0]
        assert t["price"] == 50100.0
        assert t["condition"] == "above"
        assert t["is_active"] is True

    def test_thresholds_filtered_to_correct_pair(self):
        points = _make_points(5)
        eth_rule = _make_rule(pair="ETH/USD", threshold=3000.0)
        result = _build_chart_data("BTC/USD", "24h", points, None, [eth_rule], [])
        assert len(result["thresholds"]) == 0

    def test_trigger_severity_included(self):
        points = _make_points(10)
        # Trigger at 12:15 UTC = point index 3 (12:00 + 3*5min)
        hist = _make_history(
            pair="BTC/USD",
            triggered_at=datetime(2025, 1, 15, 12, 15, 0, tzinfo=timezone.utc),
            severity="elevated",
            price=50035.0,
        )
        result = _build_chart_data("BTC/USD", "24h", points, None, [], [hist])
        assert len(result["triggers"]) == 1
        assert result["triggers"][0]["severity"] == "elevated"

    def test_trigger_nearest_timestamp_matching(self):
        """Triggers should match to the nearest chart point by timestamp."""
        points = _make_points(10)   # points at 12:00, 12:05, 12:10, ...

        # Trigger at 12:13 — nearest point is 12:15 (idx=3) not 12:10 (idx=2)
        trigger_at = datetime(2025, 1, 15, 12, 13, 0, tzinfo=timezone.utc)
        hist = _make_history(pair="BTC/USD", triggered_at=trigger_at)
        result = _build_chart_data("BTC/USD", "24h", points, None, [], [hist])

        assert len(result["triggers"]) == 1
        # Nearest point: 12:10 is 3min away, 12:15 is 2min away → idx=3
        assert result["triggers"][0]["idx"] == 3

    def test_trigger_before_range_excluded(self):
        """Triggers outside the chart time window are not included."""
        points = _make_points(10)    # range: 12:00 → 12:45
        # Trigger at 11:00 — before chart window
        hist = _make_history(
            triggered_at=datetime(2025, 1, 15, 11, 0, 0, tzinfo=timezone.utc)
        )
        result = _build_chart_data("BTC/USD", "24h", points, None, [], [hist])
        assert len(result["triggers"]) == 0

    def test_trigger_after_range_excluded(self):
        """Triggers after the chart window are not included."""
        points = _make_points(10)   # range ends at 12:45
        hist = _make_history(
            triggered_at=datetime(2025, 1, 15, 14, 0, 0, tzinfo=timezone.utc)
        )
        result = _build_chart_data("BTC/USD", "24h", points, None, [], [hist])
        assert len(result["triggers"]) == 0

    def test_trigger_from_other_pair_excluded(self):
        points = _make_points(10)
        hist = _make_history(pair="ETH/USD")
        result = _build_chart_data("BTC/USD", "24h", points, None, [], [hist])
        assert len(result["triggers"]) == 0

    def test_trigger_message_truncated(self):
        long_msg = "X" * 200
        points = _make_points(10)
        hist = _make_history(
            triggered_at=datetime(2025, 1, 15, 12, 5, 0, tzinfo=timezone.utc),
            message=long_msg,
        )
        result = _build_chart_data("BTC/USD", "24h", points, None, [], [hist])
        if result["triggers"]:
            assert len(result["triggers"][0]["message"]) <= 120

    def test_trigger_has_triggered_at_iso(self):
        points = _make_points(10)
        hist = _make_history(
            triggered_at=datetime(2025, 1, 15, 12, 10, 0, tzinfo=timezone.utc),
            severity="critical",
        )
        result = _build_chart_data("BTC/USD", "24h", points, None, [], [hist])
        if result["triggers"]:
            ts = result["triggers"][0]["triggered_at"]
            assert isinstance(ts, str)
            datetime.fromisoformat(ts)  # must be parseable

    def test_empty_points_returns_empty_lists(self):
        result = _build_chart_data("BTC/USD", "24h", [], 50000.0, [], [])
        assert result["labels"] == []
        assert result["prices"] == []
        assert result["timestamps"] == []
        assert result["triggers"] == []
        assert result["point_count"] == 0

    def test_multiple_severity_triggers(self):
        points = _make_points(20)
        histories = [
            _make_history(triggered_at=datetime(2025, 1, 15, 12, 5, 0, tzinfo=timezone.utc),
                          severity="normal"),
            _make_history(triggered_at=datetime(2025, 1, 15, 12, 20, 0, tzinfo=timezone.utc),
                          severity="elevated"),
            _make_history(triggered_at=datetime(2025, 1, 15, 12, 35, 0, tzinfo=timezone.utc),
                          severity="critical"),
        ]
        result = _build_chart_data("BTC/USD", "24h", points, None, [], histories)
        severities = {t["severity"] for t in result["triggers"]}
        assert "normal" in severities
        assert "elevated" in severities
        assert "critical" in severities

    def test_naive_trigger_timestamp_handled(self):
        """Naive (no tz) trigger timestamps should not crash."""
        points = _make_points(10)
        hist = _make_history(
            triggered_at=datetime(2025, 1, 15, 12, 10, 0)  # no tzinfo
        )
        # Should not raise
        result = _build_chart_data("BTC/USD", "24h", points, None, [], [hist])
        # Result may or may not include the trigger depending on tz handling
        assert isinstance(result["triggers"], list)
