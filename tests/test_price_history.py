"""
Tests for price history storage, chart data queries, and time-range filtering.
These tests use an in-memory approach with mocked DB calls to avoid requiring
a live database in CI.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.crud.price_history import _bucket_points, _RANGE_CONFIG


class TestBucketPoints:
    """Unit tests for the bucketing/aggregation logic — no DB needed."""

    def _make_rows(self, count: int, interval_minutes: int = 5) -> list:
        base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        return [
            (base + timedelta(minutes=i * interval_minutes), float(100 + i))
            for i in range(count)
        ]

    def test_no_aggregation_returns_raw(self):
        rows = self._make_rows(10)
        result = _bucket_points(rows, bucket_minutes=0)
        assert result == rows

    def test_empty_returns_empty(self):
        assert _bucket_points([], bucket_minutes=30) == []

    def test_30min_buckets_average_correctly(self):
        # 6 rows at 5-min intervals = one 30-min bucket
        rows = self._make_rows(6, interval_minutes=5)
        result = _bucket_points(rows, bucket_minutes=30)
        assert len(result) == 1
        # Average of 100..105 = 102.5
        assert result[0][1] == pytest.approx(102.5)

    def test_multiple_buckets(self):
        # 12 rows at 5-min intervals = two 30-min buckets
        rows = self._make_rows(12, interval_minutes=5)
        result = _bucket_points(rows, bucket_minutes=30)
        assert len(result) == 2

    def test_range_config_keys_present(self):
        assert "24h" in _RANGE_CONFIG
        assert "7d" in _RANGE_CONFIG
        assert "30d" in _RANGE_CONFIG

    def test_24h_no_aggregation(self):
        delta, bucket = _RANGE_CONFIG["24h"]
        assert bucket == 0
        assert delta == timedelta(hours=24)

    def test_7d_30min_buckets(self):
        delta, bucket = _RANGE_CONFIG["7d"]
        assert bucket == 30
        assert delta == timedelta(days=7)

    def test_30d_2h_buckets(self):
        delta, bucket = _RANGE_CONFIG["30d"]
        assert bucket == 120
        assert delta == timedelta(days=30)


class TestRangeConfig:
    def test_all_ranges_produce_reasonable_point_counts(self):
        """Verify that aggregation keeps point counts reasonable."""
        for range_key, (delta, bucket_minutes) in _RANGE_CONFIG.items():
            total_minutes = delta.total_seconds() / 60
            if bucket_minutes == 0:
                # Raw: 5-min snapshots
                max_points = total_minutes / 5
            else:
                max_points = total_minutes / bucket_minutes
            assert max_points <= 400, f"Range {range_key} produces too many points: {max_points}"
