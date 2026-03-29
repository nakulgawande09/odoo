"""SQLAlchemy model for call records and conversation audit trail."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.models.document import Base


class CallRecord(Base):
    """Stores metadata and transcript for each voice call."""

    __tablename__ = "kb_call_records"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    call_uuid = Column(String, nullable=False, index=True)
    caller_number = Column(String, nullable=True)
    called_number = Column(String, nullable=True)
    agent_id = Column(Integer, nullable=True)
    provider = Column(String, nullable=False, default="vonage")

    # Lifecycle
    status = Column(String, nullable=False, default="started")
    # "started", "ringing", "answered", "completed", "failed", "rejected"
    started_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    answered_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Integer, nullable=True)

    # Recording
    recording_url = Column(String, nullable=True)
    recording_path = Column(String, nullable=True)  # Local file path
    recording_size_bytes = Column(Integer, nullable=True)

    # Transcript: [{role: "caller"|"bot", text: "...", timestamp: ..., confidence: ...}]
    transcript = Column(JSONB, default=list)

    # AI analysis
    conversation_summary = Column(Text, nullable=True)
    crm_analysis = Column(JSONB, nullable=True)  # Full CRMAnalysis as JSON
    crm_status = Column(String, default="none")  # "none" | "pending" | "processed" | "failed"
    crm_lead_id = Column(Integer, nullable=True)

    # Metrics
    total_queries = Column(Integer, default=0)
    avg_confidence = Column(Float, nullable=True)

    # Extra data
    metadata_ = Column("metadata", JSONB, default=dict)

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_call_records_status", "status"),
        Index("ix_call_records_started_at", "started_at"),
        Index("ix_call_records_caller", "caller_number"),
        Index("ix_call_records_crm_status", "crm_status"),
    )
