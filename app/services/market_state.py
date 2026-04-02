"""
Market State Engine — classifies overall market conditions each poll cycle.

Scoring inputs
--------------
1. Alert triggers in the last 60 minutes (global, all users):
   - BTC/USD alert:   25 pts each
   - ETH/USD alert:   18 pts each
   - Any other pair:  10 pts each

2. BTC/USD price change over the last 60 minutes (from price history):
   - > 10 %:  35 pts
   - >  5 %:  20 pts
   - >  2 %:   8 pts

3. ETH/USD price change over the last 60 minutes:
   - > 10 %:  25 pts
   - >  5 %:  15 pts
   - >  2 %:   5 pts

State thresholds
----------------
  CALM    : 0 – 9
  WARNING : 10 – 29
  RISK    : 30 – 59
  EVENT   : 60 +
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.alert_history import AlertHistory
from app.models.market_state import MarketState

# ── State constants ────────────────────────────────────────────────────────────

CALM = "calm"
WARNING = "warning"
RISK = "risk"
EVENT = "event"

_STATES_ORDERED = [CALM, WARNING, RISK, EVENT]

_STATE_COLOURS = {
    CALM: "#2ecc71",
    WARNING: "#f39c12",
    RISK: "#e67e22",
    EVENT: "#e74c3c",
}

_STATE_ICONS = {
    CALM: "🟢",
    WARNING: "🟡",
    RISK: "🟠",
    EVENT: "🔴",
}

# Alert weight per trading pair
_ALERT_WEIGHTS = {
    "BTC/USD": 25,
    "ETH/USD": 18,
}
_ALERT_WEIGHT_DEFAULT = 10

# Price-move score tables: (threshold_pct, score)
_BTC_MOVE_SCORES = [(10, 35), (5, 20), (2, 8)]
_ETH_MOVE_SCORES = [(10, 25), (5, 15), (2, 5)]

# State score thresholds (lower bound, inclusive)
_SCORE_THRESHOLDS = [(60, EVENT), (30, RISK), (10, WARNING), (0, CALM)]

# Look-back window for alert counting
_ALERT_LOOKBACK_MINUTES = 60


def _score_to_state(score: int) -> str:
    for minimum, state in _SCORE_THRESHOLDS:
        if score >= minimum:
            return state
    return CALM


def _price_move_score(pct: float, table: list[tuple[int, int]]) -> int:
    for threshold, pts in table:
        if pct >= threshold:
            return pts
    return 0


class MarketStateEngine:
    """
    Evaluates market conditions and persists the result as a single
    row in ``market_state``.
    """

    async def evaluate(self, db: AsyncSession, prices: dict[str, float]) -> str:
        """
        Run one evaluation cycle. Returns the new state string.
        Called by the scheduler after each alert engine cycle.
        """
        now = datetime.now(timezone.utc)
        score = 0
        reasons: list[str] = []

        # ── 1. Recent alert triggers ──────────────────────────────────────────
        since = now - timedelta(minutes=_ALERT_LOOKBACK_MINUTES)
        alerts_result = await db.execute(
            select(AlertHistory.trading_pair)
            .where(AlertHistory.triggered_at >= since)
        )
        recent_pairs = [row[0] for row in alerts_result.fetchall()]

        if recent_pairs:
            alert_score = sum(
                _ALERT_WEIGHTS.get(pair, _ALERT_WEIGHT_DEFAULT)
                for pair in recent_pairs
            )
            score += alert_score
            pair_summary = {}
            for pair in recent_pairs:
                pair_summary[pair] = pair_summary.get(pair, 0) + 1
            summary_str = ", ".join(
                f"{cnt}× {pair}" for pair, cnt in sorted(pair_summary.items())
            )
            reasons.append(
                f"{len(recent_pairs)} alert(s) fired in last {_ALERT_LOOKBACK_MINUTES} min "
                f"({summary_str}) +{alert_score}pts"
            )

        # ── 2. Price movement checks ──────────────────────────────────────────
        for symbol, move_table, label in [
            ("BTC/USD", _BTC_MOVE_SCORES, "BTC"),
            ("ETH/USD", _ETH_MOVE_SCORES, "ETH"),
        ]:
            pts, pct = await self._price_move_pts(db, symbol, prices, move_table)
            if pts > 0:
                score += pts
                reasons.append(f"{label} moved {pct:.1f}% in last hour +{pts}pts")

        # ── 3. Classify ───────────────────────────────────────────────────────
        new_state = _score_to_state(score)

        # ── 4. Persist ────────────────────────────────────────────────────────
        from app.crud import market_state as ms_crud
        existing = await ms_crud.get_current(db)
        prev_state = existing.current_state if existing else None
        state_changed = prev_state != new_state
        changed_at = existing.changed_at if (existing and not state_changed) else now

        if state_changed:
            icon_old = _STATE_ICONS.get(prev_state, "?") if prev_state else "?"
            icon_new = _STATE_ICONS.get(new_state, "?")
            logger.warning(
                f"Market state changed: {icon_old} {(prev_state or 'none').upper()} → "
                f"{icon_new} {new_state.upper()} (score={score})"
            )
            # Human-readable event log
            from app.services.event_log import event_log
            await event_log.market_state_changed(
                db,
                previous_state=prev_state or "none",
                new_state=new_state,
                score=score,
                reasons=reasons if reasons else [],
            )

        await ms_crud.upsert(
            db,
            current_state=new_state,
            previous_state=prev_state,
            score=score,
            changed_at=changed_at,
            checked_at=now,
            reasons=reasons if reasons else ["No significant activity"],
        )

        if not state_changed:
            logger.debug(f"Market state: {new_state.upper()} (score={score})")

        return new_state

    async def _price_move_pts(
        self,
        db: AsyncSession,
        symbol: str,
        prices: dict[str, float],
        move_table: list[tuple[int, int]],
    ) -> tuple[int, float]:
        """Return (score_pts, pct_change) for a symbol's 1-hour price move."""
        from app.crud import price_history as ph_crud

        price_now = prices.get(symbol)
        if price_now is None:
            return 0, 0.0

        price_1h_ago = await ph_crud.get_price_minutes_ago(db, symbol, 60)
        if price_1h_ago is None or price_1h_ago <= 0:
            return 0, 0.0

        pct = abs(price_now - price_1h_ago) / price_1h_ago * 100
        pts = _price_move_score(pct, move_table)
        return pts, pct


# ── Module-level helpers ───────────────────────────────────────────────────────

def state_colour(state: str) -> str:
    return _STATE_COLOURS.get(state, "#888")


def state_icon(state: str) -> str:
    return _STATE_ICONS.get(state, "⚪")


market_state_engine = MarketStateEngine()
