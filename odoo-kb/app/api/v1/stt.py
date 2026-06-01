"""Speech-to-Text endpoint — fallback transcription for browsers
without Web Speech API support (e.g. Safari on iOS).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.auth import verify_api_key
from app.core.exceptions import STTError
from app.dependencies import get_stt_provider

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/stt/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    language: str = Form("en-US"),
    _api_key: str | None = Depends(verify_api_key),
) -> dict:
    """Transcribe an uploaded audio file to text.

    Accepts common browser recording formats (audio/webm, audio/ogg,
    audio/wav, audio/mp4). Returns a JSON payload with the transcript.
    """
    provider = get_stt_provider()
    if provider is None:
        raise HTTPException(
            status_code=503,
            detail="STT provider not configured (set KB_STT_PROVIDER=gemini)",
        )

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio upload")

    mime_type = audio.content_type or "audio/webm"

    try:
        result = await provider.transcribe(
            audio_bytes=audio_bytes,
            mime_type=mime_type,
            language=language,
        )
    except STTError as exc:
        logger.warning("STT failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return {
        "text": result.text,
        "confidence": result.confidence,
        "language": result.language,
    }
