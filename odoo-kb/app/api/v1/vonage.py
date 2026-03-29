"""Vonage Voice API integration with proper Answer/Event URL flow.

Vonage call flow:
  1. Inbound call → Vonage hits Answer URL → returns greeting NCCO + input
  2. Caller speaks → Vonage transcribes → hits Event URL with speech results
  3. Event URL searches KB → returns answer NCCO + next input action
  4. Loop continues until caller hangs up or escalation triggers
  5. Recording callback delivers the call audio URL
  6. Status callback tracks call lifecycle (started → answered → completed)

Endpoints:
  POST /v1/voip/vonage/answer    – Answer URL (initial greeting + record)
  POST /v1/voip/vonage/event     – Event URL (speech results → KB search)
  POST /v1/voip/vonage/recording – Recording callback
  POST /v1/voip/vonage/status    – Call status lifecycle events
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Query, Request

from app.dependencies import (
    get_call_tracker,
    get_crm_analyzer,
    get_orchestrator,
    get_query_logger,
    get_settings,
    get_voice_agent_store,
)
from app.schemas.search import SearchRequest

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_public_url() -> str:
    """Resolve the public URL for webhook callbacks."""
    settings = get_settings()
    if settings.public_url:
        return settings.public_url.rstrip("/")
    return f"http://{settings.host}:{settings.port}"


def _build_input_action(agent_id: int | None, language: str) -> dict:
    """Build a Vonage input NCCO action with eventUrl pointing back to us."""
    public_url = _get_public_url()
    agent_param = f"?agent_id={agent_id}" if agent_id else ""
    return {
        "action": "input",
        "type": ["speech"],
        "eventUrl": [f"{public_url}/v1/voip/vonage/event{agent_param}"],
        "speech": {
            "language": language,
            "endOnSilence": 2,
        },
    }


def _build_record_action(agent_id: int | None) -> dict:
    """Build a Vonage record NCCO action for call recording."""
    public_url = _get_public_url()
    agent_param = f"?agent_id={agent_id}" if agent_id else ""
    return {
        "action": "record",
        "eventUrl": [f"{public_url}/v1/voip/vonage/recording{agent_param}"],
        "split": "conversation",
        "channels": 2,
        "format": "mp3",
    }


# ─── Answer URL ───────────────────────────────────────────────


@router.post("/voip/vonage/answer")
async def vonage_answer(
    request: Request,
    agent_id: int | None = Query(None),
) -> list[dict]:
    """Vonage Answer URL — called when an inbound call connects.

    Returns NCCO with: record action, greeting, and speech input.
    Configure this as the Answer URL in your Vonage Application.
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    store = get_voice_agent_store()
    config = store.get(agent_id)

    call_uuid = body.get("uuid", body.get("conversation_uuid", ""))
    caller = body.get("from", "")
    called = body.get("to", "")

    logger.info(
        "Vonage answer: uuid=%s, from=%s, agent_id=%s",
        call_uuid,
        caller,
        agent_id,
    )

    # Start tracking the call
    tracker = get_call_tracker()
    if tracker and call_uuid:
        asyncio.create_task(
            tracker.start_call(
                call_uuid=call_uuid,
                agent_id=agent_id,
                caller_number=caller,
                called_number=called,
                provider="vonage",
            )
        )

    ncco: list[dict] = [
        _build_record_action(agent_id),
        {
            "action": "talk",
            "text": config.greeting_message,
            "bargeIn": True,
            "language": config.tts_language,
        },
        _build_input_action(agent_id, config.tts_language),
    ]

    return ncco


# ─── Event URL (Speech Results) ──────────────────────────────


