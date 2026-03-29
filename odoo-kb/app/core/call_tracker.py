"""Call transcript tracker — assembles per-call transcripts and persists them.

Each call is tracked by its Vonage call_uuid. As speech events arrive,
turns are appended. On call completion, the full transcript is saved to
the CallRecord in the database.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.call_record import CallRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscriptTurn:
    """A single turn in a call transcript."""

    role: str  # "caller" or "bot"
    text: str
    timestamp: float  # monotonic
    confidence: float = 0.0


@dataclass
class ActiveCall:
    """In-memory state for an active call."""

    call_uuid: str
    record_id: str
    agent_id: int | None = None
    caller_number: str = ""
    turns: list[TranscriptTurn] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    total_queries: int = 0
    confidence_sum: float = 0.0

    @property
    def avg_confidence(self) -> float:
        if self.total_queries == 0:
            return 0.0
        return self.confidence_sum / self.total_queries

    def add_caller_turn(self, text: str, confidence: float = 0.0) -> None:
        self.turns = [
            *self.turns,
            TranscriptTurn(
                role="caller",
                text=text,
                timestamp=time.monotonic() - self.started_at,
                confidence=confidence,
            ),
        ]
        self.total_queries += 1
        self.confidence_sum += confidence

    def add_bot_turn(self, text: str, confidence: float = 0.0) -> None:
        self.turns = [
            *self.turns,
            TranscriptTurn(
                role="bot",
                text=text,
                timestamp=time.monotonic() - self.started_at,
                confidence=confidence,
            ),
        ]

    def serialize_transcript(self) -> list[dict]:
        return [
            {
                "role": t.role,
                "text": t.text,
                "timestamp_seconds": round(t.timestamp, 2),
                "confidence": round(t.confidence, 4),
            }
            for t in self.turns
        ]


class CallTracker:
    """Tracks active calls in-memory and persists to database."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._active: dict[str, ActiveCall] = {}

    async def start_call(
        self,
        call_uuid: str,
        agent_id: int | None = None,
        caller_number: str = "",
        called_number: str = "",
        provider: str = "vonage",
    ) -> ActiveCall:
        """Register a new call and create a DB record."""
        record_id = str(uuid.uuid4())
        active = ActiveCall(
            call_uuid=call_uuid,
            record_id=record_id,
            agent_id=agent_id,
            caller_number=caller_number,
        )
        self._active[call_uuid] = active

        async with self._session_factory() as session:
            record = CallRecord(
                id=record_id,
                call_uuid=call_uuid,
                caller_number=caller_number,
                called_number=called_number,
                agent_id=agent_id,
                provider=provider,
                status="started",
            )
            session.add(record)
            await session.commit()

        logger.info("Call started: uuid=%s, caller=%s", call_uuid, caller_number)
        return active

    def get_active(self, call_uuid: str) -> ActiveCall | None:
        """Get an active call by UUID."""
        return self._active.get(call_uuid)

    async def get_or_start_call(
        self,
        call_uuid: str,
        agent_id: int | None = None,
        caller_number: str = "",
        called_number: str = "",
        provider: str = "vonage",
    ) -> ActiveCall:
        """Get existing active call or start a new one."""
        active = self._active.get(call_uuid)
        if active is not None:
            return active
        return await self.start_call(
            call_uuid, agent_id, caller_number, called_number, provider,
        )

    async def record_turn(
        self,
        call_uuid: str,
        role: str,
        text: str,
        confidence: float = 0.0,
    ) -> None:
        """Add a transcript turn to an active call."""
        active = self._active.get(call_uuid)
        if active is None:
            logger.warning("Turn for unknown call: %s", call_uuid)
            return

        if role == "caller":
            active.add_caller_turn(text, confidence)
        else:
            active.add_bot_turn(text, confidence)

    async def update_status(
        self, call_uuid: str, status: str, **extra: Any,
    ) -> None:
        """Update the call status in the database."""
        active = self._active.get(call_uuid)
        record_id = active.record_id if active else None

        if record_id is None:
            # Try to find by call_uuid in DB
            async with self._session_factory() as session:
                result = await session.execute(
                    select(CallRecord.id).where(CallRecord.call_uuid == call_uuid)
                )
                row = result.scalar_one_or_none()
                if row is None:
                    logger.warning("Status update for unknown call: %s", call_uuid)
                    return
                record_id = row

        values: dict[str, Any] = {"status": status, **extra}

        if status == "answered":
            values["answered_at"] = datetime.now(timezone.utc)
        elif status in ("completed", "failed", "rejected"):
            values["ended_at"] = datetime.now(timezone.utc)

        async with self._session_factory() as session:
            await session.execute(
                update(CallRecord).where(CallRecord.id == record_id).values(**values)
            )
            await session.commit()

    async def end_call(self, call_uuid: str) -> CallRecord | None:
        """Finalize a call: save transcript, calculate metrics, clean up."""
        active = self._active.pop(call_uuid, None)
        if active is None:
            logger.warning("End call for unknown active call: %s", call_uuid)
            return None

        now = datetime.now(timezone.utc)
        elapsed = time.monotonic() - active.started_at

        async with self._session_factory() as session:
            await session.execute(
                update(CallRecord)
                .where(CallRecord.id == active.record_id)
                .values(
                    status="completed",
                    ended_at=now,
                    duration_seconds=int(elapsed),
                    transcript=active.serialize_transcript(),
                    total_queries=active.total_queries,
                    avg_confidence=round(active.avg_confidence, 4) if active.total_queries else None,
                )
            )
            await session.commit()

            result = await session.execute(
                select(CallRecord).where(CallRecord.id == active.record_id)
            )
            record = result.scalar_one_or_none()

        logger.info(
            "Call ended: uuid=%s, duration=%ds, queries=%d, avg_conf=%.3f",
            call_uuid,
            int(elapsed),
            active.total_queries,
            active.avg_confidence,
        )
        return record

    async def save_recording(
        self, call_uuid: str, recording_url: str, recording_path: str = "",
        size_bytes: int = 0,
    ) -> None:
        """Attach recording metadata to a call record."""
        active = self._active.get(call_uuid)
        record_id = active.record_id if active else None

        if record_id is None:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(CallRecord.id).where(CallRecord.call_uuid == call_uuid)
                )
                record_id = result.scalar_one_or_none()

        if record_id is None:
            logger.warning("Recording for unknown call: %s", call_uuid)
            return

        async with self._session_factory() as session:
            await session.execute(
                update(CallRecord)
                .where(CallRecord.id == record_id)
                .values(
                    recording_url=recording_url,
                    recording_path=recording_path,
                    recording_size_bytes=size_bytes,
                )
            )
            await session.commit()

        logger.info("Recording saved: uuid=%s, url=%s", call_uuid, recording_url[:80])
