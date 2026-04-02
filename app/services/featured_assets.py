"""
In-memory cache layer for featured assets.

Callers use get_featured_symbols(db) which returns the ordered list of
enabled featured asset symbols.  The result is cached for up to
CACHE_TTL_SECONDS to avoid hammering the DB on every request.

Any write operation (add/remove/toggle/reorder) calls invalidate_cache()
via the CRUD layer so the next read always returns fresh data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger

CACHE_TTL_SECONDS = 60


class FeaturedAssetsService:
    def __init__(self) -> None:
        self._cached_symbols: list[str] = []
        self._cache_at: Optional[datetime] = None

    def invalidate_cache(self) -> None:
        self._cache_at = None
        logger.debug("FeaturedAssetsService: cache invalidated")

    def _is_fresh(self) -> bool:
        if self._cache_at is None:
            return False
        age = (datetime.now(timezone.utc) - self._cache_at).total_seconds()
        return age < CACHE_TTL_SECONDS

    async def get_featured_symbols(self, db: AsyncSession) -> list[str]:
        """Return the ordered list of enabled featured asset symbols."""
        if self._is_fresh():
            return list(self._cached_symbols)

        from app.crud import featured_assets as fa_crud
        symbols = await fa_crud.get_enabled_symbols(db)
        self._cached_symbols = symbols
        self._cache_at = datetime.now(timezone.utc)
        return list(symbols)

    async def get_featured_rows(self, db: AsyncSession):
        """Return full FeaturedAsset rows (not cached — admin use only)."""
        from app.crud import featured_assets as fa_crud
        return await fa_crud.get_all(db)

    async def ensure_seeded(self, db: AsyncSession) -> None:
        """Seed default featured assets if the table is empty."""
        from app.crud import featured_assets as fa_crud
        n = await fa_crud.seed_defaults(db)
        if n:
            logger.info(f"FeaturedAssetsService: seeded {n} default featured assets")
            self.invalidate_cache()


featured_assets_service = FeaturedAssetsService()
