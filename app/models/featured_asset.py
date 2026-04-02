"""
FeaturedAsset model — admin-managed list of assets shown prominently
on the dashboard price strip and chart navigation.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FeaturedAsset(Base):
    __tablename__ = "featured_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Display symbol, e.g. "BTC/USD" — unique, case-sensitive
    asset_symbol: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)

    # Kraken altname used for ticker queries, e.g. "XBTUSD"
    kraken_pair: Mapped[str] = mapped_column(String(40), nullable=False)

    # Optional friendly label shown in UI, e.g. "Bitcoin"
    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Lower values appear first in the dashboard strip
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Disabled entries are hidden from user-facing displays
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Admin notes (not shown to regular users)
    notes: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<FeaturedAsset {self.asset_symbol} order={self.sort_order} enabled={self.enabled}>"
