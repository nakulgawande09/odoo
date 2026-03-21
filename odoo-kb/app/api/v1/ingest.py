"""Document ingestion endpoints with async background processing."""
from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form

from app.api.auth import verify_api_key
from app.core.exceptions import IngestionError
from app.dependencies import get_backend, get_pipeline
from app.schemas.document import DocumentResponse, IngestRequest

logger = logging.getLogger(__name__)

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


# ─── Async Ingestion (9.3) ───────────────────────────────────

@router.post("/documents/async", status_code=202)
async def ingest_async(
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    _api_key: str | None = Depends(verify_api_key),
    pipeline=Depends(get_pipeline),
) -> dict:
    """Accept a document for background ingestion.

    Returns immediately with the document ID. Poll
    ``GET /v1/documents/{id}`` to check status (pending -> processing -> indexed).
    """
    from app.schemas.document import DocumentStatus
    from datetime import datetime, timezone

    doc_id = str(uuid.uuid4())
    title = request.title or "Untitled"

    # Create document record immediately with 'pending' status
    await pipeline.backend.create_document(
        doc_id=doc_id,
        title=title,
        content_type=request.content_type,
        metadata=request.metadata,
        tags=request.tags,
        source_ref=request.source_ref,
        tenant_id=request.tenant_id,
    )

    # Schedule background processing
    background_tasks.add_task(_process_async, pipeline, doc_id, request)

    return {
        "document_id": doc_id,
        "status": "pending",
        "message": "Document accepted for background processing. Poll GET /v1/documents/{id} for status.",
    }


async def _process_async(pipeline, doc_id: str, request: IngestRequest) -> None:
    """Background task that processes a document."""
    try:
        await pipeline.backend.update_document_status(doc_id, "processing")
        # Reuse the update method (same logic: chunk, embed, index)
        await pipeline.update(doc_id, request)
        logger.info("Async ingestion completed: %s", doc_id)
    except Exception as e:
        logger.error("Async ingestion failed for %s: %s", doc_id, e)
        try:
            await pipeline.backend.update_document_status(doc_id, "failed")
        except Exception:
            pass


# ─── Batch Re-indexing (9.2) ─────────────────────────────────

@router.post("/documents/reindex", status_code=202)
async def reindex_all(
    background_tasks: BackgroundTasks,
    _api_key: str | None = Depends(verify_api_key),
    backend=Depends(get_backend),
    pipeline=Depends(get_pipeline),
) -> dict:
    """Trigger a full re-index of all documents.

    Useful after changing the embedding model, chunk size, or schema.
    Runs in the background; poll ``GET /v1/documents`` to track progress.
    """
    docs = await backend.list_documents(limit=10000, offset=0)
    doc_ids = [doc.id for doc in docs]

    if not doc_ids:
        return {"message": "No documents to re-index", "count": 0}

    background_tasks.add_task(_reindex_batch, pipeline, backend, doc_ids)

    return {
        "message": f"Re-indexing {len(doc_ids)} documents in the background.",
        "count": len(doc_ids),
        "document_ids": doc_ids,
    }


async def _reindex_batch(pipeline, backend, doc_ids: list[str]) -> None:
    """Background task: re-index a batch of documents."""
    succeeded = 0
    failed = 0

    for doc_id in doc_ids:
        try:
            doc = await backend.get_document(doc_id)
            if doc is None:
                continue

            # Get existing content from chunks
            chunks = await backend.get_document_chunks(doc_id)
            if not chunks:
                continue

            content = "\n\n".join(c.content for c in chunks)

            request = IngestRequest(
                content=content,
                content_type=doc.content_type,
                title=doc.title,
                tags=doc.tags or [],
                metadata=doc.metadata_ or {},
                source_ref=doc.source_ref,
            )
            await pipeline.update(doc_id, request)
            succeeded += 1
        except Exception as e:
            failed += 1
            logger.error("Re-index failed for %s: %s", doc_id, e)

    logger.info(
        "Batch re-index complete: %d succeeded, %d failed out of %d",
        succeeded, failed, len(doc_ids),
    )
