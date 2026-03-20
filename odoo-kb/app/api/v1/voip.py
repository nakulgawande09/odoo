"""VOIP integration layer: webhook API for real-time KB lookups during calls.

Provides a standardized webhook interface that VOIP providers
(Twilio, Vonage/Nexmo, Asterisk/FreePBX, custom SIP) can call
to get KB answers during live calls.

Architecture:
  VOIP Provider → webhook POST → /v1/voip/query → KB search → structured response
                                 /v1/voip/twilio → TwiML response
                                 /v1/voip/vonage → Vonage NCCO response

Voice agent configs are pushed from Odoo UI via PUT /v1/voip/agents/{id}
and used at runtime to customize behavior (greeting, escalation, etc.).
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.api.auth import verify_api_key
from app.core.voice_agent_config import VoiceAgentConfig, VoiceAgentStore
from app.dependencies import get_orchestrator, get_query_logger, get_voice_agent_store
from app.schemas.search import SearchRequest

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────

class VOIPQueryRequest(BaseModel):
    """Provider-agnostic VOIP query request."""
    query: str = Field(..., min_length=1, max_length=2000, description="Transcribed speech or IVR input")
    call_id: str | None = None
    caller_number: str | None = None
    provider: str = "generic"  # "twilio", "vonage", "asterisk", "generic"
    language: str = "en"
    max_results: int = Field(default=3, ge=1, le=10)
    agent_id: int | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class VOIPSource(BaseModel):
    """Source reference for a VOIP answer."""
    title: str
    snippet: str
    relevance: float
    document_id: str


class VOIPQueryResponse(BaseModel):
    """Structured response for VOIP integrations."""
    call_id: str
    query: str
    answer: str  # Best answer text (ready for TTS)
    confidence: float
    sources: list[VOIPSource] = Field(default_factory=list)
    intent: str | None = None
    follow_up: str | None = None  # Suggested follow-up question
    response_time_ms: float
    agent_name: str | None = None


# ─── Agent Config CRUD ────────────────────────────────────────

@router.put("/voip/agents/{agent_id}")
async def upsert_agent_config(
    agent_id: int,
    config: dict[str, Any],
    _api_key: str | None = Depends(verify_api_key),
) -> dict:
    """Create or update a voice agent configuration (pushed from Odoo UI)."""
    store = get_voice_agent_store()
    agent = store.put(agent_id, config)
    return {"status": "ok", "agent_id": agent.agent_id, "name": agent.name}


@router.get("/voip/agents")
async def list_agent_configs(
    _api_key: str | None = Depends(verify_api_key),
) -> list[dict]:
    """List all voice agent configurations."""
    store = get_voice_agent_store()
    return [
        {"agent_id": a.agent_id, "name": a.name, "provider": a.provider}
        for a in store.list_all()
    ]


@router.delete("/voip/agents/{agent_id}")
async def delete_agent_config(
    agent_id: int,
    _api_key: str | None = Depends(verify_api_key),
) -> dict:
    """Delete a voice agent configuration."""
    store = get_voice_agent_store()
    deleted = store.delete(agent_id)
    return {"status": "deleted" if deleted else "not_found"}


# ─── Provider-Agnostic Endpoint ──────────────────────────────

@router.post("/voip/query", response_model=VOIPQueryResponse)
async def voip_query(
    request: VOIPQueryRequest,
    agent_id: int | None = Query(None),
    _api_key: str | None = Depends(verify_api_key),
    orchestrator=Depends(get_orchestrator),
) -> VOIPQueryResponse:
    """Universal VOIP webhook: accepts transcribed speech, returns KB answer.

    Reads voice agent config from the store (pushed from Odoo UI).
    Pass ?agent_id=N as query param or in the request body.
    """
    start = time.monotonic()
    call_id = request.call_id or str(uuid.uuid4())

    # Load agent config
    effective_agent_id = request.agent_id or agent_id
    store = get_voice_agent_store()
    config = store.get(effective_agent_id)

    # Search KB using the orchestrator
    search_request = SearchRequest(
        query=request.query,
        limit=request.max_results or config.max_results,
        source=f"voip_{request.provider}",
        conversation_id=call_id,
    )
    search_response = await orchestrator.search(search_request)

    # Build TTS-friendly answer using agent config
    answer, confidence = _build_answer(search_response.results, config)
    sources = [
        VOIPSource(
            title=r.title,
            snippet=r.snippet[:200],
            relevance=r.relevance_score,
            document_id=r.document_id,
        )
        for r in search_response.results[:3]
    ]

    # Generate follow-up suggestion
    follow_up = None
    if config.follow_up_enabled:
        follow_up = _suggest_follow_up(search_response.parsed_intent)

    elapsed_ms = (time.monotonic() - start) * 1000

    # Log VOIP query for analytics
    query_logger = get_query_logger()
    if query_logger:
        import asyncio
        asyncio.create_task(
            query_logger.log(
                query=request.query,
                processed_query={
                    "intent": search_response.parsed_intent,
                    "source": f"voip_{request.provider}",
                    "call_id": call_id,
                    "caller_number": request.caller_number,
                    "agent_id": effective_agent_id,
                },
                results_count=len(search_response.results),
                source=f"voip_{request.provider}",
                conversation_id=call_id,
                search_time_ms=elapsed_ms,
            )
        )

    return VOIPQueryResponse(
        call_id=call_id,
        query=request.query,
        answer=answer,
        confidence=confidence,
        sources=sources,
        intent=search_response.parsed_intent,
        follow_up=follow_up,
        response_time_ms=round(elapsed_ms, 2),
        agent_name=config.name if effective_agent_id else None,
    )


# ─── Twilio Webhook ──────────────────────────────────────────

@router.post("/voip/twilio")
async def twilio_webhook(
    request: Request,
    agent_id: int | None = Query(None),
    orchestrator=Depends(get_orchestrator),
) -> dict:
    """Twilio-specific webhook that accepts Twilio's POST format.

    Configure in Twilio as:
      <Gather input="speech" action="/v1/voip/twilio?agent_id=1" method="POST">
        <Say>How can I help you?</Say>
      </Gather>
    """
    form = await request.form()
    store = get_voice_agent_store()
    config = store.get(agent_id)

    speech_result = form.get("SpeechResult", "")
    call_sid = form.get("CallSid", "")
    digits = form.get("Digits", "")

    query = str(speech_result or digits)
    if not query.strip():
        return _twilio_response(
            "I didn't catch that. Could you please repeat your question?",
            gather=True,
            config=config,
        )

    search_request = SearchRequest(
        query=query,
        limit=config.max_results,
        source="voip_twilio",
        conversation_id=str(call_sid),
    )
    search_response = await orchestrator.search(search_request)
    answer, confidence = _build_answer(search_response.results, config)

    if confidence < config.confidence_threshold:
        escalation_msg = config.escalation_message or config.low_confidence_message
        return _twilio_response(escalation_msg, gather=False, config=config)

    follow_up = _suggest_follow_up(search_response.parsed_intent) if config.follow_up_enabled else None
    full_response = answer
    if follow_up:
        full_response += f" {follow_up}"

    return _twilio_response(full_response, gather=True, config=config)


# ─── Vonage (Nexmo) Webhook ──────────────────────────────────

@router.post("/voip/vonage")
async def vonage_webhook(
    request: Request,
    agent_id: int | None = Query(None),
    orchestrator=Depends(get_orchestrator),
) -> list[dict]:
    """Vonage Voice API webhook. Returns NCCO actions."""
    body = await request.json()
    store = get_voice_agent_store()
    config = store.get(agent_id)

    speech_results = body.get("speech", {}).get("results", [])
    query = speech_results[0].get("text", "") if speech_results else ""
    call_uuid = body.get("uuid", "")

    if not query.strip():
        return [
            {"action": "talk", "text": "I didn't catch that. Could you please repeat?", "bargeIn": True},
            {"action": "input", "type": ["speech"], "speech": {"language": config.tts_language}},
        ]

    search_request = SearchRequest(
        query=query,
        limit=config.max_results,
        source="voip_vonage",
        conversation_id=call_uuid,
    )
    search_response = await orchestrator.search(search_request)
    answer, confidence = _build_answer(search_response.results, config)

    ncco: list[dict] = [{"action": "talk", "text": answer, "bargeIn": True}]

    if confidence >= config.confidence_threshold:
        ncco.append({"action": "talk", "text": "Is there anything else I can help with?", "bargeIn": True})
        ncco.append({"action": "input", "type": ["speech"], "speech": {"language": config.tts_language}})
    else:
        ncco.append({"action": "talk", "text": config.escalation_message})
        if config.escalation_number:
            ncco.append({"action": "connect", "endpoint": [{"type": "phone", "number": config.escalation_number}]})

    return ncco


# ─── SIP/Asterisk Webhook ────────────────────────────────────

@router.post("/voip/sip")
async def sip_webhook(
    request: Request,
    agent_id: int | None = Query(None),
    _api_key: str | None = Depends(verify_api_key),
    orchestrator=Depends(get_orchestrator),
) -> dict:
    """Generic SIP/Asterisk webhook for AGI or ARI integrations."""
    body = await request.json()
    store = get_voice_agent_store()
    config = store.get(agent_id)

    query = body.get("query", body.get("text", ""))
    channel = body.get("channel", "")

    if not query:
        return {"status": "error", "message": "No query provided"}

    search_request = SearchRequest(
        query=query,
        limit=config.max_results,
        source="voip_sip",
        conversation_id=channel or None,
    )
    search_response = await orchestrator.search(search_request)
    answer, confidence = _build_answer(search_response.results, config)

    return {
        "status": "ok",
        "answer": answer,
        "confidence": confidence,
        "intent": search_response.parsed_intent,
        "results_count": len(search_response.results),
        "channel": channel,
        "escalate": confidence < config.confidence_threshold,
    }


# ─── Helpers ──────────────────────────────────────────────────

def _build_answer(
    results: list, config: VoiceAgentConfig | None = None
) -> tuple[str, float]:
    """Build a TTS-friendly answer from search results."""
    if config is None:
        from app.core.voice_agent_config import DEFAULT_CONFIG
        config = DEFAULT_CONFIG

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


def _twilio_response(
    text: str, gather: bool = True, config: VoiceAgentConfig | None = None
) -> dict:
    """Build a Twilio-compatible response structure."""
    if config is None:
        from app.core.voice_agent_config import DEFAULT_CONFIG
        config = DEFAULT_CONFIG

    response: dict[str, Any] = {
        "say": text,
        "gather": gather,
        "voice": config.tts_voice,
        "language": config.tts_language,
    }
    if gather:
        response["gather_config"] = {
            "input": "speech",
            "timeout": 5,
            "speechTimeout": "auto",
            "language": config.tts_language,
        }
    return response
