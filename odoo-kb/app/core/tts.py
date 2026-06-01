"""Text-to-Speech providers for VOIP audio responses.

Converts text answers into audio bytes. Currently supports Gemini TTS.
The TTS provider is optional -- when set to "none", VOIP endpoints
continue returning text-only responses.
"""
from __future__ import annotations

import logging
import struct
from typing import Any

from app.core.exceptions import TTSError

logger = logging.getLogger(__name__)

GEMINI_VOICES = {"Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda"}

LEGACY_VOICE_MAP: dict[str, str] = {
    "male": "Charon",
    "female": "Kore",
    "neural_male": "Fenrir",
    "neural_female": "Aoede",
}


class GeminiTTSProvider:
    """Generates speech audio using the Gemini API.

    Uses the Gemini model's audio generation capability to convert
    text into spoken audio. Returns raw audio bytes in WAV format.
    """

    def __init__(self, settings: Any) -> None:
        self._api_key = settings.gemini_api_key
        self._model = getattr(settings, "tts_model", "gemini-2.5-flash-preview-tts")
        self._voice = getattr(settings, "tts_voice", "Kore")
        self._language = getattr(settings, "tts_language", "en-US")
        self._sample_rate = getattr(settings, "tts_sample_rate", 24000)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google import genai
                self._client = genai.Client(api_key=self._api_key)
            except ImportError:
                raise TTSError(
                    "google-genai package required. Install with: "
                    "pip install google-genai"
                )
        return self._client

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
    ) -> bytes:
        """Convert text to audio bytes.

        Args:
            text: The text to convert to speech.
            voice: Override voice name (uses default from settings if None).
            language: Override language code (uses default from settings if None).

        Returns:
            Raw audio bytes in WAV format.

        Raises:
            TTSError: If synthesis fails.
        """
        import asyncio

        if not text.strip():
            raise TTSError("Cannot synthesize empty text")

        client = self._get_client()
        effective_voice = voice or self._voice
        # Resolve legacy generic voice names to actual Gemini voices
        if effective_voice in LEGACY_VOICE_MAP:
            effective_voice = LEGACY_VOICE_MAP[effective_voice]
        elif effective_voice not in GEMINI_VOICES:
            logger.warning(
                "Unknown voice %r, falling back to %s", effective_voice, self._voice,
            )
            effective_voice = self._voice

        try:
            from google.genai import types

            response = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=self._model,
                    contents=text,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=effective_voice,
                                )
                            )
                        ),
                    ),
                ),
            )

            audio_part = response.candidates[0].content.parts[0]
            audio_data = audio_part.inline_data.data

            return _wrap_wav(
                audio_data,
                sample_rate=self._sample_rate,
                channels=1,
                bits_per_sample=16,
            )
        except TTSError:
            raise
        except Exception as e:
            raise TTSError(f"Gemini TTS synthesis failed: {e}") from e

    @property
    def provider_name(self) -> str:
        return "gemini"


def _wrap_wav(
    pcm_data: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Wrap raw PCM audio data in a WAV header."""
    data_size = len(pcm_data)
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm_data


def create_tts_provider(settings: Any) -> GeminiTTSProvider | None:
    """Factory: returns a TTS provider if configured, None otherwise."""
    provider = getattr(settings, "tts_provider", "none")
    if provider == "none":
        return None
    if provider == "gemini":
        if not getattr(settings, "gemini_api_key", ""):
            logger.warning(
                "TTS provider set to 'gemini' but no gemini_api_key configured"
            )
            return None
        return GeminiTTSProvider(settings)
    raise ValueError(f"Unknown TTS provider: {provider}")
