"""
UserTickerAsset model — per-user list of assets shown in the ticker view.

Each user can curate their own set of assets (beyond admin-managed featured
markets) to appear in their personal ticker view.  Featured markets always
appear first; personal assets are appended, deduplicating any overlap.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserTickerAsset(Base):
    __tablename__ = "user_ticker_assets"
    __table_args__ = (
        UniqueConstraint("user_id", "asset_symbol", name="uq_user_ticker_asset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Trading pair symbol, e.g. "BTC/USD"
    asset_symbol: Mapped[str] = mapped_column(String(30), nullable=False)

    # Optional friendly label (overrides symbol display in ticker)
    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Lower = shown earlier in the personal section
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Disabled entries are hidden from the ticker view but preserved
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

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

    # relationship back to user (not strictly needed but useful)
    user: Mapped["User"] = relationship("User", lazy="noload")  # type: ignore[name-defined]

    def __repr__(self) -> str:
        return f"<UserTickerAsset user={self.user_id} {self.asset_symbol} enabled={self.enabled}>"
