"""Gemini Live session manager.

Wraps the long-lived `client.aio.live.connect()` WebSocket session and
bridges it bidirectionally with a browser WebSocket. KB grounding is
delivered through a `search_kb` function-calling tool that the live
model is strongly instructed to invoke before any factual claim.

The class is intentionally a thin orchestrator: query preprocessing
and KB retrieval live in `app.api.v1.tools.search_kb`, transcript
persistence reuses the existing `CallTracker`, and audio framing is
handled at the WebSocket boundary.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.core.voice_agent_config import VoiceAgentConfig

logger = logging.getLogger(__name__)


# Hard rule appended to every system instruction unless the agent
# overrides it via `kb_search_instruction`. The live model is far less
# likely to hallucinate when the rule is short, imperative, and last.
_DEFAULT_GROUNDING_RULE = (
    "Before any factual claim about products, policies, prices, hours, "
    "procedures, or specific data, you MUST call the `search_kb` tool "
    "with the user's question. Never invent answers from prior knowledge. "
    "If `search_kb` returns no relevant results, use the no-answer "
    "fallback message and offer to escalate."
)


@dataclass(frozen=True)
class LiveAgentConfig:
    """Subset of VoiceAgentConfig needed to drive a live session."""

    agent_id: int
    name: str
    greeting_message: str
    no_answer_message: str
    low_confidence_message: str
    goodbye_message: str
    system_prompt: str = ""
    kb_search_instruction: str = ""
    tts_language: str = "en-US"
    live_voice: str = "Aoede"
    confidence_threshold: float = 0.3
    max_results: int = 3
    escalation_mode: str = "transfer"
    escalation_number: str = ""
    escalation_message: str = ""

    @classmethod
    def from_voice_agent(
        cls,
        cfg: VoiceAgentConfig,
        *,
        live_voice: str,
        system_prompt: str = "",
        kb_search_instruction: str = "",
    ) -> "LiveAgentConfig":
        """Lift a VoiceAgentConfig into the live-session subset.

        `live_voice`, `system_prompt`, and `kb_search_instruction` are
        the live-only fields the existing config doesn't carry.
        """
        return cls(
            agent_id=cfg.agent_id,
            name=cfg.name,
            greeting_message=cfg.greeting_message,
            no_answer_message=cfg.no_answer_message,
            low_confidence_message=cfg.low_confidence_message,
            goodbye_message=cfg.goodbye_message,
            system_prompt=system_prompt,
            kb_search_instruction=kb_search_instruction,
            tts_language=cfg.tts_language,
            live_voice=live_voice,
            confidence_threshold=cfg.confidence_threshold,
            max_results=cfg.max_results,
            escalation_mode=cfg.escalation_mode,
            escalation_number=cfg.escalation_number,
            escalation_message=cfg.escalation_message,
        )


def _accumulate_transcript(buffer: str, chunk: str) -> str:
    """Merge a transcription chunk into the running buffer.

    Gemini Live transcription chunks are sometimes cumulative (each chunk
    contains the full text so far) and sometimes incremental (each chunk
    is just the new delta). Distinguish them: if the new chunk starts with
    the current buffer, treat it as cumulative and replace; otherwise treat
    it as a delta and append.
    """
    if not buffer:
        return chunk
    if chunk.startswith(buffer):
        return chunk
    return buffer + chunk


def assemble_system_instruction(cfg: LiveAgentConfig) -> str:
    """Build the live-model system instruction from agent config.

    Order matters — opening directive, persona, fallbacks, then the hard
    grounding rule last so it stays in attention. Empty fields are
    skipped so the prompt stays compact.
    """
    parts: list[str] = []

    parts.append(
        f"You are {cfg.name}, a multilingual voice assistant. "
        f"Reply in the same language the caller speaks; default to "
        f"{cfg.tts_language} when unsure."
    )

    if cfg.greeting_message:
        parts.append(
            f"On call start, greet the caller with: \"{cfg.greeting_message}\""
        )

    if cfg.system_prompt:
        parts.append(cfg.system_prompt.strip())

    fallback_lines: list[str] = []
    if cfg.no_answer_message:
        fallback_lines.append(
            f"- If you have no grounded answer: \"{cfg.no_answer_message}\""
        )
    if cfg.low_confidence_message:
        fallback_lines.append(
            f"- If your confidence is low: \"{cfg.low_confidence_message}\""
        )
    if cfg.goodbye_message:
        fallback_lines.append(
            f"- On hang-up or thank-you: \"{cfg.goodbye_message}\""
        )
    if fallback_lines:
        parts.append("Fallback responses:\n" + "\n".join(fallback_lines))

    if cfg.escalation_mode and cfg.escalation_mode != "none":
        parts.append(
            f"If the caller asks for a human or you cannot help after two "
            f"failed `search_kb` attempts, say: \"{cfg.escalation_message}\" "
            f"and end the call."
        )

    parts.append("Keep replies concise (one to three sentences) — this is voice.")

    parts.append(cfg.kb_search_instruction.strip() or _DEFAULT_GROUNDING_RULE)

    return "\n\n".join(parts)


def _build_search_kb_tool() -> dict:
    """Function-call schema declaring `search_kb` to the live model."""
    return {
        "name": "search_kb",
        "description": (
            "Search the knowledge base for grounded information. Call this "
            "before stating any specific fact. Pass the caller's question "
            "verbatim — preprocessing (rewriting, language normalization, "
            "intent extraction) happens server-side."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "The caller's question, verbatim.",
                },
            },
            "required": ["query"],
        },
    }


@dataclass
class _RunState:
    """Transient per-call counters and history.

    The transcription buffers exist because Gemini Live's `Transcription`
    chunks don't always carry a reliable `finished` flag -- the authoritative
    end-of-turn signal is `server_content.turn_complete`. We accumulate text
    per role and flush on `turn_complete` so the transcript actually lands.
    """

    started_at: float = field(default_factory=time.monotonic)
    history: list[dict] = field(default_factory=list)  # {role, text}
    input_buffer: str = ""
    output_buffer: str = ""


# Type alias for the tool handler the route wires in.
ToolHandler = Callable[
    [str, int, str | None, list[dict]],
    Awaitable[dict],
]


class GeminiLiveSession:
    """Bridges a browser WebSocket to Gemini Live for one call.

    Lifecycle:
      1. Caller constructs the session with agent config + tool handler.
      2. Caller awaits `stream(browser_ws)` — this opens the live
         connection, runs the bidirectional pumps until either side
         closes, and persists transcript turns through the call_tracker.

    The class never imports browser-specific types so it can be unit-
    tested with stub WebSocket fakes.
    """

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        cfg: LiveAgentConfig,
        call_uuid: str,
        tool_handler: ToolHandler,
        call_tracker: Any | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._cfg = cfg
        self._call_uuid = call_uuid
        self._tool_handler = tool_handler
        self._call_tracker = call_tracker
        self._state = _RunState()
        self._closed = False

    @property
    def transcript(self) -> list[dict]:
        return list(self._state.history)

    async def stream(self, browser_ws: Any) -> None:
        """Run the bidirectional pump until either side closes.

        `browser_ws` is duck-typed: needs `receive()` (returns dict with
        either `bytes` or `text`), `send_bytes(b)`, `send_text(s)`, and
        an awaitable `close()`.
        """
        from google.genai import types  # local import, package is optional

        config = self._build_live_config(types)

        try:
            async with self._client.aio.live.connect(
                model=self._model, config=config
            ) as live_ws:
                await asyncio.gather(
                    self._pump_browser_to_live(browser_ws, live_ws),
                    self._pump_live_to_browser(browser_ws, live_ws),
                )
        except Exception as exc:  # noqa: BLE001 — surface to client and log
            logger.exception("Gemini Live session crashed: %s", exc)
            await self._safe_send_text(
                browser_ws,
                json.dumps({"type": "error", "message": str(exc)}),
            )
        finally:
            await self._finalize()

    # ── Pump: browser → Gemini Live ──────────────────────────

    async def _pump_browser_to_live(self, browser_ws: Any, live_ws: Any) -> None:
        from google.genai import types

        try:
            while not self._closed:
                msg = await browser_ws.receive()
                if msg is None:
                    break
                # Starlette: dict with 'type', 'bytes', 'text'.
                if msg.get("type") == "websocket.disconnect":
                    break
                data_bytes = msg.get("bytes")
                data_text = msg.get("text")

                if data_bytes:
                    # Raw 16 kHz mono PCM frames from the browser worklet.
                    # `media=` was deprecated in google-genai; the live API
                    # now expects `audio=` / `video=` / `text=` instead and
                    # closes the WS with code 1007 if the old shape is used.
                    await live_ws.send_realtime_input(
                        audio=types.Blob(
                            data=data_bytes,
                            mime_type="audio/pcm;rate=16000",
                        ),
                    )
                elif data_text:
                    # Control messages (mute, end-of-turn hint, text-only fallback).
                    try:
                        ctrl = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    await self._handle_browser_control(ctrl, live_ws)
        except Exception as exc:  # noqa: BLE001
            logger.warning("browser→live pump ended: %s", exc)
            self._closed = True

    async def _handle_browser_control(self, ctrl: dict, live_ws: Any) -> None:
        from google.genai import types

        kind = ctrl.get("type")
        if kind == "text":
            text = (ctrl.get("text") or "").strip()
            if text:
                await live_ws.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text=text)]),
                    turn_complete=True,
                )
        elif kind == "end":
            self._closed = True

    # ── Pump: Gemini Live → browser ──────────────────────────

    async def _pump_live_to_browser(self, browser_ws: Any, live_ws: Any) -> None:
        try:
            async for response in live_ws.receive():
                if self._closed:
                    break
                await self._handle_live_response(response, browser_ws, live_ws)
        except Exception as exc:  # noqa: BLE001
            logger.warning("live→browser pump ended: %s", exc)
            self._closed = True

    async def _handle_live_response(
        self, response: Any, browser_ws: Any, live_ws: Any,
    ) -> None:
        # Tool calls — route to the search_kb handler.
        tool_call = getattr(response, "tool_call", None)
        if tool_call and getattr(tool_call, "function_calls", None):
            await self._handle_tool_calls(tool_call.function_calls, live_ws, browser_ws)
            return

        # Server-side input transcription (caller speech → text).
        server_content = getattr(response, "server_content", None)
        if server_content is None:
            return

        # Accumulate transcription chunks; do NOT mark them final here. Per-chunk
        # `finished` from Gemini Live is unreliable and emitting final repeatedly
        # would cause double-recording. The single source of truth for "turn is
        # done" is `server_content.turn_complete` below.
        input_tx = getattr(server_content, "input_transcription", None)
        if input_tx and getattr(input_tx, "text", ""):
            self._state.input_buffer = _accumulate_transcript(
                self._state.input_buffer, input_tx.text,
            )
            await self._emit_transcript(
                browser_ws,
                role="caller",
                text=self._state.input_buffer,
                final=False,
            )

        output_tx = getattr(server_content, "output_transcription", None)
        if output_tx and getattr(output_tx, "text", ""):
            self._state.output_buffer = _accumulate_transcript(
                self._state.output_buffer, output_tx.text,
            )
            await self._emit_transcript(
                browser_ws,
                role="agent",
                text=self._state.output_buffer,
                final=False,
            )

        # Model audio output — relay PCM frames to browser.
        model_turn = getattr(server_content, "model_turn", None)
        if model_turn and getattr(model_turn, "parts", None):
            for part in model_turn.parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and getattr(inline_data, "data", None):
                    await self._safe_send_bytes(browser_ws, inline_data.data)

        if getattr(server_content, "turn_complete", False):
            # Authoritative end-of-turn — flush any unflushed transcription as
            # final so the JS appends a bubble and call_tracker records the turn.
            # Gemini Live's Transcription proto often omits `finished`; this
            # path is what makes the transcript actually persist.
            if self._state.input_buffer.strip():
                await self._emit_transcript(
                    browser_ws,
                    role="caller",
                    text=self._state.input_buffer,
                    final=True,
                )
                self._state.input_buffer = ""
            if self._state.output_buffer.strip():
                await self._emit_transcript(
                    browser_ws,
                    role="agent",
                    text=self._state.output_buffer,
                    final=True,
                )
                self._state.output_buffer = ""
            await self._safe_send_text(
                browser_ws, json.dumps({"type": "turn_complete"})
            )

    async def _handle_tool_calls(
        self, function_calls: list, live_ws: Any, browser_ws: Any,
    ) -> None:
        from google.genai import types

        responses: list = []
        for fc in function_calls:
            name = getattr(fc, "name", "") or ""
            args = dict(getattr(fc, "args", {}) or {})
            fc_id = getattr(fc, "id", None)

            if name != "search_kb":
                logger.warning("Unknown tool call: %s", name)
                responses.append(
                    types.FunctionResponse(
                        id=fc_id, name=name, response={"error": "unknown tool"},
                    )
                )
                continue

            query = (args.get("query") or "").strip()
            if not query:
                responses.append(
                    types.FunctionResponse(
                        id=fc_id, name="search_kb",
                        response={"error": "empty query"},
                    )
                )
                continue

            await self._safe_send_text(
                browser_ws,
                json.dumps({"type": "tool_call", "name": "search_kb", "query": query}),
            )

            try:
                result = await self._tool_handler(
                    query, self._cfg.agent_id, self._cfg.tts_language,
                    self._state.history,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("search_kb handler failed: %s", exc)
                result = {"error": str(exc), "chunks": []}

            responses.append(
                types.FunctionResponse(
                    id=fc_id, name="search_kb", response=result,
                )
            )

        if responses:
            await live_ws.send_tool_response(function_responses=responses)

    # ── Transcript bookkeeping ───────────────────────────────

    async def _emit_transcript(
        self, browser_ws: Any, *, role: str, text: str, final: bool,
    ) -> None:
        text = text or ""
        payload = {
            "type": f"transcript.{'input' if role == 'caller' else 'output'}.{'final' if final else 'partial'}",
            "role": role,
            "text": text,
        }
        await self._safe_send_text(browser_ws, json.dumps(payload))

        if final and text.strip():
            self._state.history.append({"role": role, "text": text})
            if self._call_tracker is not None:
                tracker_role = "caller" if role == "caller" else "bot"
                try:
                    await self._call_tracker.record_turn(
                        self._call_uuid, tracker_role, text, confidence=0.0,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("record_turn failed: %s", exc)

    # ── Helpers ──────────────────────────────────────────────

    def _build_live_config(self, types_mod: Any) -> Any:
        """Construct the `LiveConnectConfig` for Gemini Live."""
        speech = types_mod.SpeechConfig(
            voice_config=types_mod.VoiceConfig(
                prebuilt_voice_config=types_mod.PrebuiltVoiceConfig(
                    voice_name=self._cfg.live_voice,
                ),
            ),
        )
        return types_mod.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=speech,
            system_instruction=types_mod.Content(
                parts=[types_mod.Part(text=assemble_system_instruction(self._cfg))],
            ),
            tools=[
                types_mod.Tool(
                    function_declarations=[_build_search_kb_tool()],
                ),
            ],
            input_audio_transcription=types_mod.AudioTranscriptionConfig(),
            output_audio_transcription=types_mod.AudioTranscriptionConfig(),
        )

    async def _safe_send_text(self, browser_ws: Any, text: str) -> None:
        try:
            await browser_ws.send_text(text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("send_text failed (ws closed?): %s", exc)
            self._closed = True

    async def _safe_send_bytes(self, browser_ws: Any, data: bytes) -> None:
        try:
            await browser_ws.send_bytes(data)
        except Exception as exc:  # noqa: BLE001
            logger.debug("send_bytes failed (ws closed?): %s", exc)
            self._closed = True

    async def _finalize(self) -> None:
        # Flush any half-recorded turn before marking the call done -- the WS
        # may have closed (user clicked End Call) before Gemini sent its final
        # `turn_complete`, which would otherwise drop the in-flight utterance.
        if self._call_tracker is not None:
            for role, text in (
                ("caller", self._state.input_buffer),
                ("agent", self._state.output_buffer),
            ):
                if not text.strip():
                    continue
                self._state.history.append({"role": role, "text": text})
                tracker_role = "caller" if role == "caller" else "bot"
                try:
                    await self._call_tracker.record_turn(
                        self._call_uuid, tracker_role, text, confidence=0.0,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("flush record_turn failed: %s", exc)
            self._state.input_buffer = ""
            self._state.output_buffer = ""

            try:
                await self._call_tracker.update_status(self._call_uuid, "answered")
            except Exception:  # noqa: BLE001
                pass
