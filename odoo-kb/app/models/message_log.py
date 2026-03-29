"""SQLAlchemy model for WhatsApp/SMS message audit log."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from app.models.document import Base


class MessageLog(Base):
    """Logs every inbound and outbound WhatsApp/SMS message."""

    __tablename__ = "kb_message_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    message_uuid = Column(String, nullable=True, index=True)  # Vonage message UUID
    channel = Column(String, nullable=False)  # "whatsapp" | "sms"
    direction = Column(String, nullable=False)  # "inbound" | "outbound"
    sender = Column(String, nullable=False)
    recipient = Column(String, nullable=False)
    text = Column(Text, nullable=False, default="")
    status = Column(String, nullable=False, default="received")
    # "received", "sent", "delivered", "read", "failed"

    related_call_id = Column(String, nullable=True)  # FK to CallRecord.id
    crm_lead_id = Column(Integer, nullable=True)
    conversation_id = Column(String, nullable=True)  # For context tracking

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_message_log_channel", "channel"),
        Index("ix_message_log_sender", "sender"),
        Index("ix_message_log_created_at", "created_at"),
    )