@router.post("/voip/vonage/event")
async def vonage_event(
    request: Request,
    agent_id: int | None = Query(None),
    orchestrator=Depends(get_orchestrator),
) -> list[dict]:
    """Vonage Event URL — called with speech transcription results.

    Searches the KB and returns answer NCCO + next input action.
    """
    body = await request.json()
    store = get_voice_agent_store()
    config = store.get(agent_id)

    # Extract speech result
    speech = body.get("speech", {})
    speech_results = speech.get("results", [])
    query = speech_results[0].get("text", "") if speech_results else ""
    speech_confidence = speech_results[0].get("confidence", 0.0) if speech_results else 0.0
    call_uuid = body.get("uuid", body.get("conversation_uuid", ""))

    # No speech detected
    if not query.strip():
        return [
            {
                "action": "talk",
                "text": "I didn't catch that. Could you please repeat your question?",
                "bargeIn": True,
            },
            _build_input_action(agent_id, config.tts_language),
        ]

    # Track caller turn
    tracker = get_call_tracker()
    if tracker and call_uuid:
        asyncio.create_task(
            tracker.record_turn(call_uuid, "caller", query, speech_confidence)
        )

    # Search KB
    start = time.monotonic()
    search_request = SearchRequest(
        query=query,
        limit=config.max_results,
        source="voip_vonage",
        conversation_id=call_uuid or str(uuid.uuid4()),
    )
    search_response = await orchestrator.search(search_request)

    answer, confidence = _build_answer(search_response.results, config)
    elapsed_ms = (time.monotonic() - start) * 1000

    logger.info(
        "Vonage event: query=%r, confidence=%.3f, time=%.1fms",
        query[:80],
        confidence,
        elapsed_ms,
    )

    # Track bot turn
    if tracker and call_uuid:
        asyncio.create_task(
            tracker.record_turn(call_uuid, "bot", answer, confidence)
        )

    # Log the query
    query_logger = get_query_logger()
    if query_logger:
        asyncio.create_task(
            query_logger.log(
                query=query,
                processed_query={
                    "intent": search_response.parsed_intent,
                    "source": "voip_vonage",
                    "call_uuid": call_uuid,
                    "agent_id": agent_id,
                },
                results_count=len(search_response.results),
                source="voip_vonage",
                conversation_id=call_uuid,
                search_time_ms=elapsed_ms,
            )
        )

    # Build NCCO response
    ncco: list[dict] = [
        {"action": "talk", "text": answer, "bargeIn": True},
    ]

    if confidence >= config.confidence_threshold:
        follow_up = (
            _suggest_follow_up(search_response.parsed_intent)
            if config.follow_up_enabled
            else None
        )
        if follow_up:
            ncco.append({"action": "talk", "text": follow_up, "bargeIn": True})

        ncco.append(
            {"action": "talk", "text": "Is there anything else I can help with?", "bargeIn": True}
        )
        ncco.append(_build_input_action(agent_id, config.tts_language))
    else:
        ncco.append({"action": "talk", "text": config.escalation_message})
        if config.escalation_number:
            ncco.append({
                "action": "connect",
                "endpoint": [{"type": "phone", "number": config.escalation_number}],
            })

    return ncco


# ─── Recording Callback ──────────────────────────────────────


@router.post("/voip/vonage/recording")
async def vonage_recording(
    request: Request,
    agent_id: int | None = Query(None),
) -> dict:
    """Vonage recording callback — receives recording URL after call ends.

    Downloads and stores the recording for audit purposes.
    """
    body = await request.json()

    recording_url = body.get("recording_url", "")
    call_uuid = body.get("conversation_uuid", body.get("uuid", ""))
    size = body.get("size", 0)
    timestamp = body.get("timestamp", "")

    logger.info(
        "Vonage recording: uuid=%s, url=%s, size=%d",
        call_uuid,
        recording_url[:80] if recording_url else "",
        size,
    )

    tracker = get_call_tracker()
    if tracker and call_uuid and recording_url:
        # TODO: In production, download the recording via Vonage API (JWT auth)
        # and store locally or in S3. For now, just save the URL.
        asyncio.create_task(
            tracker.save_recording(
                call_uuid=call_uuid,
                recording_url=recording_url,
                size_bytes=size,
            )
        )

    return {"status": "ok", "call_uuid": call_uuid}


# ─── Status Callback ─────────────────────────────────────────


