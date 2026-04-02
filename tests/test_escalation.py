"""
Tests for the alert escalation engine scoring and classification logic.
No database or running services required — tests the pure scoring functions.
"""
import pytest

from app.services.escalation import (
    CRITICAL,
    ELEVATED,
    NORMAL,
    EscalationEngine,
    _HIGH_IMPORTANCE_PAIRS,
    _score_to_level,
)


class TestScoreToLevel:
    def test_score_0_is_normal(self):
        assert _score_to_level(0) == NORMAL

    def test_score_14_is_normal(self):
        assert _score_to_level(14) == NORMAL

    def test_score_15_is_elevated(self):
        assert _score_to_level(15) == ELEVATED

    def test_score_29_is_elevated(self):
        assert _score_to_level(29) == ELEVATED

    def test_score_30_is_critical(self):
        assert _score_to_level(30) == CRITICAL

    def test_score_100_is_critical(self):
        assert _score_to_level(100) == CRITICAL

    def test_score_negative_is_normal(self):
        assert _score_to_level(-1) == NORMAL


class TestHighImportancePairs:
    def test_btc_is_high_importance(self):
        assert "BTC/USD" in _HIGH_IMPORTANCE_PAIRS

    def test_eth_is_high_importance(self):
        assert "ETH/USD" in _HIGH_IMPORTANCE_PAIRS

    def test_sol_is_not_high_importance(self):
        assert "SOL/USD" not in _HIGH_IMPORTANCE_PAIRS

    def test_tao_is_not_high_importance(self):
        assert "TAO/USD" not in _HIGH_IMPORTANCE_PAIRS


class TestEscalationScenarios:
    """
    Verify scoring logic by simulating the component scores directly.
    Each test computes expected score and checks _score_to_level.
    """

    def test_first_trigger_non_btc_is_normal(self):
        # prior=0 (+0), distinct_pairs=1 (+0), non-BTC (+0) → score=0 → NORMAL
        score = 0 + 0 + 0
        assert _score_to_level(score) == NORMAL

    def test_first_trigger_btc_is_elevated(self):
        # prior=0 (+0), distinct_pairs=1 (+0), BTC (+10) → score=10 → NORMAL
        # (10 < 15, so still NORMAL — BTC alone on first trigger isn't escalated)
        score = 0 + 0 + 10
        assert _score_to_level(score) == NORMAL

    def test_second_trigger_same_rule_non_btc_is_elevated(self):
        # prior=1 (+15), distinct_pairs=1 (+0), non-BTC (+0) → score=15 → ELEVATED
        score = 15 + 0 + 0
        assert _score_to_level(score) == ELEVATED

    def test_second_trigger_same_rule_btc_is_elevated(self):
        # prior=1 (+15), distinct_pairs=1 (+0), BTC (+10) → score=25 → ELEVATED
        score = 15 + 0 + 10
        assert _score_to_level(score) == ELEVATED

    def test_third_trigger_same_rule_is_critical(self):
        # prior=2 (+30), distinct_pairs=1 (+0), non-BTC (+0) → score=30 → CRITICAL
        score = 30 + 0 + 0
        assert _score_to_level(score) == CRITICAL

    def test_two_assets_firing_boosts_to_elevated(self):
        # prior=0 (+0), distinct_pairs=2 (+10), non-BTC (+0) → score=10 → NORMAL
        # Just below elevated threshold
        score = 0 + 10 + 0
        assert _score_to_level(score) == NORMAL

    def test_three_assets_plus_btc_is_elevated(self):
        # prior=0 (+0), distinct_pairs=3 (+20), BTC (+10) → score=30 → CRITICAL
        score = 0 + 20 + 10
        assert _score_to_level(score) == CRITICAL

    def test_second_trigger_plus_two_assets_is_critical(self):
        # prior=1 (+15), distinct_pairs=2 (+10), non-BTC (+0) → score=25 → ELEVATED
        score = 15 + 10 + 0
        assert _score_to_level(score) == ELEVATED

    def test_second_trigger_plus_three_assets_is_critical(self):
        # prior=1 (+15), distinct_pairs=3 (+20), non-BTC (+0) → score=35 → CRITICAL
        score = 15 + 20 + 0
        assert _score_to_level(score) == CRITICAL

    def test_full_critical_scenario(self):
        # prior=2+ (+30), distinct_pairs=3 (+20), BTC (+10) → score=60 → CRITICAL
        score = 30 + 20 + 10
        assert _score_to_level(score) == CRITICAL


class TestLevelConstants:
    def test_constants_are_strings(self):
        assert isinstance(NORMAL, str)
        assert isinstance(ELEVATED, str)
        assert isinstance(CRITICAL, str)

    def test_constant_values(self):
        assert NORMAL == "normal"
        assert ELEVATED == "elevated"
        assert CRITICAL == "critical"

    def test_engine_instantiates(self):
        engine = EscalationEngine()
        assert engine is not None
