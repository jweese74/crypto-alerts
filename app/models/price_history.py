import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssetPriceHistory(Base):
    __tablename__ = "asset_price_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    asset_symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(50), default="kraken", nullable=False)

    __table_args__ = (
        Index("ix_price_history_symbol_time", "asset_symbol", "captured_at"),
    )

    def __repr__(self) -> str:
        return f"<AssetPriceHistory {self.asset_symbol} ${self.price_usd} @ {self.captured_at}>"
