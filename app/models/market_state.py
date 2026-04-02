"""
Market state model — persists the current market condition classification.

A single row (id=1) is maintained and updated each evaluation cycle.
States in ascending severity: CALM → WARNING → RISK → EVENT.
"""
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MarketState(Base):
    __tablename__ = "market_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    current_state: Mapped[str] = mapped_column(String(20), nullable=False, default="calm")
    previous_state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    # When the state last changed (vs checked_at which updates every cycle)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # JSON array of human-readable explanation strings, e.g. ["3 alerts in 60 min", "BTC moved 6.2%"]
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
