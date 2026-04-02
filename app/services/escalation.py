"""
Alert escalation engine — classifies each triggered alert as NORMAL,
ELEVATED, or CRITICAL by looking at recent activity patterns.

Scoring inputs
--------------
1. Same-rule recurrence in last 24 h
   - 0 prior triggers : +0
   - 1 prior trigger  : +15  → ELEVATED
   - 2+ prior         : +30  → CRITICAL

2. Multi-asset activity in last 30 min (distinct pairs firing)
   - 2 distinct pairs : +10
   - 3+ distinct pairs: +20

3. High-importance pair bonus
   - BTC/USD or ETH/USD rule: +10

Classification thresholds
--------------------------
   score < 15  → normal
   score 15-29 → elevated
   score ≥ 30  → critical
"""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.models.alert_history import AlertHistory
from app.models.alert_rule import AlertRule

NORMAL = "normal"
ELEVATED = "elevated"
CRITICAL = "critical"

_LEVELS = [NORMAL, ELEVATED, CRITICAL]

_HIGH_IMPORTANCE_PAIRS = frozenset({"BTC/USD", "ETH/USD"})

# Lookback windows
_RULE_RECURRENCE_HOURS = 24
_MULTI_ASSET_MINUTES = 30


def _score_to_level(score: int) -> str:
    if score >= 30:
        return CRITICAL
    if score >= 15:
        return ELEVATED
    return NORMAL


class EscalationEngine:
    """
    Determines the severity of a newly triggered alert.
    Called from AlertEngine._fire() before the email is sent.
    """

    async def compute_severity(
        self, db: AsyncSession, rule: AlertRule
    ) -> str:
        """
        Compute and return the severity string for a rule that is about
        to fire.  Queries recent AlertHistory (not the current trigger,
        which hasn't been persisted yet).
        """
        score = 0
        now = datetime.now(timezone.utc)

        # ── 1. Same-rule recurrence ───────────────────────────────────────
        since_recurrence = now - timedelta(hours=_RULE_RECURRENCE_HOURS)
        recurrence_result = await db.execute(
            select(func.count(AlertHistory.id))
            .where(AlertHistory.rule_id == rule.id)
            .where(AlertHistory.triggered_at >= since_recurrence)
        )
        prior_triggers = recurrence_result.scalar_one() or 0

        if prior_triggers >= 2:
            score += 30
        elif prior_triggers == 1:
            score += 15

        # ── 2. Multi-asset activity ───────────────────────────────────────
        since_multi = now - timedelta(minutes=_MULTI_ASSET_MINUTES)
        multi_result = await db.execute(
            select(func.count(AlertHistory.trading_pair.distinct()))
            .where(AlertHistory.triggered_at >= since_multi)
        )
        distinct_pairs = multi_result.scalar_one() or 0

        if distinct_pairs >= 3:
            score += 20
        elif distinct_pairs >= 2:
            score += 10

        # ── 3. High-importance pair bonus ─────────────────────────────────
        if rule.trading_pair in _HIGH_IMPORTANCE_PAIRS:
            score += 10

        level = _score_to_level(score)

        if level != NORMAL:
            logger.info(
                f"Escalation: rule {rule.id} ({rule.trading_pair}) → "
                f"{level.upper()} (score={score}, prior_triggers={prior_triggers}, "
                f"distinct_pairs_30m={distinct_pairs})"
            )

        return level


escalation_engine = EscalationEngine()