@router.post("/voip/vonage/status")
async def vonage_status(
    request: Request,
    agent_id: int | None = Query(None),
) -> dict:
    """Vonage call status events — tracks call lifecycle.

    Receives: started, ringing, answered, completed, failed, etc.
    """
    body = await request.json()

    status = body.get("status", "")
    call_uuid = body.get("uuid", body.get("conversation_uuid", ""))
    direction = body.get("direction", "")
    timestamp = body.get("timestamp", "")

    logger.info(
        "Vonage status: uuid=%s, status=%s, direction=%s",
        call_uuid,
        status,
        direction,
    )

    tracker = get_call_tracker()
    if not tracker or not call_uuid:
        return {"status": "ok"}

    if status == "completed":
        # Finalize the call — save transcript, calculate metrics, trigger CRM
        asyncio.create_task(_finalize_call(tracker, call_uuid))
    elif status in ("failed", "rejected", "busy", "cancelled"):
        asyncio.create_task(
            tracker.update_status(call_uuid, status)
        )
    elif status == "answered":
        asyncio.create_task(
            tracker.update_status(call_uuid, "answered")
        )

    return {"status": "ok", "call_uuid": call_uuid}


# ─── Call Finalization ────────────────────────────────────────


async def _finalize_call(tracker, call_uuid: str) -> None:
    """End call, run AI analysis, store for Odoo to pull, send notifications."""
    record = await tracker.end_call(call_uuid)
    if record is None:
        return

    crm_analyzer = get_crm_analyzer()
    analysis = None

    if crm_analyzer and record.transcript:
        try:
            analysis = await crm_analyzer.analyze(
                transcript=record.transcript,
                caller_number=record.caller_number or "",
                duration_seconds=record.duration_seconds or 0,
                agent_id=record.agent_id,
            )
        except Exception as e:
            logger.error("CRM analysis failed for call %s: %s", call_uuid, e)

    # Store analysis in DB and mark as pending for Odoo cron to pick up
    if analysis and analysis.confidence >= 0.1:
        await _store_crm_analysis(record.id, analysis)

    # Send notifications (WhatsApp/SMS) if analysis is available
    if analysis:
        from app.dependencies import get_vonage_messages_client
        messages_client = get_vonage_messages_client()
        settings = get_settings()

        if messages_client:
            try:
                from app.core.notifications import notify_triage_result, send_caller_followup

                team_numbers = settings.notify_team_numbers
                priorities = set(p.strip() for p in settings.notify_on_priority.split(","))
                await notify_triage_result(
                    messages_client, analysis, record, team_numbers, priorities,
                )

                if settings.caller_followup_enabled:
                    await send_caller_followup(
                        messages_client, analysis, record,
                        channel=settings.caller_followup_channel,
                    )
            except Exception as e:
                logger.error("Notification failed for call %s: %s", call_uuid, e)


async def _store_crm_analysis(call_record_id: str, analysis) -> None:
    """Persist CRM analysis to the call record and mark as pending."""
    from sqlalchemy import update as sql_update
    from app.core.crm_analyzer import analysis_to_dict
    from app.dependencies import get_session_factory
    from app.models.call_record import CallRecord

    session_factory = get_session_factory()
    if not session_factory:
        return

    async with session_factory() as session:
        await session.execute(
            sql_update(CallRecord)
            .where(CallRecord.id == call_record_id)
            .values(
                crm_analysis=analysis_to_dict(analysis),
                crm_status="pending",
                conversation_summary=analysis.summary,
            )
        )
        await session.commit()

    logger.info("CRM analysis stored for call %s (status=pending)", call_record_id)


# ─── Helpers ──────────────────────────────────────────────────


def _build_answer(
    results: list, config,
) -> tuple[str, float]:
    """Build a TTS-friendly answer from search results."""
    if not results:
        return config.no_answer_message, 0.0

    top = results[0]
    confidence = top.relevance_score

    if confidence < config.confidence_threshold:
        return config.low_confidence_message, confidence

    content = top.content or top.snippet
    max_len = config.max_answer_length
    if len(content) > max_len:
        truncated = content[:max_len]
        last_period = truncated.rfind(".")
        if last_period > max_len // 2:
            content = truncated[: last_period + 1]
        else:
            content = truncated + "..."

    return content, confidence


def _suggest_follow_up(intent: str | None) -> str | None:
    """Suggest a follow-up question based on detected intent."""
    suggestions = {
        "return_policy": "Would you like to know how to start a return?",
        "pricing": "Would you like details on a specific plan?",
        "troubleshooting": "Did that solve your issue, or do you need more help?",
        "how_to": "Would you like me to go through the steps again?",
        "shipping": "Would you like to track an existing order?",
    }
    return suggestions.get(intent)
