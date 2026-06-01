"""Test Console: full-page chat interface for testing Voice Agents.

Emulates a call session where the user types messages (as a caller)
and the system responds using the KB service with RAG answer generation.
"""
import logging
import urllib.parse
import uuid

import requests as http_requests
from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)

BUBBLE_CALLER = (
    "background-color:#dcf8c6;border-radius:12px;padding:8px 12px;"
    "margin:6px 0 6px 80px;text-align:right;"
)
BUBBLE_AGENT = (
    "background-color:#f1f0f0;border-radius:12px;padding:8px 12px;"
    "margin:6px 80px 6px 0;text-align:left;"
)
CONFIDENCE_CSS = "font-size:11px;color:#888;margin-top:2px;"
WRAPPER_OPEN = '<div style="overflow:hidden;min-height:60px;">'
WRAPPER_CLOSE = "</div>"


class KBTestConsole(models.TransientModel):
    _name = "kb.test.console"
    _description = "Voice Agent Test Console"
    # Default TransientModel vacuum (1 hour / 200 records) deletes the
    # console mid-conversation, breaking record.update() from the live_call
    # widget with "records with IDs N cannot be found". Extend lifetime to
    # 24h and bump the count cap so multi-turn calls aren't truncated.
    _transient_max_hours = 24.0
    _transient_max_count = 2000

    agent_id = fields.Many2one(
        "kb.voice.agent",
        string="Voice Agent",
        required=True,
    )
    mode = fields.Selection(
        [
            ("text", "Text Chat"),
            ("voice", "Voice Call"),
        ],
        string="Mode",
        default="text",
        required=True,
        help="Text Chat: type and send. Voice Call: speak to the agent and hear replies.",
    )
    record_as_real_call = fields.Boolean(
        string="Record as Real Call",
        default=True,
        help=(
            "When enabled, each turn goes through the full Vonage webhook "
            "pipeline — a CallRecord is created, the transcript is persisted, "
            "and CRM analysis runs when the call ends. Disable for a "
            "lightweight mode that hits /voip/query only (no DB writes)."
        ),
    )
    user_input = fields.Char("Your Message")
    conversation_html = fields.Html(
        "Conversation",
        readonly=True,
        sanitize=False,
    )
    last_response_text = fields.Text("Last Response", readonly=True)
    call_id = fields.Char("Call ID", readonly=True)
    session_call_uuid = fields.Char(
        "Session Call UUID",
        help="Persistent UUID for the live voice session; shared across turns.",
    )
    call_summary_html = fields.Html(
        "Call Summary",
        readonly=True,
        sanitize=False,
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Auto-select agent from context or first active agent
        agent_id = self.env.context.get("default_agent_id")
        if not agent_id:
            first_agent = self.env["kb.voice.agent"].search(
                [("active", "=", True)], limit=1
            )
            if first_agent:
                agent_id = first_agent.id
        if agent_id:
            res["agent_id"] = agent_id
            agent = self.env["kb.voice.agent"].browse(agent_id)
            if agent.exists() and agent.greeting_message:
                greeting = _format_agent_bubble(
                    agent.greeting_message, label="Agent"
                )
                res["conversation_html"] = Markup(WRAPPER_OPEN + greeting + WRAPPER_CLOSE)
        res["call_id"] = f"test-{uuid.uuid4().hex[:8]}"
        return res

    def action_send(self):
        """Send a text-mode message and render the response."""
        self.ensure_one()
        if not self.user_input or not self.user_input.strip():
            raise UserError(_("Please enter a message."))

        query_text = self.user_input.strip()

        # Start a real call session on the first turn if needed
        if self.record_as_real_call and not self.session_call_uuid:
            self._start_real_call_session()

        if self.record_as_real_call:
            data = self._send_turn_vonage(query_text)
        else:
            data = self._send_turn_query(query_text)

        answer = data.get("answer") or _("No answer received.")
        confidence = data.get("confidence")
        follow_up = data.get("follow_up")
        intent = data.get("intent")

        caller_bubble = _format_caller_bubble(query_text)
        agent_bubble = _format_agent_bubble(
            answer,
            confidence=confidence,
            follow_up=follow_up,
            intent=intent,
        )

        existing = str(self.conversation_html or "")
        if not existing:
            existing = WRAPPER_OPEN
        elif existing.endswith(WRAPPER_CLOSE):
            existing = existing[: -len(WRAPPER_CLOSE)]
        updated = Markup(existing + caller_bubble + agent_bubble + WRAPPER_CLOSE)

        self.write({
            "conversation_html": updated,
            "user_input": False,
            "last_response_text": answer,
        })

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_end_session(self):
        """End the current real-call session and fetch CRM analysis."""
        self.ensure_one()
        if not self.record_as_real_call or not self.session_call_uuid:
            # Nothing to finalize in lightweight mode
            self.write({"session_call_uuid": False})
            return {
                "type": "ir.actions.act_window",
                "res_model": self._name,
                "res_id": self.id,
                "view_mode": "form",
                "target": "current",
            }

        base_url, headers = self._kb_config()
        call_uuid = self.session_call_uuid

        try:
            http_requests.post(
                f"{base_url}/v1/voip/vonage/status",
                json={
                    "uuid": call_uuid,
                    "conversation_uuid": call_uuid,
                    "status": "completed",
                    "direction": "inbound",
                    "timestamp": "",
                },
                headers=headers,
                timeout=30,
            )
        except http_requests.RequestException as exc:
            logger.warning("Failed to send vonage status: %s", exc)

        # Poll briefly for the finalized record (CRM analysis is async)
        record = self._poll_call_record(call_uuid)
        if record:
            self.call_summary_html = Markup(_format_call_summary(record))

        self.session_call_uuid = False
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    # ── Internal helpers ──────────────────────────────────────

    def _kb_config(self) -> tuple[str, dict]:
        icp = self.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("kb.service.url", "http://localhost:8100").rstrip("/")
        api_key = icp.get_param("kb.service.api_key", "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return base_url, headers

    def _start_real_call_session(self) -> None:
        """POST /voip/vonage/answer to create a CallRecord for this session."""
        base_url, headers = self._kb_config()
        call_uuid = str(uuid.uuid4())
        try:
            http_requests.post(
                f"{base_url}/v1/voip/vonage/answer",
                params={"agent_id": self.agent_id.id},
                json={
                    "uuid": call_uuid,
                    "conversation_uuid": call_uuid,
                    "from": "+15550000000",
                    "to": "+18001234567",
                },
                headers=headers,
                timeout=30,
            )
        except http_requests.RequestException as exc:
            logger.warning("Failed to start call session: %s", exc)
        self.session_call_uuid = call_uuid

    def _send_turn_vonage(self, text: str) -> dict:
        """Send a turn via the Vonage webhook pipeline (full CallRecord)."""
        base_url, headers = self._kb_config()
        try:
            resp = http_requests.post(
                f"{base_url}/v1/voip/vonage/event",
                params={"agent_id": self.agent_id.id},
                json={
                    "uuid": self.session_call_uuid,
                    "conversation_uuid": self.session_call_uuid,
                    "speech": {
                        "results": [{"text": text, "confidence": 0.95}],
                    },
                },
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            ncco = resp.json()
        except http_requests.exceptions.ConnectionError:
            raise UserError(_("Cannot connect to KB service at %s") % base_url)
        except http_requests.exceptions.Timeout:
            raise UserError(_("KB service timed out."))
        except http_requests.exceptions.HTTPError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            raise UserError(_("KB service error: %s") % detail)

        answer_parts = [
            a.get("text", "")
            for a in ncco
            if a.get("action") == "talk" and a.get("text")
        ]
        return {
            "answer": " ".join(p for p in answer_parts if p).strip(),
            "confidence": None,
            "follow_up": None,
            "intent": None,
        }

    def _send_turn_query(self, text: str) -> dict:
        """Send a turn via /voip/query (lightweight, no CallRecord)."""
        base_url, headers = self._kb_config()
        try:
            resp = http_requests.post(
                f"{base_url}/v1/voip/query",
                json={
                    "query": text,
                    "provider": self.agent_id.voip_provider or "generic",
                    "call_id": self.call_id or f"test-{self.id}",
                    "max_results": self.agent_id.max_results or 3,
                    "agent_id": self.agent_id.id,
                },
                headers=headers,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
        except http_requests.exceptions.ConnectionError:
            raise UserError(_("Cannot connect to KB service at %s") % base_url)
        except http_requests.exceptions.Timeout:
            raise UserError(_("KB service timed out."))
        except http_requests.exceptions.HTTPError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            raise UserError(_("KB service error: %s") % detail)

    def _poll_call_record(self, call_uuid: str, attempts: int = 10) -> dict | None:
        """Poll /v1/calls/{uuid} until CRM analysis completes or we time out."""
        import time as _time

        base_url, headers = self._kb_config()
        for _ in range(attempts):
            try:
                resp = http_requests.get(
                    f"{base_url}/v1/calls/{call_uuid}",
                    headers=headers,
                    timeout=10,
                )
                if resp.ok:
                    data = resp.json()
                    if data.get("conversation_summary") or data.get("status") == "completed":
                        return data
            except http_requests.RequestException:
                pass
            _time.sleep(1.0)
        return None

    def action_play_last_tts(self):
        """Play TTS audio of the last agent response in a new tab."""
        self.ensure_one()
        if not self.last_response_text:
            raise UserError(_("No response to play yet."))
        text_encoded = urllib.parse.quote(self.last_response_text[:500])
        return {
            "type": "ir.actions.act_url",
            "url": f"/kb/tts/speak/{self.agent_id.id}?text={text_encoded}",
            "target": "new",
        }

    def action_reset(self):
        """Reset the conversation and start fresh."""
        self.ensure_one()
        # Finalize any open real-call session before clearing
        if self.record_as_real_call and self.session_call_uuid:
            try:
                base_url, headers = self._kb_config()
                http_requests.post(
                    f"{base_url}/v1/voip/vonage/status",
                    json={
                        "uuid": self.session_call_uuid,
                        "conversation_uuid": self.session_call_uuid,
                        "status": "completed",
                        "direction": "inbound",
                        "timestamp": "",
                    },
                    headers=headers,
                    timeout=10,
                )
            except http_requests.RequestException:
                pass

        greeting = ""
        if self.agent_id and self.agent_id.greeting_message:
            greeting = _format_agent_bubble(
                self.agent_id.greeting_message, label="Agent"
            )
        self.write({
            "conversation_html": Markup(WRAPPER_OPEN + greeting + WRAPPER_CLOSE),
            "user_input": False,
            "last_response_text": False,
            "call_id": f"test-{uuid.uuid4().hex[:8]}",
            "session_call_uuid": False,
            "call_summary_html": False,
        })
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.onchange("agent_id")
    def _onchange_agent_id(self):
        """Seed the greeting on a fresh conversation only.

        Re-firing this onchange mid-call would clobber the live transcript
        the live_call widget builds in conversation_html — so leave any
        non-empty conversation alone.
        """
        if not (self.agent_id and self.agent_id.greeting_message):
            return
        existing = (self.conversation_html or "").strip()
        empty_wrapper = (WRAPPER_OPEN + WRAPPER_CLOSE).strip()
        if existing and existing != empty_wrapper:
            return
        greeting = _format_agent_bubble(
            self.agent_id.greeting_message, label="Agent"
        )
        self.conversation_html = Markup(WRAPPER_OPEN + greeting + WRAPPER_CLOSE)
        self.last_response_text = False
        self.call_id = f"test-{uuid.uuid4().hex[:8]}"


# ─── HTML helpers (module-level, no self needed) ─────────────

def _format_caller_bubble(text: str) -> str:
    safe = _escape(text)
    return (
        f'<div style="{BUBBLE_CALLER}">'
        f"<strong>Caller:</strong> {safe}"
        f"</div>"
    )


def _format_agent_bubble(
    text: str,
    confidence: float | None = None,
    label: str = "Agent",
    follow_up: str | None = None,
    intent: str | None = None,
) -> str:
    safe = _escape(text)
    parts = [f'<div style="{BUBBLE_AGENT}"><strong>{label}:</strong> {safe}']
    if follow_up:
        parts.append(f'<br/><em style="color:#555;">{_escape(follow_up)}</em>')
    meta = []
    if confidence is not None:
        meta.append(f"Confidence: {confidence:.0%}")
    if intent:
        meta.append(f"Intent: {intent}")
    if meta:
        parts.append(f'<div style="{CONFIDENCE_CSS}">{" | ".join(meta)}</div>')
    parts.append("</div>")
    return "".join(parts)


def _escape(text: str) -> str:
    """Minimal HTML escaping for user-supplied text."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


SUMMARY_WRAPPER = (
    "border:1px solid #cde;border-radius:8px;padding:12px;"
    "margin:8px 0;background-color:#f5f9ff;"
)
SUMMARY_LABEL = "font-weight:600;color:#334;"
SUMMARY_BADGE = (
    "display:inline-block;padding:2px 8px;border-radius:10px;"
    "font-size:11px;margin-right:6px;background-color:#e3f0ff;color:#234;"
)


def _format_call_summary(record: dict) -> str:
    """Render a compact call summary panel from a /v1/calls/{uuid} payload."""
    duration = record.get("duration_seconds") or 0
    total_queries = record.get("total_queries") or 0
    avg_conf = record.get("avg_confidence")
    summary = record.get("conversation_summary") or ""
    transcript = record.get("transcript") or []

    # CRM analysis isn't returned on /calls/{uuid}; caller can fetch /crm/pending.
    # Show what we have.
    rows = [
        f'<div style="{SUMMARY_LABEL}">Call Summary</div>',
        f'<div>',
        f'<span style="{SUMMARY_BADGE}">Duration: {duration}s</span>',
        f'<span style="{SUMMARY_BADGE}">Turns: {len(transcript)}</span>',
        f'<span style="{SUMMARY_BADGE}">Queries: {total_queries}</span>',
    ]
    if avg_conf is not None:
        rows.append(
            f'<span style="{SUMMARY_BADGE}">Avg confidence: {avg_conf:.0%}</span>'
        )
    rows.append("</div>")
    if summary:
        rows.append(
            f'<div style="margin-top:8px;">'
            f'<strong>Summary:</strong> {_escape(summary)}</div>'
        )

    return f'<div style="{SUMMARY_WRAPPER}">' + "".join(rows) + "</div>"
