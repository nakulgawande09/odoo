"""Tests for TTS module."""
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import TTSError
from app.core.tts import GeminiTTSProvider, _wrap_wav, create_tts_provider


def _make_settings(**overrides):
    defaults = {
        "tts_provider": "gemini",
        "tts_model": "gemini-2.5-flash",
        "tts_voice": "Kore",
        "tts_language": "en-US",
        "tts_sample_rate": 24000,
        "gemini_api_key": "test-key",
    }
    defaults.update(overrides)
    return MagicMock(**defaults)


def test_create_tts_provider_none():
    settings = _make_settings(tts_provider="none")
    assert create_tts_provider(settings) is None


def test_create_tts_provider_gemini():
    provider = create_tts_provider(_make_settings())
    assert isinstance(provider, GeminiTTSProvider)
    assert provider.provider_name == "gemini"


def test_create_tts_provider_no_key_returns_none():
    provider = create_tts_provider(_make_settings(gemini_api_key=""))
    assert provider is None


def test_create_tts_provider_unknown_raises():
    with pytest.raises(ValueError, match="Unknown TTS provider"):
        create_tts_provider(_make_settings(tts_provider="unknown"))


def test_wrap_wav_header():
    pcm = b"\x00" * 100
    wav = _wrap_wav(pcm, sample_rate=24000)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav.endswith(pcm)
    assert len(wav) == 44 + 100  # 44-byte WAV header + data


def test_wrap_wav_empty_data():
    wav = _wrap_wav(b"", sample_rate=24000)
    assert wav[:4] == b"RIFF"
    assert len(wav) == 44


@pytest.mark.asyncio
async def test_synthesize_empty_text_raises():
    provider = GeminiTTSProvider(_make_settings())
    with pytest.raises(TTSError, match="empty text"):
        await provider.synthesize("")


@pytest.mark.asyncio
async def test_synthesize_whitespace_raises():
    provider = GeminiTTSProvider(_make_settings())
    with pytest.raises(TTSError, match="empty text"):
        await provider.synthesize("   ")
