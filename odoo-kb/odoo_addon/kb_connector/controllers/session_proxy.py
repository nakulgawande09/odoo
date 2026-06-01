"""Session proxy controllers for the Test Console.

These endpoints are called by the LiveCallField OWL component to drive
a real call session against the KB service. They hide the KB service URL
and API key from the browser.

Two modes are supported:
  - "real_call":  Drives the full Vonage webhook pipeline — /voip/vonage/answer,
                  /event, /status — so every turn produces a real CallRecord
                  and triggers CRM analysis on hang-up.
  - "lightweight": Hits /voip/query only — no DB writes, no CRM, just RAG.

Plus a Gemini-Live-only path:
  - "live": Mints a short-lived JWT and points the browser at the FastAPI
            WebSocket route. Audio is bridged browser ↔ Gemini Live; KB
            grounding flows through the `search_kb` tool. Transcripts and
            CRM analysis reuse the same CallRecord pipeline as real_call.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import logging
import time
import uuid
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests as http_requests

from odoo import http
from odoo.http import request

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30  # seconds
_LIVE_JWT_TTL = 1800  # 30 minutes

# Hardcoded fallback used ONLY when no `kb.service.api_key` or
# `kb.service.live_jwt_secret` is configured. Lets local dev work
# out-of-the-box; in production both Odoo and FastAPI must agree on a
# real shared secret. The FastAPI side has the matching sentinel.
_DEV_FALLBACK_JWT_SECRET = "kb-live-dev-fallback-do-not-use-in-prod"  # noqa: S105


# ─── Helpers ─────────────────────────────────────────────────


def _kb_service_config() -> tuple[str, dict[str, str]]:
    """Read KB service URL + build auth headers from system parameters."""
    icp = request.env["ir.config_parameter"].sudo()
    base_url = icp.get_param("kb.service.url", "http://localhost:8100").rstrip("/")
    api_key = icp.get_param("kb.service.api_key", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return base_url, headers


def _live_jwt_secret() -> tuple[str, bool]:
    """Shared HS256 secret used to sign Live-API session tokens.

    Resolution order:
      1. `kb.service.live_jwt_secret` ICP — explicit override.
      2. `kb.service.api_key` ICP — reuses the secret already shared
         between Odoo and FastAPI for HTTP auth.
      3. A hardcoded dev sentinel — keeps local dev unblocked.

    Returns (secret, is_fallback). Caller is expected to log a warning
    when `is_fallback` is True so prod misconfigurations are visible.
    """
    icp = request.env["ir.config_parameter"].sudo()
    explicit = (icp.get_param("kb.service.live_jwt_secret", "") or "").strip()
    if explicit:
        return explicit, False
    api_key = (icp.get_param("kb.service.api_key", "") or "").strip()
    if api_key:
        return api_key, False
    return _DEV_FALLBACK_JWT_SECRET, True


def _b64url(data: bytes) -> str:
    """Base64url-encode without padding (per JWT spec)."""
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _mint_hs256_jwt(claims: dict[str, Any], secret: str) -> str:
    """Sign a minimal HS256 JWT without bringing in pyjwt as an Odoo dep.

    Output format: <header>.<payload>.<sig>, all base64url-encoded.
    """
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"},
                                separators=(",", ":")).encode("utf-8"))
    payload = _b64url(json.dumps(claims, separators=(",", ":"),
                                 sort_keys=True).encode("utf-8"))
    signing_input = f"{header}.{payload}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


def _ws_url_from_http(http_url: str) -> str:
    """Convert http:// → ws:// (and https:// → wss://) preserving host/port/path."""
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(parsed._replace(scheme=scheme))


def _extract_talk_text(ncco: list[dict]) -> str:
    """Concatenate the text from all 'talk' actions in an NCCO response."""
    parts = [
        action.get("text", "")
        for action in ncco
        if action.get("action") == "talk" and action.get("text")
    ]
    return " ".join(p for p in parts if p).strip()


def _ncco_has_escalation(ncco: list[dict]) -> bool:
    """An NCCO with a 'connect' action means the agent is escalating."""
    return any(a.get("action") == "connect" for a in ncco)


def _agent_or_raise(agent_id):
    """Fetch the agent record or raise a 404-style error."""
    # Accept int, str, dict ({id: N}), or list ([id, name]) — the OWL record
    # representation differs across Odoo versions.
    if isinstance(agent_id, dict):
        agent_id = agent_id.get("id") or agent_id.get("resId")
    elif isinstance(agent_id, (list, tuple)) and agent_id:
        agent_id = agent_id[0]
    try:
        agent_id_int = int(agent_id)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid agent_id: {agent_id!r}")
    agent = request.env["kb.voice.agent"].browse(agent_id_int)
    if not agent.exists():
        raise ValueError(f"Voice agent {agent_id_int} not found")
    return agent


