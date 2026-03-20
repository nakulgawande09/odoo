"""Search endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.auth import verify_api_key
from app.dependencies import get_orchestrator
from app.schemas.search import SearchRequest, SearchResponse

router = APIRouter()


@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    _api_key: str | None = Depends(verify_api_key),
    orchestrator=Depends(get_orchestrator),
) -> SearchResponse:
    """Search the knowledge base.

    Accepts a natural language query string. The system automatically
    extracts intent and filters, generates embeddings, searches all
    configured backends, and returns ranked results.
    """
    return await orchestrator.search(request)
