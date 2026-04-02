"""
Alert engine — evaluates active rules against live Kraken prices.

State machine per rule:
  last_state = None  →  first evaluation; trigger if condition already met
  last_state = "above"/"below"  →  trigger only on crossing in the right direction

Cooldown:
  After triggering, wait `rule.cooldown_minutes` before re-evaluating.

send_once:
  If True, disable the rule after it fires once.

SMTP delivery is NOT yet implemented; alerts are logged as
  "ALERT WOULD SEND".
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import logger
from app.crud import alert as alert_crud
from app.models.alert_rule import AlertCondition, AlertRule
from app.services.market_data import market_data_service

settings = get_settings()


def _current_state(price: float, threshold: float) -> str:
    return "above" if price >= threshold else "below"


def _should_trigger(rule: AlertRule, price: float) -> bool:
    """
    Return True if this rule should fire given the current price.
    Evaluates crossing, cooldown, and active status.
    """
    now = datetime.now(timezone.utc)
    current = _current_state(price, rule.threshold)

    # ── Crossing detection ────────────────────────────────────────────────
    if rule.last_state is None:
        # First evaluation: trigger if the condition is already satisfied
        crossing = (
            (rule.condition == AlertCondition.ABOVE and current == "above")
            or (rule.condition == AlertCondition.BELOW and current == "below")
        )
    else:
        crossing = (
            rule.condition == AlertCondition.ABOVE
            and rule.last_state == "below"
            and current == "above"
        ) or (
            rule.condition == AlertCondition.BELOW
            and rule.last_state == "above"
            and current == "below"
        )

    if not crossing:
        return False

    # ── Cooldown check ────────────────────────────────────────────────────
    if rule.last_triggered_at is not None:
        elapsed_minutes = (now - rule.last_triggered_at).total_seconds() / 60
        if elapsed_minutes < rule.cooldown_minutes:
            logger.debug(
                f"Rule {rule.id} ({rule.trading_pair} {rule.condition.value} "
                f"{rule.threshold}) skipped — cooldown "
                f"({elapsed_minutes:.1f}/{rule.cooldown_minutes} min)"
            )
            return False

    return True


def _in_active_window(rule: AlertRule, now_utc: datetime) -> bool:
    """
    Return True if now_utc falls within the rule's configured active time window.

    When time_filter_enabled is False (the default), always returns True.
    Handles overnight windows correctly (e.g. 22:00 → 06:00).
    DST is handled automatically via zoneinfo/IANA timezone names.
    """
    if not rule.time_filter_enabled:
        return True
    if rule.active_hours_start is None or rule.active_hours_end is None:
        return True  # incomplete config → treat as unrestricted

    try:
        tz = ZoneInfo(rule.active_timezone or "UTC")
    except ZoneInfoNotFoundError:
        logger.warning(
            f"Rule {rule.id}: unknown timezone '{rule.active_timezone}' — treating as UTC"
        )
        tz = ZoneInfo("UTC")

    now_local = now_utc.astimezone(tz)
    current = now_local.time().replace(tzinfo=None)

    start = rule.active_hours_start
    end   = rule.active_hours_end

    if start <= end:
        # Normal window: e.g. 09:00–17:00
        return start <= current <= end
    else:
        # Overnight window: e.g. 22:00–06:00 (wraps midnight)
        return current >= start or current <= end


class AlertEngine:
    """
    Evaluates all active alert rules against current Kraken prices
    and records triggered alerts.
    """

    def __init__(self) -> None:
        self._last_snapshot_at: datetime | None = None

    async def _maybe_store_snapshots(
        self, db: AsyncSession, prices: dict[str, float]
    ) -> None:
        """Store price snapshots at the configured interval."""
        now = datetime.now(timezone.utc)
        interval_secs = settings.PRICE_SNAPSHOT_INTERVAL_MINUTES * 60
        if (
            self._last_snapshot_at is not None
            and (now - self._last_snapshot_at).total_seconds() < interval_secs
        ):
            return
        from app.crud import price_history as ph_crud
        for symbol, price in prices.items():
            await ph_crud.store_snapshot(db, symbol=symbol, price=price, captured_at=now)
        self._last_snapshot_at = now
        logger.debug(f"Price snapshots stored for {len(prices)} pairs")

    async def run_evaluation_cycle(self, db: AsyncSession) -> None:
        # Determine which pairs to fetch prices for
        from app.services.featured_assets import featured_assets_service
        featured = await featured_assets_service.get_featured_symbols(db)
        rules = await alert_crud.get_active_rules(db)
        rule_pairs = list({r.trading_pair for r in rules})
        all_pairs = list(dict.fromkeys(featured + rule_pairs))

        try:
            prices = await market_data_service.fetch_prices(all_pairs)
        except Exception as exc:
            logger.error(f"Alert engine: price fetch failed — {exc}")
            return

        # Store price snapshots at configured interval
        await self._maybe_store_snapshots(db, prices)

        if not rules:
            logger.debug("Alert engine: no active rules — skipping rule evaluation")
            return

        triggered = 0
        skipped_no_price = 0

        for rule in rules:
            price = prices.get(rule.trading_pair)
            if price is None:
                logger.warning(
                    f"Rule {rule.id}: no price available for {rule.trading_pair} — skipping"
                )
                skipped_no_price += 1
                continue

            current_state = _current_state(price, rule.threshold)

            if _should_trigger(rule, price):
                # ── Time-window gate ──────────────────────────────────────
                now_utc = datetime.now(timezone.utc)
                if not _in_active_window(rule, now_utc):
                    if rule.critical_override:
                        logger.info(
                            f"Rule {rule.id} ({rule.trading_pair}): outside active window "
                            f"but critical_override=True — firing"
                        )
                    else:
                        tz_label = rule.active_timezone or "UTC"
                        logger.info(
                            f"Rule {rule.id} ({rule.trading_pair}): suppressed — "
                            f"outside active hours [{rule.active_hours_start}–"
                            f"{rule.active_hours_end} {tz_label}]"
                        )
                        # Human-readable event log
                        from app.services.event_log import event_log
                        await event_log.alert_suppressed_time_filter(
                            db,
                            trading_pair=rule.trading_pair,
                            threshold=rule.threshold,
                            condition=rule.condition.value,
                            window_start=str(rule.active_hours_start or ""),
                            window_end=str(rule.active_hours_end or ""),
                            timezone_name=tz_label,
                            user_id=str(rule.user_id),
                        )
                        # Track state crossing without alerting
                        await alert_crud.update_rule_state(db, rule, last_state=current_state)
                        continue

                await self._fire(db, rule, price, current_state)
                triggered += 1
            else:
                if rule.last_state != current_state:
                    await alert_crud.update_rule_state(db, rule, last_state=current_state)

        logger.info(
            f"Alert engine cycle complete — "
            f"{len(rules)} rules evaluated, "
            f"{triggered} triggered, "
            f"{skipped_no_price} skipped (no price)"
        )

    async def _fire(
        self,
        db: AsyncSession,
        rule: AlertRule,
        price: float,
        current_state: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        direction = "above" if rule.condition == AlertCondition.ABOVE else "below"
        message = (
            f"{rule.trading_pair} price ${price:,.2f} crossed "
            f"{direction} threshold ${rule.threshold:,.2f}"
        )

        # ── Compute escalation severity ───────────────────────────────────
        from app.services.escalation import escalation_engine
        severity = await escalation_engine.compute_severity(db, rule)

        logger.warning(
            f"[ALERT TRIGGERED] rule={rule.id} user={rule.user_id} "
            f"pair={rule.trading_pair} price=${price:,.2f} "
            f"threshold={rule.condition.value} ${rule.threshold:,.2f} "
            f"severity={severity.upper()}"
        )

        # ── Load the rule owner ───────────────────────────────────────────
        from app.crud import user as user_crud
        user = await user_crud.get_by_id(db, rule.user_id)

        # ── Dispatch to all configured channels ───────────────────────────
        from app.services.notification import notification_dispatcher
        channel_results = await notification_dispatcher.send_alert(
            db,
            to_address=user.email if (user and user.email) else "",
            username=user.username if user else "unknown",
            trading_pair=rule.trading_pair,
            condition=rule.condition.value,
            threshold=rule.threshold,
            triggered_price=price,
            message=message,
            timestamp=now,
            severity=severity,
            rule_label=getattr(rule, "label", None),
        )

        delivered = any(channel_results.values())
        delivery_channel = ",".join(ch for ch, ok in channel_results.items() if ok) or "none"

        if not user or not user.email:
            logger.warning(f"Rule {rule.id}: no user email found — email channel skipped")

        # ── Persist history record ────────────────────────────────────────
        record = await alert_crud.create_history_record(
            db, rule=rule, triggered_price=price, message=message, severity=severity
        )
        # Patch delivery status after creation
        record.delivered = delivered
        record.delivery_channel = delivery_channel[:50]   # VARCHAR(50) guard
        await db.commit()

        # ── Update rule state ─────────────────────────────────────────────
        deactivate = rule.send_once
        await alert_crud.update_rule_state(
            db,
            rule,
            last_state=current_state,
            last_triggered_at=now,
            is_active=False if deactivate else None,
        )

        if deactivate:
            logger.info(f"Rule {rule.id} disabled after send_once trigger")

        # ── Human-readable event log ──────────────────────────────────────
        from app.services.event_log import event_log
        await event_log.alert_triggered(
            db,
            trading_pair=rule.trading_pair,
            condition=rule.condition.value,
            threshold=rule.threshold,
            triggered_price=price,
            severity=severity,
            user_id=str(rule.user_id),
            rule_label=getattr(rule, "label", None),
        )


alert_engine = AlertEngine()
