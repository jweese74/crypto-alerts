"""
Simulation Engine
=================
Replays stored price history against alert rules to show which
alerts *would have* triggered — without sending any notifications
or modifying any database state.

Design
------
- Pure functions only; no DB access inside the engine itself.
- The route layer is responsible for fetching price history and
  alert rules from the DB, then passing them in.
- Per-rule state (last_state, last_triggered_at, fired_count) is
  tracked independently inside the simulation so real rule state
  is never touched.
- Reuses _current_state + crossing logic identical to alert_engine.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.alert_rule import AlertCondition


# ── Data transfer objects ─────────────────────────────────────────────────────

@dataclass
class SimRule:
    """
    Immutable snapshot of an AlertRule's relevant fields for simulation.
    Created from a real AlertRule object by the route layer.
    """
    id: str
    label: str
    trading_pair: str
    condition: AlertCondition
    threshold: float
    cooldown_minutes: int
    send_once: bool
    # Time-filter fields (respected during simulation)
    time_filter_enabled: bool = False
    active_hours_start: Optional[object] = None   # datetime.time or None
    active_hours_end: Optional[object] = None
    active_timezone: str = "UTC"
    critical_override: bool = False


@dataclass
class SimTrigger:
    """A single simulated alert trigger event."""
    rule_id: str
    rule_label: str
    trading_pair: str
    condition: str          # "above" | "below"
    threshold: float
    triggered_price: float
    triggered_at: datetime
    previous_state: Optional[str]   # state before crossing
    trigger_index: int              # position in the price series


@dataclass
class SimRuleResult:
    """Per-rule summary of the simulation."""
    rule: SimRule
    triggers: list[SimTrigger] = field(default_factory=list)
    skipped_cooldown: int = 0
    skipped_time_filter: int = 0
    deactivated_send_once: bool = False

    @property
    def trigger_count(self) -> int:
        return len(self.triggers)


@dataclass
class SimResult:
    """Full simulation output."""
    asset_symbol: str
    range_key: str
    start_time: datetime
    end_time: datetime
    price_point_count: int
    rule_results: list[SimRuleResult]

    @property
    def total_triggers(self) -> int:
        return sum(r.trigger_count for r in self.rule_results)

    @property
    def total_skipped(self) -> int:
        return sum(r.skipped_cooldown + r.skipped_time_filter for r in self.rule_results)


# ── Internal per-rule mutable state ──────────────────────────────────────────

@dataclass
class _RuleState:
    last_state: Optional[str] = None
    last_triggered_at: Optional[datetime] = None
    active: bool = True
    fired_count: int = 0


# ── Core helpers (mirrors alert_engine.py exactly) ────────────────────────────

def _current_state(price: float, threshold: float) -> str:
    return "above" if price >= threshold else "below"


def _crossing(
    rule: SimRule,
    state: _RuleState,
    current: str,
) -> bool:
    """Return True if the price just crossed the threshold in the required direction."""
    if state.last_state is None:
        return (
            (rule.condition == AlertCondition.ABOVE and current == "above")
            or (rule.condition == AlertCondition.BELOW and current == "below")
        )
    return (
        rule.condition == AlertCondition.ABOVE
        and state.last_state == "below"
        and current == "above"
    ) or (
        rule.condition == AlertCondition.BELOW
        and state.last_state == "above"
        and current == "below"
    )


def _in_active_window(rule: SimRule, ts: datetime) -> bool:
    """
    Return True if ts falls within the rule's active time window.
    Mirrors alert_engine._in_active_window exactly.
    """
    if not rule.time_filter_enabled:
        return True
    if rule.critical_override:
        return True
    if rule.active_hours_start is None or rule.active_hours_end is None:
        return True

    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        tz = ZoneInfo(rule.active_timezone or "UTC")
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    local_time = ts.astimezone(tz).time()
    start = rule.active_hours_start
    end   = rule.active_hours_end

    if start <= end:
        return start <= local_time <= end
    # Overnight window (e.g. 22:00 → 06:00)
    return local_time >= start or local_time <= end


# ── Main simulation function ──────────────────────────────────────────────────

def run_simulation(
    *,
    asset_symbol: str,
    price_points: list[tuple[datetime, float]],
    rules: list[SimRule],
    range_key: str = "24h",
) -> SimResult:
    """
    Replay price_points against rules and return a SimResult.

    price_points — list of (datetime, price_usd) sorted ascending by time.
    rules        — list of SimRule snapshots to evaluate.
    range_key    — label only, used in result metadata.

    No database access, no notifications, no logging side-effects.
    """
    if not price_points:
        start_time = end_time = datetime.now(timezone.utc)
    else:
        start_time = price_points[0][0]
        end_time   = price_points[-1][0]

    # Initialise per-rule mutable state (isolated from real DB state)
    states: dict[str, _RuleState] = {r.id: _RuleState() for r in rules}
    rule_results: dict[str, SimRuleResult] = {r.id: SimRuleResult(rule=r) for r in rules}

    for idx, (ts, price) in enumerate(price_points):
        for rule in rules:
            state  = states[rule.id]
            result = rule_results[rule.id]

            if not state.active:
                continue

            current = _current_state(price, rule.threshold)

            # Time-filter check
            if not _in_active_window(rule, ts):
                state.last_state = current
                result.skipped_time_filter += 1
                continue

            if not _crossing(rule, state, current):
                state.last_state = current
                continue

            # Cooldown check
            if state.last_triggered_at is not None:
                elapsed = (ts - state.last_triggered_at).total_seconds() / 60
                if elapsed < rule.cooldown_minutes:
                    state.last_state = current
                    result.skipped_cooldown += 1
                    continue

            # ── Would trigger ────────────────────────────────────────────
            trigger = SimTrigger(
                rule_id=rule.id,
                rule_label=rule.label or rule.trading_pair,
                trading_pair=rule.trading_pair,
                condition=rule.condition.value,
                threshold=rule.threshold,
                triggered_price=price,
                triggered_at=ts,
                previous_state=state.last_state,
                trigger_index=idx,
            )
            result.triggers.append(trigger)
            state.fired_count += 1
            state.last_triggered_at = ts
            state.last_state = current

            if rule.send_once:
                state.active = False
                result.deactivated_send_once = True

    return SimResult(
        asset_symbol=asset_symbol,
        range_key=range_key,
        start_time=start_time,
        end_time=end_time,
        price_point_count=len(price_points),
        rule_results=list(rule_results.values()),
    )


def sim_rule_from_alert_rule(rule) -> SimRule:
    """
    Convert a live AlertRule ORM object to an immutable SimRule snapshot.
    Called by the route layer; keeps the engine free of SQLAlchemy imports.
    """
    return SimRule(
        id=str(rule.id),
        label=rule.label or f"{rule.trading_pair} {rule.condition.value} ${rule.threshold:,.2f}",
        trading_pair=rule.trading_pair,
        condition=rule.condition,
        threshold=rule.threshold,
        cooldown_minutes=rule.cooldown_minutes,
        send_once=rule.send_once,
        time_filter_enabled=rule.time_filter_enabled,
        active_hours_start=rule.active_hours_start,
        active_hours_end=rule.active_hours_end,
        active_timezone=rule.active_timezone,
        critical_override=rule.critical_override,
    )
