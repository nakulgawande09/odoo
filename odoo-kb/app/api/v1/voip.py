"""VOIP integration layer: webhook API for real-time KB lookups during calls.

Provides a standardized webhook interface that VOIP providers
(Twilio, Vonage/Nexmo, Asterisk/FreePBX, custom SIP) can call
to get KB answers during live calls.

Architecture:
  VOIP Provider → webhook POST → /v1/voip/query → KB search → structured response
                                 /v1/voip/twilio → TwiML response
                                 /v1/voip/vonage → Vonage NCCO response

The VOIP layer translates between provider-specific formats and
the KB search API, adding call-context metadata (caller number,
call duration, IVR path) for analytics and personalization.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.auth import verify_api_key
from app.dependencies import get_orchestrator, get_query_logger
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


# ─── Provider-Agnostic Endpoint ──────────────────────────────

@router.post("/voip/query", response_model=VOIPQueryResponse)
async def voip_query(
    request: VOIPQueryRequest,
    _api_key: str | None = Depends(verify_api_key),
    orchestrator=Depends(get_orchestrator),
) -> VOIPQueryResponse:
    """Universal VOIP webhook: accepts transcribed speech, returns KB answer.

    This is the primary endpoint for VOIP integrations. It accepts
    a natural language query (from speech-to-text) and returns a
    structured response suitable for text-to-speech playback.
    """
    start = time.monotonic()
    call_id = request.call_id or str(uuid.uuid4())

    # Search KB using the orchestrator
    search_request = SearchRequest(
        query=request.query,
        limit=request.max_results,
        source=f"voip_{request.provider}",
        conversation_id=call_id,  # Use call_id as conversation for context
    )
    search_response = await orchestrator.search(search_request)

    # Build TTS-friendly answer from top results
    answer, confidence = _build_answer(search_response.results)
    sources = [
        VOIPSource(
            title=r.title,
            snippet=r.snippet[:200],
            relevance=r.relevance_score,
            document_id=r.document_id,
        )
        for r in search_response.results[:3]
    ]

    # Generate follow-up suggestion based on intent
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
    )


# ─── Twilio Webhook ──────────────────────────────────────────

@router.post("/voip/twilio")
async def twilio_webhook(
    request: Request,
    orchestrator=Depends(get_orchestrator),
) -> dict:
    """Twilio-specific webhook that accepts Twilio's POST format.

    Twilio sends form-encoded data with SpeechResult (from <Gather>),
    CallSid, From, etc. Returns TwiML-compatible JSON for the response.

    Configure in Twilio as:
      <Gather input="speech" action="/v1/voip/twilio" method="POST">
        <Say>How can I help you?</Say>
      </Gather>
    """
    form = await request.form()

    speech_result = form.get("SpeechResult", "")
    call_sid = form.get("CallSid", "")
    caller = form.get("From", "")
    digits = form.get("Digits", "")

    # Use speech or DTMF input
    query = str(speech_result or digits)
    if not query.strip():
        return _twilio_response(
            "I didn't catch that. Could you please repeat your question?",
            gather=True,
        )

    # Search KB
    search_request = SearchRequest(
        query=query,
        limit=3,
        source="voip_twilio",
        conversation_id=str(call_sid),
    )
    search_response = await orchestrator.search(search_request)
    answer, confidence = _build_answer(search_response.results)

    if confidence < 0.3:
        return _twilio_response(
            "I'm not sure I found a good answer for that. "
            "Let me transfer you to an agent who can help.",
            gather=False,
        )

    follow_up = _suggest_follow_up(search_response.parsed_intent)
    full_response = answer
    if follow_up:
        full_response += f" {follow_up}"

    return _twilio_response(full_response, gather=True)


# ─── Vonage (Nexmo) Webhook ──────────────────────────────────

@router.post("/voip/vonage")
async def vonage_webhook(
    request: Request,
    orchestrator=Depends(get_orchestrator),
) -> list[dict]:
    """Vonage Voice API webhook. Returns NCCO (Nexmo Call Control Objects).

    Vonage sends JSON with speech recognition results.
    Returns NCCO actions for the voice call flow.
    """
    body = await request.json()

    speech_results = body.get("speech", {}).get("results", [])
    query = speech_results[0].get("text", "") if speech_results else ""
    call_uuid = body.get("uuid", "")

    if not query.strip():
        return [
            {
                "action": "talk",
                "text": "I didn't catch that. Could you please repeat?",
                "bargeIn": True,
            },
            {
                "action": "input",
                "type": ["speech"],
                "speech": {"language": "en-US"},
                "eventUrl": [body.get("eventUrl", "")],
            },
        ]

    # Search KB
    search_request = SearchRequest(
        query=query,
        limit=3,
        source="voip_vonage",
        conversation_id=call_uuid,
    )
    search_response = await orchestrator.search(search_request)
    answer, confidence = _build_answer(search_response.results)

    ncco: list[dict] = [
        {"action": "talk", "text": answer, "bargeIn": True},
    ]

    # Continue listening for more questions
    if confidence >= 0.3:
        ncco.append({
            "action": "talk",
            "text": "Is there anything else I can help with?",
            "bargeIn": True,
        })
        ncco.append({
            "action": "input",
            "type": ["speech"],
            "speech": {"language": "en-US"},
        })
    else:
        ncco.append({
            "action": "talk",
            "text": "Let me connect you with a live agent.",
        })
        ncco.append({
            "action": "connect",
            "endpoint": [{"type": "phone", "number": body.get("fallback_number", "")}],
        })

    return ncco


# ─── SIP/Asterisk Webhook ────────────────────────────────────

@router.post("/voip/sip")
async def sip_webhook(
    request: Request,
    _api_key: str | None = Depends(verify_api_key),
    orchestrator=Depends(get_orchestrator),
) -> dict:
    """Generic SIP/Asterisk webhook for AGI or ARI integrations.

    Asterisk can call this via curl in an AGI script or via ARI
    external media. Returns a simple JSON response with the answer
    text for the PBX to play via TTS (Festival, Google TTS, etc.).
    """
    body = await request.json()

    query = body.get("query", body.get("text", ""))
    channel = body.get("channel", "")
    caller_id = body.get("callerid", body.get("caller_id", ""))

    if not query:
        return {"status": "error", "message": "No query provided"}

    search_request = SearchRequest(
        query=query,
        limit=3,
        source="voip_sip",
        conversation_id=channel or None,
    )
    search_response = await orchestrator.search(search_request)
    answer, confidence = _build_answer(search_response.results)

    return {
        "status": "ok",
        "answer": answer,
        "confidence": confidence,
        "intent": search_response.parsed_intent,
        "results_count": len(search_response.results),
        "channel": channel,
    }


# ─── Helpers ──────────────────────────────────────────────────

def _build_answer(results: list) -> tuple[str, float]:
    """Build a TTS-friendly answer from search results.

    Returns (answer_text, confidence_score).
    """
    if not results:
        return (
            "I couldn't find information about that in our knowledge base. "
            "Would you like me to connect you with a support agent?",
            0.0,
        )

    top = results[0]
    confidence = top.relevance_score

    if confidence < 0.3:
        return (
            "I'm not confident I have the right answer. "
            "Let me transfer you to someone who can help.",
            confidence,
        )

    # Use the top result's content, truncated for TTS
    content = top.content or top.snippet
    # Truncate to ~500 chars for reasonable TTS length
    if len(content) > 500:
        # Break at sentence boundary
        truncated = content[:500]
        last_period = truncated.rfind(".")
        if last_period > 200:
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


def _twilio_response(text: str, gather: bool = True) -> dict:
    """Build a Twilio-compatible response structure.

    Returns a dict that the caller should convert to TwiML.
    For production, use the twilio Python SDK to generate proper TwiML.
    """
    response: dict[str, Any] = {
        "say": text,
        "gather": gather,
    }
    if gather:
        response["gather_config"] = {
            "input": "speech",
            "timeout": 5,
            "speechTimeout": "auto",
            "language": "en-US",
        }
    return response
