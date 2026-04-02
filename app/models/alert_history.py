import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class AlertHistory(Base):
    __tablename__ = "alert_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("alert_rules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trading_pair: Mapped[str] = mapped_column(String(20), nullable=False)
    # Price at the moment the alert fired
    triggered_price: Mapped[float] = mapped_column(Float, nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Escalation severity: normal | elevated | critical
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    # Delivery channel and status
    delivery_channel: Mapped[str] = mapped_column(String(50), default="email", nullable=False)
    delivered: Mapped[bool] = mapped_column(default=False, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="alert_history")  # noqa: F821
    rule: Mapped["AlertRule"] = relationship("AlertRule", back_populates="history")  # noqa: F821

    def __repr__(self) -> str:
        return f"<AlertHistory id={self.id} pair={self.trading_pair} price={self.triggered_price}>"
