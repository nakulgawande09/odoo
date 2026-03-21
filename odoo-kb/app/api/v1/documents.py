"""Document CRUD endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import verify_api_key
from app.core.exceptions import DocumentNotFoundError, IngestionError
from app.dependencies import get_backend, get_pipeline
from app.schemas.document import DocumentResponse, DocumentStatus, IngestRequest

router = APIRouter()


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    _api_key: str | None = Depends(verify_api_key),
    backend=Depends(get_backend),
) -> DocumentResponse:
    """Get document metadata by ID."""
    doc = await backend.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        content_type=doc.content_type,
        status=DocumentStatus(doc.status),
        chunks_count=doc.chunks_count,
        metadata=doc.metadata_ or {},
        tags=doc.tags or [],
        source_ref=doc.source_ref,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.put("/documents/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: str,
    request: IngestRequest,
    _api_key: str | None = Depends(verify_api_key),
    backend=Depends(get_backend),
    pipeline=Depends(get_pipeline),
) -> DocumentResponse:
    """Update a document: delete old chunks and re-ingest with the same ID."""
    doc = await backend.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        return await pipeline.update(document_id, request)
    except IngestionError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    _api_key: str | None = Depends(verify_api_key),
    backend=Depends(get_backend),
) -> dict:
    """Delete a document and all its chunks."""
    doc = await backend.get_document(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    await backend.delete_document(document_id)
    return {"status": "deleted", "document_id": document_id}


@router.get("/documents", response_model=list[DocumentResponse])
async def list_documents(
    limit: int = 20,
    offset: int = 0,
    _api_key: str | None = Depends(verify_api_key),
    backend=Depends(get_backend),
) -> list[DocumentResponse]:
    """List all documents."""
    docs = await backend.list_documents(limit=limit, offset=offset)
    return [
        DocumentResponse(
            id=doc.id,
            title=doc.title,
            content_type=doc.content_type,
            status=DocumentStatus(doc.status),
            chunks_count=doc.chunks_count,
            metadata=doc.metadata_ or {},
            tags=doc.tags or [],
            source_ref=doc.source_ref,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
        for doc in docs
    ]
