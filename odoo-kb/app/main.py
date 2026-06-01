"""FastAPI application factory with lifespan management."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.auth import configure_auth
from app.api.v1.router import router as v1_router
from app.backends.pgvector import PgVectorBackend
from app.backends.registry import create_backend
from app.core.cache import CachedEmbedder, EmbeddingCache, SearchCache
from app.core.conversation import ConversationTracker
from app.core.query_expansion import create_query_expander
from app.core.query_logger import QueryLogger
from app.core.query_preprocessor import create_preprocessor
from app.core.reranker import create_reranker
from app.core.search_service import SearchOrchestrator
from app.core.stt import create_stt_provider
from app.core.tts import create_tts_provider
from app.core.voice_agent_config import VoiceAgentStore
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

    # Create backend(s)
    backend = create_backend(settings)
    backends = [backend]

    if isinstance(backend, PgVectorBackend):
        await backend.initialize()

    # Initialize Qdrant if it's the selected backend
    if hasattr(backend, "initialize") and not isinstance(backend, PgVectorBackend):
        await backend.initialize()

    # Create embedder with caching layer
    raw_embedder = create_embedder(settings)
    embedding_cache = EmbeddingCache(
        max_size=getattr(settings, "embedding_cache_size", 5000),
        ttl_seconds=getattr(settings, "embedding_cache_ttl", 3600),
    )
    embedder = CachedEmbedder(raw_embedder, embedding_cache)

    # Create search cache
    search_cache = SearchCache(
        max_size=getattr(settings, "search_cache_size", 500),
        ttl_seconds=getattr(settings, "search_cache_ttl", 120),
    )

    # Create preprocessor
    taxonomy = load_filter_taxonomy(settings.filter_taxonomy_path)
    preprocessor = create_preprocessor(settings, taxonomy)

    # Create reranker (optional)
    reranker = create_reranker(settings)

    # Create conversation tracker
    conversation_tracker = ConversationTracker(
        ttl_seconds=settings.conversation_ttl_seconds
    )

    # Create query expander (with domain synonyms from config/synonyms.yaml)
    from config.settings import load_synonyms

    domain_synonyms = load_synonyms(settings.synonyms_path)
    if domain_synonyms:
        logger.info("Loaded %d domain synonym groups from %s",
                    len(domain_synonyms), settings.synonyms_path)
    query_expander = create_query_expander(settings, synonyms=domain_synonyms or None)

    # Create query logger
    session_factory = None
    if isinstance(backend, PgVectorBackend):
        session_factory = backend._session_factory
    query_logger = QueryLogger(session_factory=session_factory)

    # Create enrichment providers
    enrichment_providers = []
    if settings.enrichment_enabled:
        if settings.odoo_url:
            from app.enrichment.odoo_linker import OdooEntityLinker
            enrichment_providers.append(OdooEntityLinker(settings))
        if settings.tavily_api_key:
            from app.enrichment.tavily_web import TavilyEnrichmentProvider
            enrichment_providers.append(TavilyEnrichmentProvider(settings))

    # Voice agent config store
    voice_agent_store = VoiceAgentStore()

    # Call tracker (uses same session factory as query logger)
    call_tracker = None
    if session_factory:
        from app.core.call_tracker import CallTracker
        call_tracker = CallTracker(session_factory=session_factory)
        logger.info("Call tracker initialized")

    # CRM analyzer (optional — requires Gemini API key)
    from app.core.crm_analyzer import create_crm_analyzer
    crm_analyzer = create_crm_analyzer(settings)
    if crm_analyzer:
        logger.info("CRM analyzer initialized (model=%s)", settings.crm_analysis_model)

    # Vonage Messages client (optional — WhatsApp/SMS)
    from app.clients.vonage_messages import create_vonage_messages_client
    vonage_messages_client = create_vonage_messages_client(settings)
    if vonage_messages_client:
        logger.info("Vonage Messages client initialized (WhatsApp/SMS)")

    # TTS provider (optional)
    tts_provider = create_tts_provider(settings)
    if tts_provider:
        logger.info("TTS provider initialized: %s", tts_provider.provider_name)

    # STT provider (optional — fallback for browsers without Web Speech API)
    stt_provider = create_stt_provider(settings)
    if stt_provider:
        logger.info("STT provider initialized: %s", stt_provider.provider_name)

    # Answer generator (RAG — requires Gemini API key)
    from app.core.answer_generator import create_answer_generator
    answer_generator = create_answer_generator(settings)
    if answer_generator:
        logger.info("Answer generator initialized (model=%s)", settings.answer_model)

    # Cache stats aggregator
    def cache_stats():
        return {
            "embedding_cache": embedding_cache.stats,
            "search_cache": search_cache.stats,
        }

    # Create search orchestrator
    orchestrator = SearchOrchestrator(
        preprocessor=preprocessor,
        embedder=embedder,
        backends=backends,
        enrichment_providers=enrichment_providers,
        reranker=reranker,
        conversation_tracker=conversation_tracker,
        query_expander=query_expander,
        backend_weights=settings.backend_weights,
        query_logger=query_logger,
        search_cache=search_cache,
    )

    # Create ingestion pipeline (uses raw embedder, not cached)
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
        query_logger=query_logger,
        cache_stats_fn=cache_stats,
        voice_agent_store=voice_agent_store,
        tts_provider=tts_provider,
        call_tracker=call_tracker,
        session_factory=session_factory,
        crm_analyzer=crm_analyzer,
        vonage_messages_client=vonage_messages_client,
        answer_generator=answer_generator,
        stt_provider=stt_provider,
    )

    logger.info(
        "KB service started (backend=%s, embedder=%s, reranker=%s, "
        "expander=%s, tts=%s, cache=enabled, logging=enabled)",
        settings.search_backend,
        settings.embedding_provider,
        settings.reranker_provider,
        settings.query_expansion_provider,
        settings.tts_provider,
    )

    yield

    # Shutdown
    if hasattr(backend, "close"):
        await backend.close()
    logger.info("KB service stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Knowledge Base Service",
        description=(
            "Abstraction layer for knowledge base search. "
            "Provides a stable search interface with swappable backends, "
            "semantic reranking, conversation context, query expansion, "
            "caching, analytics, and multi-tenant isolation."
        ),
        version="0.3.0",
        lifespan=lifespan,
    )
    app.include_router(v1_router)
    return app


app = create_app()
