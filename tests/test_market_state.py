"""
Tests for Market State Engine scoring and classification logic.
No database or running services required.
"""
import pytest

from app.services.market_state import (
    CALM,
    EVENT,
    RISK,
    WARNING,
    _price_move_score,
    _score_to_state,
    _BTC_MOVE_SCORES,
    _ETH_MOVE_SCORES,
    state_colour,
    state_icon,
)


class TestScoreToState:
    def test_score_0_is_calm(self):
        assert _score_to_state(0) == CALM

    def test_score_9_is_calm(self):
        assert _score_to_state(9) == CALM

    def test_score_10_is_warning(self):
        assert _score_to_state(10) == WARNING

    def test_score_29_is_warning(self):
        assert _score_to_state(29) == WARNING

    def test_score_30_is_risk(self):
        assert _score_to_state(30) == RISK

    def p_score_59_is_risk(self):
        assert _score_to_state(59) == RISK

    def test_score_60_is_event(self):
        assert _score_to_state(60) == EVENT

    def test_score_100_is_event(self):
        assert _score_to_state(100) == EVENT

    def test_score_negative_is_calm(self):
        # Defensive: shouldn't happen but must not crash
        assert _score_to_state(-5) == CALM


class TestPriceMoveScore:
    def test_btc_no_move_zero_pts(self):
        assert _price_move_score(0.5, _BTC_MOVE_SCORES) == 0

    def test_btc_2pct_move(self):
        assert _price_move_score(2.0, _BTC_MOVE_SCORES) == 8

    def test_btc_5pct_move(self):
        assert _price_move_score(5.0, _BTC_MOVE_SCORES) == 20

    def test_btc_10pct_move(self):
        assert _price_move_score(10.0, _BTC_MOVE_SCORES) == 35

    def test_btc_15pct_move(self):
        # Above highest threshold, still returns highest tier
        assert _price_move_score(15.0, _BTC_MOVE_SCORES) == 35

    def test_eth_no_move_zero_pts(self):
        assert _price_move_score(1.0, _ETH_MOVE_SCORES) == 0

    def test_eth_2pct_move(self):
        assert _price_move_score(2.5, _ETH_MOVE_SCORES) == 5

    def test_eth_5pct_move(self):
        assert _price_move_score(5.5, _ETH_MOVE_SCORES) == 15

    def test_eth_10pct_move(self):
        assert _price_move_score(10.1, _ETH_MOVE_SCORES) == 25


class TestStateBoundaries:
    """Verify state transitions using realistic scoring scenarios."""

    def test_one_minor_alert_is_warning(self):
        # 1 non-BTC/ETH alert: +10 → WARNING
        score = 10
        assert _score_to_state(score) == WARNING

    def test_two_btc_alerts_is_risk(self):
        # 2 BTC alerts: 2×25 = 50 → RISK
        score = 50
        assert _score_to_state(score) == RISK

    def test_three_btc_alerts_is_event(self):
        # 3 BTC alerts: 3×25 = 75 → EVENT
        score = 75
        assert _score_to_state(score) == EVENT

    def test_btc_10pct_move_alone_is_risk(self):
        # BTC 10% move: +35 → RISK
        score = 35
        assert _score_to_state(score) == RISK

    def test_btc_10pct_plus_eth_10pct_is_event(self):
        # BTC 10%: +35, ETH 10%: +25 → 60 → EVENT
        score = 35 + 25
        assert _score_to_state(score) == EVENT

    def test_small_btc_move_plus_one_alert_is_warning(self):
        # BTC 2.5%: +8, one other alert: +10 → 18 → WARNING
        score = 8 + 10
        assert _score_to_state(score) == WARNING


class TestStateHelpers:
    def test_all_states_have_colour(self):
        for state in [CALM, WARNING, RISK, EVENT]:
            colour = state_colour(state)
            assert colour.startswith("#"), f"{state} has no valid colour"

    def test_all_states_have_icon(self):
        for state in [CALM, WARNING, RISK, EVENT]:
            icon = state_icon(state)
            assert icon, f"{state} has no icon"

    def test_calm_is_green(self):
        assert "2ecc71" in state_colour(CALM) or "green" in state_colour(CALM).lower()

    def test_event_is_red(self):
        # Red-ish: should contain e7 or similar
        colour = state_colour(EVENT)
        assert colour.startswith("#e") or "e74c3c" in colour

    def test_unknown_state_returns_fallback(self):
        # Should not crash on unknown state
        c = state_colour("unknown")
        assert c is not None
        i = state_icon("unknown")
        assert i is not None
