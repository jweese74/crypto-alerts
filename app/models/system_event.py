import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    # Severity: info | warning | critical
    severity: Mapped[str] = mapped_column(String(20), default="info", nullable=False)
    # Event type string — e.g. "alert_triggered", "market_state_changed"
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # Asset symbol (BTC/USD) — nullable for system-wide events
    asset_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    # User ID (UUID as string) — nullable for system-wide events
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # Human-readable description — the main thing users see
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Extra structured data as JSON string (optional, low-cardinality)
    extra: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_system_events_asset_time", "asset_symbol", "occurred_at"),
        Index("ix_system_events_user_time", "user_id", "occurred_at"),
    )

    def __repr__(self) -> str:
        return f"<SystemEvent {self.event_type} @ {self.occurred_at}>"
