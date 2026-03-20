"""Document ingestion endpoints."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form

from app.api.auth import verify_api_key
from app.core.exceptions import IngestionError
from app.dependencies import get_pipeline
from app.schemas.document import DocumentResponse, IngestRequest

router = APIRouter()


@router.post("/documents", response_model=DocumentResponse)
async def ingest_document(
    request: IngestRequest,
    _api_key: str | None = Depends(verify_api_key),
    pipeline=Depends(get_pipeline),
) -> DocumentResponse:
    """Ingest a single document into the knowledge base."""
    try:
        return await pipeline.ingest(request)
    except IngestionError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/documents/bulk", response_model=list[DocumentResponse])
async def ingest_bulk(
    requests: list[IngestRequest],
    _api_key: str | None = Depends(verify_api_key),
    pipeline=Depends(get_pipeline),
) -> list[DocumentResponse]:
    """Ingest multiple documents."""
    results = await asyncio.gather(
        *[pipeline.ingest(r) for r in requests],
        return_exceptions=True,
    )
    responses = []
    for r in results:
        if isinstance(r, Exception):
            raise HTTPException(status_code=422, detail=str(r))
        responses.append(r)
    return responses


@router.post("/documents/upload", response_model=DocumentResponse)
async def upload_file(
    file: UploadFile = File(...),
    title: str = Form(None),
    tags: str = Form(""),
    _api_key: str | None = Depends(verify_api_key),
    pipeline=Depends(get_pipeline),
) -> DocumentResponse:
    """Upload a file for ingestion (PDF, CSV, HTML, text, etc.)."""
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    request = IngestRequest(
        content=content.decode("utf-8", errors="replace"),
        content_type=content_type,
        title=title or file.filename or "Uploaded file",
        tags=tag_list,
    )
    try:
        return await pipeline.ingest(request)
    except IngestionError as e:
        raise HTTPException(status_code=422, detail=str(e))
