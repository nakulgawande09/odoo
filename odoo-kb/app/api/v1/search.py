"""Search endpoints, including RAG-powered synthesis."""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.api.auth import verify_api_key
from app.core.rag_config import RAGConfig
from app.dependencies import get_answer_synthesizer, get_orchestrator
from app.schemas.search import (
    SearchRequest,
    SearchResponse,
    SearchWithSynthesisRequest,
    SearchWithSynthesisResponse,
)

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


@router.post("/search/synthesize", response_model=SearchWithSynthesisResponse)
async def search_with_synthesis(
    request: SearchWithSynthesisRequest,
    _api_key: str | None = Depends(verify_api_key),
    orchestrator=Depends(get_orchestrator),
) -> SearchWithSynthesisResponse:
    """Search + LLM synthesis in one call."""
    synthesizer = get_answer_synthesizer()
    if not synthesizer:
        raise HTTPException(
            status_code=503,
            detail="RAG answer synthesis is not enabled. Set KB_RAG_ENABLED=true.",
        )

    start = time.monotonic()

    search_request = SearchRequest(
        query=request.query,
        limit=request.limit,
        filters=request.filters,
        source=request.source,
        conversation_id=request.conversation_id,
        tenant_id=request.tenant_id,
    )
    search_response = await orchestrator.search(search_request)
    search_ms = (time.monotonic() - start) * 1000

    rag_config = RAGConfig(
        enabled=True,
        llm_model=request.rag.model or "gpt-4o-mini",
        system_prompt=request.rag.system_prompt or "",
        max_tokens=request.rag.max_tokens,
        temperature=request.rag.temperature,
        max_context_chunks=request.limit,
        channel="search",
    )

    synthesis = await synthesizer.synthesize(
        request.query, search_response.results, rag_config,
    )

    return SearchWithSynthesisResponse(
        query=request.query,
        answer=synthesis.answer,
        model_used=synthesis.model_used,
        sources=search_response.results,
        parsed_intent=search_response.parsed_intent,
        search_time_ms=round(search_ms, 2),
        synthesis_time_ms=synthesis.synthesis_time_ms,
        fallback_used=synthesis.fallback_used,
    )


@router.post("/search/synthesize/stream")
async def search_with_synthesis_stream(
    request: SearchWithSynthesisRequest,
    _api_key: str | None = Depends(verify_api_key),
    orchestrator=Depends(get_orchestrator),
) -> StreamingResponse:
    """Search + LLM synthesis with streaming SSE response."""
    synthesizer = get_answer_synthesizer()
    if not synthesizer:
        raise HTTPException(
            status_code=503,
            detail="RAG answer synthesis is not enabled. Set KB_RAG_ENABLED=true.",
        )

    search_request = SearchRequest(
        query=request.query,
        limit=request.limit,
        filters=request.filters,
        source=request.source,
        conversation_id=request.conversation_id,
        tenant_id=request.tenant_id,
    )
    search_response = await orchestrator.search(search_request)

    rag_config = RAGConfig(
        enabled=True,
        llm_model=request.rag.model or "gpt-4o-mini",
        system_prompt=request.rag.system_prompt or "",
        max_tokens=request.rag.max_tokens,
        temperature=request.rag.temperature,
        max_context_chunks=request.limit,
        channel="search",
    )

    sources_data = [s.model_dump(mode="json") for s in search_response.results[:5]]

    async def event_stream():
        async for token in synthesizer.synthesize_stream(
            request.query, search_response.results, rag_config,
        ):
            yield f"data: {json.dumps({'token': token})}\n\n"
        yield f"data: {json.dumps({'done': True, 'sources': sources_data})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
