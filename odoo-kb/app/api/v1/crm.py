"""CRM integration API — pull-based endpoints for Odoo to fetch pending leads.

Instead of pushing CRM data to Odoo via XML-RPC (which requires Odoo credentials),
the KB service stores analysis results locally and exposes them here. The Odoo
kb_crm addon polls these endpoints using the already-configured KB service URL
and API key.

Endpoints:
  GET  /v1/crm/pending           — List calls with pending CRM lead creation
  POST /v1/crm/processed/{id}    — Mark a call as processed (lead created in Odoo)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, update as sql_update

from app.api.auth import verify_api_key
from app.dependencies import get_session_factory
from app.models.call_record import CallRecord

logger = logging.getLogger(__name__)

router = APIRouter()


class ProcessedRequest(BaseModel):
    """Body for marking a call as CRM-processed."""

    lead_id: int


@router.get("/crm/pending")
async def list_pending(
    limit: int = Query(50, ge=1, le=200),
    _api_key: str | None = Depends(verify_api_key),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Return call records awaiting CRM lead creation.

    The Odoo kb_crm addon polls this endpoint via cron to pull
    analyzed calls and create crm.lead records locally.
    """
    async with session_factory() as session:
        result = await session.execute(
            select(CallRecord)
            .where(CallRecord.crm_status == "pending")
            .order_by(CallRecord.ended_at.asc())
            .limit(limit)
        )
        records = result.scalars().all()

    return {
        "count": len(records),
        "calls": [_serialize_pending(r) for r in records],
    }


@router.post("/crm/processed/{call_id}")
async def mark_processed(
    call_id: str,
    body: ProcessedRequest,
    _api_key: str | None = Depends(verify_api_key),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Mark a call record as CRM-processed after Odoo creates the lead.

    Args:
        call_id: The KB call record ID (UUID).
        body: Contains the Odoo crm.lead ID.
    """
    async with session_factory() as session:
        result = await session.execute(
            sql_update(CallRecord)
            .where(
                (CallRecord.id == call_id) | (CallRecord.call_uuid == call_id)
            )
            .where(CallRecord.crm_status == "pending")
            .values(crm_status="processed", crm_lead_id=body.lead_id)
        )
        await session.commit()

    if result.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="Call not found or not in pending status",
        )

    logger.info("Call %s marked as CRM-processed (lead_id=%d)", call_id, body.lead_id)
    return {"status": "ok", "call_id": call_id, "lead_id": body.lead_id}


def _serialize_pending(record: CallRecord) -> dict:
    """Serialize a pending call record for the CRM sync endpoint."""
    return {
        "id": record.id,
        "call_uuid": record.call_uuid,
        "caller_number": record.caller_number,
        "called_number": record.called_number,
        "agent_id": record.agent_id,
        "provider": record.provider,
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "ended_at": record.ended_at.isoformat() if record.ended_at else None,
        "duration_seconds": record.duration_seconds,
        "recording_url": record.recording_url,
        "transcript": record.transcript or [],
        "conversation_summary": record.conversation_summary,
        "crm_analysis": record.crm_analysis or {},
    }
