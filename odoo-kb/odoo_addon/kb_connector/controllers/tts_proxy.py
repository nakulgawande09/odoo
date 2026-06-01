"""TTS proxy controller.

Streams WAV audio from the KB service so the Odoo UI can play
TTS output directly in the browser.
"""
import logging

import requests as http_requests

from odoo import http
from odoo.http import request

logger = logging.getLogger(__name__)


class TTSProxyController(http.Controller):

    @http.route(
        "/kb/tts/test/<int:agent_id>",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def test_tts(self, agent_id: int):
        """Fetch TTS audio for the agent's greeting and return WAV bytes."""
        agent = request.env["kb.voice.agent"].browse(agent_id)
        if not agent.exists():
            return request.not_found()

        icp = request.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("kb.service.url", "http://localhost:8100")
        api_key = icp.get_param("kb.service.api_key", "")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        sample_text = (
            agent.greeting_message
            or "Hello, this is a test of the text to speech system."
        )

        payload = {
            "text": sample_text,
            "voice": agent.tts_voice if agent.tts_voice != "default" else None,
            "language": agent.tts_language or "en-US",
        }

        try:
            resp = http_requests.post(
                f"{base_url}/v1/tts/test",
                json=payload,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
        except http_requests.exceptions.ConnectionError:
            return request.make_response(
                "Cannot connect to KB service. Is it running?",
                headers=[("Content-Type", "text/plain")],
                status=502,
            )
        except http_requests.exceptions.HTTPError as exc:
            detail = exc.response.text if exc.response is not None else str(exc)
            return request.make_response(
                f"TTS failed: {detail}",
                headers=[("Content-Type", "text/plain")],
                status=exc.response.status_code if exc.response is not None else 500,
            )
        except http_requests.exceptions.Timeout:
            return request.make_response(
                "KB service timed out.",
                headers=[("Content-Type", "text/plain")],
                status=504,
            )

        return request.make_response(
            resp.content,
            headers=[
                ("Content-Type", "audio/wav"),
                ("Content-Disposition", "inline; filename=tts_test.wav"),
            ],
        )

    @http.route(
        "/kb/tts/speak/<int:agent_id>",
        type="http",
        auth="user",
        methods=["GET"],
    )
    def speak_text(self, agent_id: int, text: str = ""):
        """Synthesise arbitrary text via TTS and return WAV audio."""
        if not text.strip():
            return request.make_response(
                "No text provided.",
                headers=[("Content-Type", "text/plain")],
                status=400,
            )

        agent = request.env["kb.voice.agent"].browse(agent_id)
        if not agent.exists():
            return request.not_found()

        icp = request.env["ir.config_parameter"].sudo()
        base_url = icp.get_param("kb.service.url", "http://localhost:8100")
        api_key = icp.get_param("kb.service.api_key", "")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "text": text[:500],
            "voice": agent.tts_voice if agent.tts_voice != "default" else None,
            "language": agent.tts_language or "en-US",
        }

        try:
            resp = http_requests.post(
                f"{base_url}/v1/tts/test",
                json=payload,
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
        except http_requests.exceptions.RequestException as exc:
            return request.make_response(
                f"TTS failed: {exc}",
                headers=[("Content-Type", "text/plain")],
                status=502,
            )

        return request.make_response(
            resp.content,
            headers=[
                ("Content-Type", "audio/wav"),
                ("Content-Disposition", "inline; filename=tts_speak.wav"),
            ],
        )
