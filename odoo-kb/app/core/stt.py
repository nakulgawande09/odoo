"""Speech-to-Text providers for voice call input.

Transcribes audio bytes (from browser MediaRecorder or provider webhooks)
into text that can be fed into the KB search pipeline.

Currently used only as a fallback for browsers without Web Speech API
(notably Safari on iOS). Production voice calls via Vonage/Twilio already
receive pre-transcribed speech from the provider.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.core.exceptions import STTError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transcription:
    """Result of an STT call."""

    text: str
    confidence: float
    language: str


class GeminiSTTProvider:
    """Transcribe audio to text using Gemini's audio understanding.

    Accepts raw audio bytes (WAV, WebM/Opus, MP3, etc.) and returns
    a plain text transcription. Uses the same Gemini API as the TTS
    and answer generation providers, so only one API key is required.
    """

    def __init__(self, settings: Any) -> None:
        self._api_key: str = settings.gemini_api_key
        self._model: str = getattr(settings, "stt_model", "gemini-2.5-flash")
        self._language: str = getattr(settings, "stt_language", "en-US")
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
                self._client = genai.Client(api_key=self._api_key)
            except ImportError as exc:
                raise STTError(
                    "google-genai package required. Install with: "
                    "pip install google-genai"
                ) from exc
        return self._client

    async def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str = "audio/webm",
        language: str | None = None,
    ) -> Transcription:
        """Convert audio bytes to text.

        Args:
            audio_bytes: Raw audio payload from the browser or provider.
            mime_type: MIME type of the audio (e.g. audio/webm, audio/wav).
            language: Override language hint (uses default from settings if None).

        Returns:
            Transcription with text, confidence, and detected language.

        Raises:
            STTError: If transcription fails or returns empty.
        """
        import asyncio

        if not audio_bytes:
            raise STTError("Cannot transcribe empty audio")

        client = self._get_client()
        effective_language = language or self._language

        prompt = (
            f"Transcribe the following audio clip verbatim in {effective_language}. "
            "Return ONLY the transcribed text without any commentary, "
            "labels, or punctuation beyond what was spoken."
        )

        try:
            from google.genai import types

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=self._model,
                    contents=[
                        prompt,
                        types.Part.from_bytes(
                            data=audio_bytes,
                            mime_type=mime_type,
                        ),
                    ],
                ),
            )
            text = (response.text or "").strip()
        except Exception as exc:
            raise STTError(f"Gemini STT transcription failed: {exc}") from exc

        if not text:
            raise STTError("Transcription returned empty text")

        return Transcription(
            text=text,
            confidence=0.9,  # Gemini doesn't expose per-transcript confidence
            language=effective_language,
        )

    @property
    def provider_name(self) -> str:
        return "gemini"


def create_stt_provider(settings: Any) -> GeminiSTTProvider | None:
    """Factory: returns an STT provider if configured, None otherwise."""
    provider = getattr(settings, "stt_provider", "none")
    if provider == "none":
        return None
    if provider == "gemini":
        if not getattr(settings, "gemini_api_key", ""):
            logger.warning(
                "STT provider set to 'gemini' but no gemini_api_key configured"
            )
            return None
        return GeminiSTTProvider(settings)
    raise ValueError(f"Unknown STT provider: {provider}")
