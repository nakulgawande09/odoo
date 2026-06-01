"""Gemini Live WebSocket route + supporting HTTP endpoints.

The browser opens a WSS to `/v1/live/connect?token=…&agent_id=…&call_uuid=…`.
JWT (HS256) is validated against `live_jwt_secret`; on success a
`GeminiLiveSession` is opened against the agent's config and bridged
to the browser. Transcript turns are persisted through CallTracker so
the existing CallRecord pipeline (and downstream CRM analysis) work
unchanged.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.api.auth import verify_api_key
from app.api.v1.tools import QueryPreprocessor, search_kb
from app.core.live_session import GeminiLiveSession, LiveAgentConfig
from app.dependencies import (
    get_call_tracker,
    get_crm_analyzer,
    get_orchestrator,
    get_session_factory,
    get_settings,
    get_voice_agent_store,
)
from app.models.call_record import CallRecord

logger = logging.getLogger(__name__)

router = APIRouter()

# Must match the value in
# odoo_addon/kb_connector/controllers/session_proxy.py — used ONLY when
# neither LIVE_JWT_SECRET nor KB_API_KEYS is configured, so local dev
# works without setup. A loud warning is emitted on every fallback use.
_DEV_FALLBACK_JWT_SECRET = "kb-live-dev-fallback-do-not-use-in-prod"  # noqa: S105
_FALLBACK_LOGGED = False


# ── JWT helpers ──────────────────────────────────────────────


def _resolve_signing_secret(settings: Any) -> str:
    """Pick the HS256 secret used to validate live-session JWTs.

    Resolution order:
      1. `live_jwt_secret` setting — explicit override for WS auth.
      2. First entry of `api_keys` — reuses the existing Odoo↔FastAPI
         HTTP auth secret.
      3. A hardcoded dev sentinel — matches the Odoo controller's
         fallback so local dev works out-of-the-box. A warning is
         logged on every use.
    """
    global _FALLBACK_LOGGED
    explicit = (getattr(settings, "live_jwt_secret", "") or "").strip()
    if explicit:
        return explicit
    api_keys = getattr(settings, "api_keys", None) or []
    if api_keys:
        return str(api_keys[0]).strip()
    if not _FALLBACK_LOGGED:
        logger.warning(
            "Live WS using DEV fallback JWT secret — set KB_LIVE_JWT_SECRET "
            "or KB_API_KEYS (matching Odoo's `kb.service.api_key`) before "
            "going to production."
        )
        _FALLBACK_LOGGED = True
    return _DEV_FALLBACK_JWT_SECRET


def _decode_token(token: str, secret: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


# ── Lazy live-client cache ───────────────────────────────────

_live_client: Any = None
_preprocessor_cache: QueryPreprocessor | None = None


def _get_live_client(api_key: str) -> Any:
    """Return a shared `genai.Client` for the live API.

    Constructed lazily on first WS open so the FastAPI app boots even
    when Gemini isn't configured. The Live API lives on `v1alpha` —
    the default `v1beta` rejects `bidiGenerateContent`.
    """
    global _live_client
    if _live_client is None:
        try:
            from google import genai
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail="google-genai package not installed",
            ) from exc
        if not api_key:
            raise HTTPException(
                status_code=500, detail="GEMINI_API_KEY not configured",
            )
        _live_client = genai.Client(
            api_key=api_key,
            http_options={"api_version": "v1alpha"},
        )
    return _live_client


def _get_preprocessor(client: Any, model: str) -> QueryPreprocessor:
    global _preprocessor_cache
    if _preprocessor_cache is None:
        _preprocessor_cache = QueryPreprocessor(client=client, model=model)
    return _preprocessor_cache


# ── WebSocket: /v1/live/connect ──────────────────────────────


@router.websocket("/live/connect")
async def live_connect(
    websocket: WebSocket,
    token: str = Query(...),
    agent_id: int = Query(...),
    call_uuid: str = Query(...),
) -> None:
    """Bidirectional bridge: browser ↔ Gemini Live."""
    settings = get_settings()
    claims = _decode_token(token, _resolve_signing_secret(settings))

    # Token must be bound to this agent + call_uuid.
    if int(claims.get("agent_id", -1)) != int(agent_id):
        await websocket.close(code=4403)
        return
    if str(claims.get("call_uuid", "")) != call_uuid:
        await websocket.close(code=4403)
        return

    voice_agent_store = get_voice_agent_store()
    cfg = voice_agent_store.get(agent_id)
    live_cfg = LiveAgentConfig.from_voice_agent(
        cfg,
        live_voice=getattr(cfg, "live_voice", "") or settings.live_voice,
        system_prompt=getattr(cfg, "system_prompt", "") or "",
        kb_search_instruction=getattr(cfg, "kb_search_instruction", "") or "",
    )

    client = _get_live_client(settings.gemini_api_key)
    preprocessor = _get_preprocessor(client, settings.live_preprocessor_model)
    orchestrator = get_orchestrator()
    call_tracker = get_call_tracker()

    # Best-effort: ensure a CallRecord exists for this call_uuid.
    if call_tracker is not None:
        try:
            await call_tracker.get_or_start_call(
                call_uuid=call_uuid,
                agent_id=agent_id,
                caller_number=str(claims.get("caller_number") or ""),
                provider="gemini_live",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("call_tracker start failed: %s", exc)

    async def tool_handler(
        query: str,
        agent_id_arg: int,
        language: str | None,
        history: list[dict],
    ) -> dict:
        return await search_kb(
            query, agent_id_arg, language, history,
            preprocessor=preprocessor,
            orchestrator=orchestrator,
            voice_agent_store=voice_agent_store,
            call_uuid=call_uuid,
        )

    await websocket.accept()
    # Per-agent model override beats service default. Trim/blank → fall back.
    agent_model = (getattr(cfg, "live_model", "") or "").strip()
    effective_model = agent_model or settings.live_model
    logger.info(
        "Opening Gemini Live session: agent_id=%s model=%s voice=%s call=%s",
        agent_id, effective_model, live_cfg.live_voice, call_uuid,
    )
    session = GeminiLiveSession(
        client=client,
        model=effective_model,
        cfg=live_cfg,
        call_uuid=call_uuid,
        tool_handler=tool_handler,
        call_tracker=call_tracker,
    )

    try:
        await session.stream(_StarletteWSAdapter(websocket))
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        logger.exception("live session error: %s", exc)
    finally:
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass


class _StarletteWSAdapter:
    """Adapter so GeminiLiveSession (duck-typed WS) works with Starlette.

    Starlette's `receive()` returns a dict like `{"type": "websocket.receive",
    "bytes": ..., "text": ...}`. We pass it through, and `send_bytes` /
    `send_text` map directly.
    """

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws

    async def receive(self) -> dict | None:
        try:
            msg = await self._ws.receive()
        except WebSocketDisconnect:
            return {"type": "websocket.disconnect"}
        return msg

    async def send_bytes(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def send_text(self, text: str) -> None:
        await self._ws.send_text(text)


# ── HTTP helpers: transcript + finalize ──────────────────────


@router.get("/live/transcript/{call_uuid}")
async def get_live_transcript(
    call_uuid: str,
    _api_key: str | None = Depends(verify_api_key),
    session_factory=Depends(get_session_factory),
) -> dict:
    """Return the captured transcript for a live call.

    Reads from CallRecord (same store the Vonage flow uses), so the
    response is identical in shape to `/v1/calls/{call_uuid}/transcript`.
    """
    if session_factory is None:
        return {"call_uuid": call_uuid, "turns": [], "ready": False}

    async with session_factory() as session:
        result = await session.execute(
            select(CallRecord).where(CallRecord.call_uuid == call_uuid)
        )
        record = result.scalar_one_or_none()

    if record is None:
        return {"call_uuid": call_uuid, "turns": [], "ready": False}

    return {
        "call_uuid": call_uuid,
        "ready": record.status == "completed",
        "duration_seconds": record.duration_seconds,
        "total_queries": record.total_queries,
        "avg_confidence": record.avg_confidence,
        "conversation_summary": record.conversation_summary,
        "turns": record.transcript or [],
    }


@router.post("/live/finalize/{call_uuid}")
async def finalize_live_call(
    call_uuid: str,
    _api_key: str | None = Depends(verify_api_key),
) -> dict:
    """Mark the live call complete and trigger CRM analysis.

    Mirrors what the Vonage `status` webhook does so downstream code
    paths stay identical.
    """
    call_tracker = get_call_tracker()
    if call_tracker is None:
        raise HTTPException(status_code=503, detail="Call tracker not configured")

    record = await call_tracker.end_call(call_uuid)
    if record is None:
        raise HTTPException(status_code=404, detail="No active call to finalize")

    # Best-effort CRM analysis — same path the Vonage flow uses.
    crm_analyzer = get_crm_analyzer()
    if crm_analyzer is not None and record.transcript:
        try:
            await crm_analyzer.analyze(
                transcript=record.transcript,
                caller_number=record.caller_number or "",
                duration_seconds=record.duration_seconds or 0,
                agent_id=record.agent_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("CRM analysis failed for %s: %s", call_uuid, exc)

    return {
        "call_uuid": call_uuid,
        "status": record.status,
        "duration_seconds": record.duration_seconds,
        "total_queries": record.total_queries,
    }


# ── JWT minting helper (for unit tests / manual debugging) ───


def mint_token(
    *,
    secret: str,
    agent_id: int,
    call_uuid: str,
    user_id: int = 0,
    ttl_seconds: int = 1800,
    caller_number: str = "",
) -> str:
    """Sign a short-lived HS256 token bound to (agent_id, call_uuid).

    Used by the Odoo `/kb/session/live/start` controller and by tests.
    """
    now = int(time.time())
    return jwt.encode(
        {
            "iat": now,
            "exp": now + ttl_seconds,
            "user_id": user_id,
            "agent_id": agent_id,
            "call_uuid": call_uuid,
            "caller_number": caller_number,
        },
        secret,
        algorithm="HS256",
    )
