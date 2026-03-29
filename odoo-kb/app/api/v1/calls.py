"""Call records API — retrieve call history, transcripts, and recordings."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select, func, desc

from app.api.auth import verify_api_key
from app.dependencies import get_session_factory
from app.models.call_record import CallRecord

router = APIRouter()


@router.get("/calls")
async def list_calls(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None),
    caller: str | None = Query(None),
    _api_key: str | None = Depends(verify_api_key),
    session_factory=Depends(get_session_factory),
) -> dict:
    """List call records with optional filters."""
    async with session_factory() as session:
        query = select(CallRecord).order_by(desc(CallRecord.started_at))

        if status:
            query = query.where(CallRecord.status == status)
        if caller:
            safe_caller = caller.replace("%", r"\%").replace("_", r"\_")
            query = query.where(CallRecord.caller_number.ilike(f"%{safe_caller}%"))

        # Count total
        count_query = select(func.count(CallRecord.id))
        if status:
            count_query = count_query.where(CallRecord.status == status)
        if caller:
            safe_caller = caller.replace("%", r"\%").replace("_", r"\_")
            count_query = count_query.where(CallRecord.caller_number.ilike(f"%{safe_caller}%"))
        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        # Fetch page
        query = query.offset(offset).limit(limit)
        result = await session.execute(query)
        records = result.scalars().all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "calls": [_serialize_call(r) for r in records],
    }


@router.get("/calls/{call_id}")
async def get_call(
    call_id: str,
    _api_key: str | None = Depends(verify_api_key),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Get a single call record with full transcript."""
    async with session_factory() as session:
        result = await session.execute(
            select(CallRecord).where(
                (CallRecord.id == call_id) | (CallRecord.call_uuid == call_id)
            )
        )
        record = result.scalar_one_or_none()

    if record is None:
        raise HTTPException(status_code=404, detail="Call not found")

    return _serialize_call(record, include_transcript=True)


@router.get("/calls/{call_id}/transcript")
async def get_transcript(
    call_id: str,
    _api_key: str | None = Depends(verify_api_key),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Get just the structured transcript for a call."""
    async with session_factory() as session:
        result = await session.execute(
            select(CallRecord.transcript, CallRecord.call_uuid, CallRecord.duration_seconds)
            .where(
                (CallRecord.id == call_id) | (CallRecord.call_uuid == call_id)
            )
        )
        row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Call not found")

    return {
        "call_uuid": row.call_uuid,
        "duration_seconds": row.duration_seconds,
        "turns": row.transcript or [],
    }


@router.get("/calls/{call_id}/recording")
async def get_recording(
    call_id: str,
    _api_key: str | None = Depends(verify_api_key),
    session_factory=Depends(get_session_factory),
) -> FileResponse:
    """Stream the recording audio file for a call."""
    async with session_factory() as session:
        result = await session.execute(
            select(CallRecord.recording_path, CallRecord.recording_url)
            .where(
                (CallRecord.id == call_id) | (CallRecord.call_uuid == call_id)
            )
        )
        row = result.one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Call not found")

    if not row.recording_path:
        if row.recording_url:
            # Recording exists but hasn't been downloaded locally yet
            raise HTTPException(
                status_code=202,
                detail={
                    "message": "Recording available at Vonage but not downloaded locally",
                    "recording_url": row.recording_url,
                },
            )
        raise HTTPException(status_code=404, detail="No recording available")

    return FileResponse(
        path=row.recording_path,
        media_type="audio/mpeg",
        filename=f"call_{call_id}.mp3",
    )


def _serialize_call(record: CallRecord, include_transcript: bool = False) -> dict:
    """Serialize a CallRecord to a JSON-friendly dict."""
    data = {
        "id": record.id,
        "call_uuid": record.call_uuid,
        "caller_number": record.caller_number,
        "called_number": record.called_number,
        "agent_id": record.agent_id,
        "provider": record.provider,
        "status": record.status,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "answered_at": record.answered_at.isoformat() if record.answered_at else None,
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
        "duration_seconds": record.duration_seconds,
        "has_recording": bool(record.recording_url or record.recording_path),
        "total_queries": record.total_queries,
        "avg_confidence": record.avg_confidence,
        "conversation_summary": record.conversation_summary,
        "crm_lead_id": record.crm_lead_id,
    }
    if include_transcript:
        data["transcript"] = record.transcript or []
        data["recording_url"] = record.recording_url
    return data
