"""FastAPI application factory with lifespan management."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import configure_auth
from app.api.v1.router import router as v1_router
from app.backends.pgvector import PgVectorBackend
from app.backends.registry import create_backend
from app.core.query_preprocessor import create_preprocessor
from app.core.search_service import SearchOrchestrator
from app.dependencies import get_settings, set_services
from app.ingestion.chunker import RecursiveChunker
from app.ingestion.embedder import create_embedder
from app.ingestion.pipeline import IngestionPipeline
from config.settings import load_filter_taxonomy

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup, clean up on shutdown."""
    settings = get_settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Configure auth
    configure_auth(settings.api_keys)

    # Create backend
    backend = create_backend(settings)
    if isinstance(backend, PgVectorBackend):
        await backend.initialize()

    # Create embedder
    embedder = create_embedder(settings)

    # Create preprocessor
    taxonomy = load_filter_taxonomy(settings.filter_taxonomy_path)
    preprocessor = create_preprocessor(settings, taxonomy)

    # Create search orchestrator
    orchestrator = SearchOrchestrator(
        preprocessor=preprocessor,
        embedder=embedder,
        backends=[backend],
    )

    # Create ingestion pipeline
    chunker = RecursiveChunker(
        max_chunk_size=settings.chunk_size,
        overlap=settings.chunk_overlap,
    )
    pipeline = IngestionPipeline(
        backend=backend,
        embedder=embedder,
        chunker=chunker,
    )

    # Wire up dependencies
    set_services(
        backend=backend,
        embedder=embedder,
        preprocessor=preprocessor,
        orchestrator=orchestrator,
        pipeline=pipeline,
    )

    logger.info(
        "KB service started (backend=%s, embedder=%s)",
        settings.search_backend,
        settings.embedding_provider,
    )

    yield

    # Shutdown
    if isinstance(backend, PgVectorBackend):
        await backend.close()
    logger.info("KB service stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Knowledge Base Service",
        description=(
            "Abstraction layer for knowledge base search. "
            "Provides a stable search interface with swappable backends."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(v1_router)
    return app


app = create_app()
