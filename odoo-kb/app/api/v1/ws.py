"""WebSocket endpoint for streaming search and RAG results."""
from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.rag_config import RAGConfig
from app.dependencies import get_answer_synthesizer, get_orchestrator
from app.schemas.search import SearchRequest

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/search")
async def ws_search(websocket: WebSocket):
    """WebSocket endpoint for interactive search with streaming RAG.

    Protocol:
      Client sends JSON: {"query": "...", "limit": 5, "rag": true}
      Server sends:
        {"type": "results", "data": {...}}       — search results
        {"type": "token", "data": "..."}         — RAG answer token
        {"type": "done", "data": {"sources": [...]}}  — stream complete
        {"type": "error", "data": "..."}         — error
    """
    await websocket.accept()
    logger.info("WebSocket client connected")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "data": "Invalid JSON"})
                continue

            query = msg.get("query", "").strip()
            if not query:
                await websocket.send_json({"type": "error", "data": "Empty query"})
                continue

            limit = min(msg.get("limit", 5), 20)
            use_rag = msg.get("rag", False)
            conversation_id = msg.get("conversation_id")
            tenant_id = msg.get("tenant_id")

            orchestrator = get_orchestrator()

            # 1. Run search
            start = time.monotonic()
            try:
                search_request = SearchRequest(
                    query=query,
                    limit=limit,
                    source="websocket",
                    conversation_id=conversation_id,
                    tenant_id=tenant_id,
                )
                response = await orchestrator.search(search_request)
                search_ms = (time.monotonic() - start) * 1000

                # Send search results
                await websocket.send_json({
                    "type": "results",
                    "data": {
                        "query": query,
                        "results": [
                            {
                                "document_id": r.document_id,
                                "title": r.title,
                                "snippet": r.snippet,
                                "relevance_score": r.relevance_score,
                                "tags": r.tags,
                            }
                            for r in response.results
                        ],
                        "total_count": response.total_count,
                        "search_time_ms": round(search_ms, 2),
                    },
                })

                # 2. Stream RAG answer if requested
                if use_rag and response.results:
                    synthesizer = get_answer_synthesizer()
                    if synthesizer and hasattr(synthesizer, "synthesize_stream"):
                        rag_config = RAGConfig(
                            enabled=True,
                            max_context_chunks=limit,
                            channel="websocket",
                        )
                        async for token in synthesizer.synthesize_stream(
                            query, response.results, rag_config,
                        ):
                            await websocket.send_json({"type": "token", "data": token})

                        sources = [
                            {"document_id": r.document_id, "title": r.title}
                            for r in response.results[:5]
                        ]
                        await websocket.send_json({
                            "type": "done",
                            "data": {"sources": sources},
                        })
                    elif use_rag:
                        await websocket.send_json({
                            "type": "error",
                            "data": "RAG not enabled on this server",
                        })

            except Exception as e:
                logger.error("WebSocket search error: %s", e, exc_info=True)
                await websocket.send_json({"type": "error", "data": str(e)})

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
