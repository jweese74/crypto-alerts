import uuid
from datetime import datetime, time, timezone
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class AlertCondition(str, PyEnum):
    ABOVE = "above"
    BELOW = "below"


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Human-readable pair e.g. "BTC/USD"
    trading_pair: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    condition: Mapped[AlertCondition] = mapped_column(Enum(AlertCondition, name="alertcondition"), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)

    # Rule behaviour
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    send_once: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    label: Mapped[str | None] = mapped_column(String(100), nullable=True)          # human name for the rule
    custom_message: Mapped[str | None] = mapped_column(Text, nullable=True)         # appended to alert email body

    # State tracking
    last_state: Mapped[str | None] = mapped_column(String(10), nullable=True)   # "above" | "below"
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Time-based filtering ───────────────────────────────────────────────────
    # When time_filter_enabled=False (default), rule fires at any time.
    # active_hours_start/end are stored as plain time (no timezone); the
    # active_timezone field controls how "now" is converted before comparison.
    time_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active_hours_start: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    active_hours_end: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    active_timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    # critical_override=True → rule fires even when outside the time window
    critical_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="alert_rules")  # noqa: F821
    history: Mapped[list["AlertHistory"]] = relationship(  # noqa: F821
        "AlertHistory", back_populates="rule", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<AlertRule id={self.id} pair={self.trading_pair} condition={self.condition} threshold={self.threshold}>"
