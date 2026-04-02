"""
Background scheduler — drives periodic price polling and alert evaluation.

Runs as a long-lived asyncio Task started during app lifespan.
Per-tick errors are caught and logged so a transient failure (e.g.
Kraken returning 503) never kills the loop.
"""
import asyncio
from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import logger
from app.services.alert_engine import alert_engine

settings = get_settings()


class Scheduler:
    """
    Polls Kraken and runs alert evaluation on a configurable interval.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._cycle_count: int = 0
        self._last_tick_at: datetime | None = None

    async def start(self) -> None:
        interval = settings.KRAKEN_POLL_INTERVAL_SECONDS
        logger.info(f"Scheduler starting — poll interval: {interval}s")
        self._task = asyncio.create_task(self._loop(), name="alert-scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(f"Scheduler stopped after {self._cycle_count} cycle(s)")

    async def _loop(self) -> None:
        interval = settings.KRAKEN_POLL_INTERVAL_SECONDS
        while True:
            await asyncio.sleep(interval)
            await self._tick()

    async def _tick(self) -> None:
        self._cycle_count += 1
        start = datetime.now(timezone.utc)
        logger.info(f"Scheduler tick #{self._cycle_count} starting")

        try:
            async with AsyncSessionLocal() as db:
                await alert_engine.run_evaluation_cycle(db)

                # Market state evaluation (runs after every alert cycle)
                from app.services.market_state import market_state_engine
                from app.services.market_data import market_data_service
                await market_state_engine.evaluate(db, market_data_service.last_prices)

                # Daily retention cleanup
                if self._cycle_count % (86400 // settings.KRAKEN_POLL_INTERVAL_SECONDS) == 0:
                    from app.services.retention import run_retention_cycle
                    await run_retention_cycle(db)
        except Exception as exc:
            logger.error(f"Scheduler tick #{self._cycle_count} failed: {exc}", exc_info=True)
        else:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            logger.info(f"Scheduler tick #{self._cycle_count} completed in {elapsed:.2f}s")
            self._last_tick_at = datetime.now(timezone.utc)

    @property
    def last_tick_at(self) -> datetime | None:
        return self._last_tick_at

    @property
    def cycle_count(self) -> int:
        return self._cycle_count


scheduler = Scheduler()