# ─── JSON Controllers ────────────────────────────────────────


class SessionProxyController(http.Controller):
    """Backend-for-frontend proxy between the Odoo UI and the KB service."""

    # ── Session start ────────────────────────────────────────

    @http.route(
        "/kb/session/start",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def session_start(
        self,
        agent_id: int,
        caller_number: str = "+15550000000",
        record_as_real_call: bool = True,
    ) -> dict:
        """Open a new session. Returns the greeting + a fresh call_uuid."""
        agent = _agent_or_raise(agent_id)
        base_url, headers = _kb_service_config()
        call_uuid = str(uuid.uuid4())

        if record_as_real_call:
            # Drive the Vonage answer webhook to get a real CallRecord
            try:
                resp = http_requests.post(
                    f"{base_url}/v1/voip/vonage/answer",
                    params={"agent_id": agent.id},
                    json={
                        "uuid": call_uuid,
                        "conversation_uuid": call_uuid,
                        "from": caller_number,
                        "to": "+18001234567",
                    },
                    headers=headers,
                    timeout=_HTTP_TIMEOUT,
                )
                resp.raise_for_status()
                ncco = resp.json()
                greeting = _extract_talk_text(ncco) or agent.greeting_message
            except http_requests.RequestException as exc:
                logger.warning("Real-call start failed, falling back: %s", exc)
                greeting = agent.greeting_message
        else:
            # Lightweight mode — just use the agent's configured greeting
            greeting = agent.greeting_message

        return {
            "call_uuid": call_uuid,
            "greeting": greeting,
            "mode": "real_call" if record_as_real_call else "lightweight",
            "tts_language": agent.tts_language or "en-US",
            "tts_voice": agent.tts_voice if agent.tts_voice != "default" else None,
        }

    # ── One turn ─────────────────────────────────────────────

    @http.route(
        "/kb/session/turn",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def session_turn(
        self,
        call_uuid: str,
        text: str,
        agent_id: int,
        mode: str = "real_call",
        caller_number: str = "+15550000000",
    ) -> dict:
        """Send one caller utterance, return the agent's answer."""
        if not text or not text.strip():
            return {"error": "Empty text"}

        agent = _agent_or_raise(agent_id)
        base_url, headers = _kb_service_config()
        query_text = text.strip()

        if mode == "real_call":
            # Full Vonage pipeline — produces CallRecord transcript
            try:
                resp = http_requests.post(
                    f"{base_url}/v1/voip/vonage/event",
                    params={"agent_id": agent.id},
                    json={
                        "uuid": call_uuid,
                        "conversation_uuid": call_uuid,
                        "speech": {
                            "results": [
                                {"text": query_text, "confidence": 0.95},
                            ],
                        },
                    },
                    headers=headers,
                    timeout=_HTTP_TIMEOUT,
                )
                resp.raise_for_status()
                ncco = resp.json()
                answer = _extract_talk_text(ncco)
                escalated = _ncco_has_escalation(ncco)
                # Vonage endpoint doesn't surface confidence/intent in the NCCO
                return {
                    "answer": answer,
                    "confidence": None,
                    "intent": None,
                    "follow_up": None,
                    "escalated": escalated,
                }
            except http_requests.RequestException as exc:
                return {"error": f"KB service error: {exc}"}
        else:
            # Lightweight /voip/query — gets confidence + intent in JSON
            try:
                resp = http_requests.post(
                    f"{base_url}/v1/voip/query",
                    json={
                        "query": query_text,
                        "provider": agent.voip_provider or "generic",
                        "call_id": call_uuid,
                        "caller_number": caller_number,
                        "max_results": agent.max_results or 3,
                        "agent_id": agent.id,
                    },
                    headers=headers,
                    timeout=_HTTP_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
                return {
                    "answer": data.get("answer", ""),
                    "confidence": data.get("confidence"),
                    "intent": data.get("intent"),
                    "follow_up": data.get("follow_up"),
                    "escalated": False,
                }
            except http_requests.RequestException as exc:
                return {"error": f"KB service error: {exc}"}

    # ── Session end ──────────────────────────────────────────

    @http.route(
        "/kb/session/end",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def session_end(
        self,
        call_uuid: str,
        mode: str = "real_call",
    ) -> dict:
        """Finalize the call. In real-call mode this triggers CRM analysis."""
        base_url, headers = _kb_service_config()

        if mode != "real_call":
            # Lightweight mode has no server-side state to clean up
            return {"status": "ok", "mode": mode}

        try:
            resp = http_requests.post(
                f"{base_url}/v1/voip/vonage/status",
                json={
                    "uuid": call_uuid,
                    "conversation_uuid": call_uuid,
                    "status": "completed",
                    "direction": "inbound",
                    "timestamp": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                    ),
                },
                headers=headers,
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            return {"status": "ok", "mode": mode}
        except http_requests.RequestException as exc:
            return {"status": "error", "error": str(exc)}

    # ── Fetch final record (poll-able) ───────────────────────

    @http.route(
        "/kb/session/record",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def session_record(self, call_uuid: str) -> dict:
        """Fetch the finalized CallRecord and CRM analysis for display."""
        base_url, headers = _kb_service_config()
        try:
            resp = http_requests.get(
                f"{base_url}/v1/calls/{call_uuid}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 404:
                return {"ready": False}
            resp.raise_for_status()
        except http_requests.RequestException as exc:
            return {"ready": False, "error": str(exc)}

        data = resp.json()

        # Also pull CRM analysis from /crm/pending if available
        crm_analysis: dict[str, Any] | None = None
        try:
            pending_resp = http_requests.get(
                f"{base_url}/v1/crm/pending",
                headers=headers,
                timeout=10,
            )
            if pending_resp.ok:
                for call in pending_resp.json().get("calls", []):
                    if call.get("call_uuid") == call_uuid:
                        crm_analysis = call.get("crm_analysis") or None
                        break
        except http_requests.RequestException:
            pass

        return {
            "ready": True,
            "call_uuid": data.get("call_uuid"),
            "status": data.get("status"),
            "duration_seconds": data.get("duration_seconds"),
            "total_queries": data.get("total_queries"),
            "avg_confidence": data.get("avg_confidence"),
            "conversation_summary": data.get("conversation_summary"),
            "transcript": data.get("transcript") or [],
            "crm_analysis": crm_analysis,
        }

    # ── TTS (plays the agent's answer in the browser) ────────

    @http.route(
        "/kb/session/tts",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def session_tts(self, agent_id: str = "0", text: str = "") -> http.Response:
        """Proxy to /v1/tts/test — returns WAV bytes to play in <audio>."""
        try:
            agent_id_int = int(agent_id)
        except (TypeError, ValueError):
            return request.make_response(
                "Invalid agent_id",
                headers=[("Content-Type", "text/plain")],
                status=400,
            )

        if not text.strip():
            return request.make_response(
                "No text provided",
                headers=[("Content-Type", "text/plain")],
                status=400,
            )

        agent = request.env["kb.voice.agent"].browse(agent_id_int)
        if not agent.exists():
            return request.not_found()

        base_url, headers = _kb_service_config()
        payload = {
            "text": text[:1000],
            "voice": agent.tts_voice if agent.tts_voice != "default" else None,
            "language": agent.tts_language or "en-US",
        }

        try:
            resp = http_requests.post(
                f"{base_url}/v1/tts/test",
                json=payload,
                headers=headers,
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
        except http_requests.HTTPError as exc:
            upstream = exc.response
            detail = upstream.text if upstream is not None else str(exc)
            status = upstream.status_code if upstream is not None else 502
            logger.warning(
                "TTS upstream returned %s for text=%r voice=%r lang=%r: %s",
                status,
                payload["text"][:80],
                payload["voice"],
                payload["language"],
                detail[:500],
            )
            return request.make_response(
                f"TTS failed ({status}): {detail}",
                headers=[("Content-Type", "text/plain")],
                status=502,
            )
        except http_requests.RequestException as exc:
            logger.warning(
                "TTS request failed (no response) for voice=%r lang=%r: %s",
                payload["voice"],
                payload["language"],
                exc,
            )
            return request.make_response(
                f"TTS failed: {exc}",
                headers=[("Content-Type", "text/plain")],
                status=502,
            )

        return request.make_response(
            resp.content,
            headers=[
                ("Content-Type", "audio/wav"),
                ("Content-Disposition", "inline; filename=agent_reply.wav"),
                ("Cache-Control", "no-store"),
            ],
        )

    # ── STT fallback (Safari etc.) ───────────────────────────

    @http.route(
        "/kb/session/stt",
        type="http",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def session_stt(self, **kwargs) -> http.Response:
        """Accept a multipart audio blob and return the transcription as JSON."""
        audio_file = kwargs.get("audio")
        language = kwargs.get("language", "en-US")
        if not audio_file:
            return request.make_response(
                json.dumps({"error": "No audio file provided"}),
                headers=[("Content-Type", "application/json")],
                status=400,
            )

        audio_bytes = audio_file.read()
        mime_type = getattr(audio_file, "content_type", None) or "audio/webm"
        base_url, headers = _kb_service_config()
        # Strip content-type: requests will set the multipart boundary for us
        headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}

        try:
            resp = http_requests.post(
                f"{base_url}/v1/stt/transcribe",
                files={"audio": (audio_file.filename or "audio.webm", audio_bytes, mime_type)},
                data={"language": language},
                headers=headers,
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
        except http_requests.RequestException as exc:
            status = exc.response.status_code if exc.response is not None else 502
            detail = exc.response.text if exc.response is not None else str(exc)
            return request.make_response(
                json.dumps({"error": f"STT failed: {detail}"}),
                headers=[("Content-Type", "application/json")],
                status=status,
            )

        return request.make_response(
            resp.content,
            headers=[("Content-Type", "application/json")],
            status=200,
        )

    # ── Gemini Live: bootstrap + finalize ────────────────────

    @http.route(
        "/kb/session/live/start",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def session_live_start(
        self,
        agent_id: int,
        caller_number: str = "+15550000000",
    ) -> dict:
        """Mint a JWT bound to (user, agent_id, call_uuid) and return the WS URL.

        The browser then opens a WebSocket directly to the FastAPI service
        (Odoo can't proxy WebSockets), passing this token. The FastAPI route
        validates the token, opens the Gemini Live session, and bridges audio.
        """
        agent = _agent_or_raise(agent_id)
        secret, is_fallback = _live_jwt_secret()
        if is_fallback:
            logger.warning(
                "Live session using DEV fallback JWT secret — set "
                "`kb.service.api_key` (Odoo) and `KB_API_KEYS` (FastAPI) "
                "to the same value before going to production."
            )

        base_url, _ = _kb_service_config()
        call_uuid = str(uuid.uuid4())
        now = int(time.time())
        token = _mint_hs256_jwt(
            {
                "iat": now,
                "exp": now + _LIVE_JWT_TTL,
                "user_id": request.env.user.id,
                "agent_id": agent.id,
                "call_uuid": call_uuid,
                "caller_number": caller_number,
            },
            secret,
        )

        ws_url = _ws_url_from_http(f"{base_url}/v1/live/connect")
        return {
            "call_uuid": call_uuid,
            "ws_url": ws_url,
            "token": token,
            "agent_id": agent.id,
            "agent_config": {
                "name": agent.name,
                "greeting": agent.greeting_message,
                "tts_language": agent.tts_language or "en-US",
                "live_voice": getattr(agent, "live_voice", "") or None,
            },
        }

    @http.route(
        "/kb/session/live/end",
        type="jsonrpc",
        auth="user",
        methods=["POST"],
        csrf=False,
    )
    def session_live_end(self, call_uuid: str) -> dict:
        """Finalize the live call and fetch the persisted record + CRM analysis."""
        base_url, headers = _kb_service_config()
        try:
            http_requests.post(
                f"{base_url}/v1/live/finalize/{call_uuid}",
                headers=headers,
                timeout=_HTTP_TIMEOUT,
            )
        except http_requests.RequestException as exc:
            logger.warning("live finalize failed: %s", exc)

        # Reuse the existing record poller — same shape as Vonage flow.
        try:
            resp = http_requests.get(
                f"{base_url}/v1/calls/{call_uuid}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 404:
                return {"ready": False}
            resp.raise_for_status()
            data = resp.json()
        except http_requests.RequestException as exc:
            return {"ready": False, "error": str(exc)}

        crm_analysis: dict[str, Any] | None = None
        try:
            pending_resp = http_requests.get(
                f"{base_url}/v1/crm/pending",
                headers=headers,
                timeout=10,
            )
            if pending_resp.ok:
                for call in pending_resp.json().get("calls", []):
                    if call.get("call_uuid") == call_uuid:
                        crm_analysis = call.get("crm_analysis") or None
                        break
        except http_requests.RequestException:
            pass

        return {
            "ready": True,
            "call_uuid": data.get("call_uuid"),
            "status": data.get("status"),
            "duration_seconds": data.get("duration_seconds"),
            "total_queries": data.get("total_queries"),
            "avg_confidence": data.get("avg_confidence"),
            "conversation_summary": data.get("conversation_summary"),
            "transcript": data.get("transcript") or [],
            "crm_analysis": crm_analysis,
        }
