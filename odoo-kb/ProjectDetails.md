# Odoo Knowledge Base Service - Project Details

## Architecture Overview

Two-component system: **FastAPI microservice** (semantic search, voice, CRM) + **Odoo addons** (UI, document management, voice agent config).

```
Browser (OWL components)
    |
Odoo Server (controllers/session_proxy.py, tts_proxy.py)
    |
FastAPI Service :8100 (search, VOIP, TTS, STT, CRM)
    |
    +-- PostgreSQL + pgvector (documents, chunks, embeddings, calls)
    +-- Google Gemini API (embeddings, RAG answers, TTS, STT, CRM analysis)
    +-- Vonage API (phone calls, WhatsApp, SMS)
```

**Communication**: Odoo -> HTTP -> FastAPI -> Gemini / pgvector. Browser streams audio via Odoo proxy (not direct).

---

## Directory Structure

```
odoo-kb/
├── app/                          # FastAPI microservice
│   ├── main.py                   # App factory, lifespan, service init
│   ├── dependencies.py           # Dependency injection (singletons)
│   ├── api/
│   │   ├── auth.py               # API key verification middleware
│   │   └── v1/
│   │       ├── router.py         # Route aggregator
│   │       ├── search.py         # POST /v1/search
│   │       ├── ingest.py         # POST /v1/documents (ingestion)
│   │       ├── documents.py      # GET/DELETE /v1/documents
│   │       ├── vonage.py         # Vonage voice webhooks (answer/event/recording/status)
│   │       ├── voip.py           # Generic VOIP + Twilio/SIP + agent CRUD + TTS test
│   │       ├── calls.py          # Call record retrieval
│   │       ├── crm.py            # CRM pending/processed (pull-based sync)
│   │       ├── messaging.py      # WhatsApp/SMS inbound + log
│   │       ├── health.py         # Health check
│   │       ├── analytics.py      # Query stats + cache metrics
│   │       └── stt.py            # Speech-to-text endpoint
│   ├── core/
│   │   ├── search_service.py     # SearchOrchestrator (12-phase pipeline)
│   │   ├── query_preprocessor.py # Intent + filter extraction via LLM
│   │   ├── query_expansion.py    # Static/LLM synonym expansion
│   │   ├── reranker.py           # Result reranking (OpenAI/local cross-encoder)
│   │   ├── conversation.py       # Multi-turn context tracker (per conversation_id)
│   │   ├── cache.py              # LRU caches (embedding + search results)
│   │   ├── query_logger.py       # Query analytics logging to DB
│   │   ├── call_tracker.py       # Call lifecycle tracking + DB persistence
│   │   ├── crm_analyzer.py       # Gemini-powered transcript analysis for CRM
│   │   ├── notifications.py      # Triage notifications (WhatsApp/SMS via Vonage)
│   │   ├── tts.py                # GeminiTTSProvider (text -> WAV audio)
│   │   ├── stt.py                # GeminiSTTProvider (audio -> text, fallback)
│   │   ├── voice_agent_config.py # VoiceAgentConfig dataclass + in-memory store
│   │   ├── answer_generator.py   # RAG answer generation (Gemini LLM)
│   │   ├── exceptions.py         # Custom exception hierarchy
│   │   └── interfaces.py         # Protocol/interface definitions
│   ├── backends/
│   │   ├── pgvector.py           # PostgreSQL + pgvector (cosine similarity)
│   │   ├── qdrant.py             # Qdrant vector DB (alternative)
│   │   └── registry.py           # Backend factory pattern
│   ├── ingestion/
│   │   ├── pipeline.py           # Extract -> chunk -> embed -> index
│   │   ├── chunker.py            # Recursive text chunking (paragraph/sentence/word)
│   │   ├── embedder.py           # Embedding provider factory (Gemini/OpenAI/local)
│   │   └── extractors/
│   │       ├── base.py           # BaseExtractor abstract class
│   │       ├── pdf.py            # PDF extraction (pdfplumber)
│   │       ├── html.py           # HTML/richtext extraction (beautifulsoup4)
│   │       ├── csv.py            # CSV extraction
│   │       └── text.py           # Plain text extraction
│   ├── clients/
│   │   └── vonage_messages.py    # Vonage Messages API client (WhatsApp/SMS)
│   ├── models/
│   │   ├── document.py           # DocumentRecord, ChunkRecord, QueryLogRecord (SQLAlchemy)
│   │   ├── call_record.py        # CallRecord (voice calls with transcript + CRM)
│   │   └── message_log.py        # MessageLog (WhatsApp/SMS history)
│   ├── schemas/
│   │   ├── search.py             # SearchRequest/SearchResponse (Pydantic)
│   │   ├── document.py           # IngestRequest/DocumentResponse
│   │   └── common.py             # Shared types
│   ├── enrichment/
│   │   ├── odoo_linker.py        # Odoo entity linking
│   │   └── tavily_web.py         # Web search enrichment (Tavily)
│   └── tasks/
│       └── __init__.py           # Background task utilities
│
├── odoo_addon/
│   ├── kb_connector/             # Main Odoo addon (v19.0.2.0.0)
│   │   ├── __manifest__.py
│   │   ├── models/
│   │   │   ├── kb_mixin.py       # Abstract mixin: make any Odoo model KB-indexable
│   │   │   ├── kb_document.py    # Standalone document model (rich text + attachments)
│   │   │   ├── kb_voice_agent.py # Voice agent config (greeting, TTS, escalation)
│   │   │   ├── product_kb.py     # product.template extension for KB indexing
│   │   │   └── res_config_settings.py  # System settings (KB URL, API key)
│   │   ├── controllers/
│   │   │   ├── session_proxy.py  # BFF proxy: session start/turn/end/tts/stt
│   │   │   └── tts_proxy.py     # TTS audio proxy for testing
│   │   ├── static/src/
│   │   │   ├── js/
│   │   │   │   ├── live_call_field.js   # Bidirectional voice call OWL widget
│   │   │   │   ├── voice_input_field.js # Web Speech API text input widget
│   │   │   │   └── auto_tts_field.js    # Auto-play TTS on field change
│   │   │   └── xml/
│   │   │       ├── live_call_field.xml
│   │   │       ├── voice_input_field.xml
│   │   │       └── auto_tts_field.xml
│   │   ├── views/
│   │   │   ├── kb_document_views.xml
│   │   │   ├── kb_voice_agent_views.xml
│   │   │   └── res_config_settings_views.xml
│   │   ├── wizard/
│   │   │   ├── kb_test_console.py       # Test console wizard model
│   │   │   └── kb_test_console_views.xml
│   │   ├── tools/
│   │   │   └── kb_client.py     # HTTP client helpers (ingest, search, delete)
│   │   ├── data/
│   │   │   └── ir_config_parameter.xml  # Default settings
│   │   └── security/
│   │       └── ir.model.access.csv
│   │
│   └── kb_crm/                   # CRM addon (v19.0.1.0.0)
│       ├── __manifest__.py
│       ├── models/
│       │   └── crm_lead_kb.py    # crm.lead extension + cron sync from KB
│       ├── views/
│       │   └── crm_lead_views.xml
│       ├── data/
│       │   └── ir_cron.xml       # 5-minute sync cron job
│       └── security/
│           └── ir.model.access.csv
│
├── config/
│   ├── settings.py               # Pydantic settings (env_prefix="KB_")
│   └── filters.yaml              # Intent/filter taxonomy
│
├── tests/                        # pytest test suite
│   ├── conftest.py
│   ├── test_cache.py
│   ├── test_call_simulation.py
│   ├── test_conversation.py
│   ├── test_gemini_embedder.py
│   ├── test_ingestion.py
│   ├── test_preprocessing.py
│   ├── test_query_expansion.py
│   ├── test_query_logger.py
│   ├── test_reranker.py
│   ├── test_search.py
│   ├── test_search_orchestrator.py
│   ├── test_tenant_isolation.py
│   ├── test_tts.py
│   └── test_voip.py
│
├── requirements.txt
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── .env.example
└── README.md
```

---

## Data Flows

### 1. Document Ingestion

```
Odoo UI (kb.document form)
  -> action_push_to_kb()
  -> kb_client.kb_ingest() / kb_upload_file()
  -> POST /v1/documents
  -> IngestionPipeline.ingest()
     1. Extractor (PDF/HTML/CSV/text) -> raw text
     2. RecursiveChunker -> text chunks (512 tokens, 64 overlap)
     3. Embedder (Gemini/OpenAI) -> 768/1536-dim vectors
     4. pgvector backend -> INSERT chunks + embeddings
  -> DocumentRecord status = "indexed"
```

### 2. Voice Call (Turn-Based, Real Call Mode)

```
Browser: User clicks "Start Call"
  -> LiveCallField.onStartCall()
  -> RPC /kb/session/start {agent_id}
  -> session_proxy.session_start()
  -> POST /v1/voip/vonage/answer {uuid, from, to}
  -> call_tracker.start_call() (creates CallRecord)
  <- NCCO with greeting text
  <- greeting text -> /kb/session/tts -> WAV audio
  <- Browser plays audio

Browser: SpeechRecognition captures user speech
  -> LiveCallField._handleUserTurn(text)
  -> RPC /kb/session/turn {call_uuid, text, agent_id}
  -> session_proxy.session_turn()
  -> POST /v1/voip/vonage/event {uuid, speech.results}
  -> call_tracker.record_turn("caller", text)
  -> SearchOrchestrator.search(query)
     -> QueryPreprocessor (intent extraction)
     -> QueryExpander (synonyms)
     -> Embedder (query -> vector)
     -> pgvector cosine search
     -> Reranker (optional)
  -> AnswerGenerator.generate(query, results) [RAG via Gemini]
  -> call_tracker.record_turn("bot", answer)
  <- NCCO with answer text
  <- answer text -> /kb/session/tts -> WAV audio
  <- Browser plays audio, then listens again (loop)

Browser: User clicks "End Call"
  -> RPC /kb/session/end {call_uuid}
  -> POST /v1/voip/vonage/status {status: "completed"}
  -> _finalize_call()
     -> call_tracker.end_call() (calculates duration, metrics)
     -> crm_analyzer.analyze(transcript) [Gemini AI]
     -> Store CRM analysis (crm_status = "pending")
     -> Send notifications (WhatsApp/SMS)
  <- Browser polls /kb/session/record for summary
```

### 3. CRM Lead Sync (Pull-Based)

```
Odoo cron (every 5 minutes)
  -> crm.lead._cron_sync_from_kb()
  -> GET /v1/crm/pending
  <- List of calls with crm_status="pending"
  -> For each call:
     -> Create crm.lead with AI fields:
        - kb_customer_intent (purchase/support/inquiry/complaint/return)
        - kb_ai_priority (hot/warm/cold)
        - kb_sentiment (positive/neutral/negative)
        - kb_suggested_action
        - kb_transcript, kb_recording_url
     -> Post transcript as chatter note
     -> POST /v1/crm/processed/{call_id} (acknowledge)
```

### 4. WhatsApp/SMS Messaging

```
Inbound message -> Vonage webhook
  -> POST /v1/messaging/inbound
  -> SearchOrchestrator.search(message_text)
  -> AnswerGenerator.generate(query, results)
  -> Vonage Messages API -> reply to sender
  -> MessageLog saved to DB
```

---

## Key Models

### SQLAlchemy (FastAPI side)

| Table | Model | Key Fields |
|-------|-------|------------|
| `kb_documents` | `DocumentRecord` | id(UUID), title, content_type, status, chunks_count, metadata_, tags, source_ref, tenant_id |
| `kb_chunks` | `ChunkRecord` | id(UUID), document_id(FK), chunk_index, content, embedding(Vector(768)), metadata_ |
| `kb_call_records` | `CallRecord` | id(UUID), call_uuid, caller_number, agent_id, status, transcript(JSONB), crm_analysis(JSONB), crm_status |
| `kb_message_log` | `MessageLog` | id(UUID), channel, direction, sender, recipient, text, status |
| `kb_query_log` | `QueryLogRecord` | id(UUID), query, processed_query(JSONB), results_count, source, search_time_ms |

### Odoo (addon side)

| Model | Purpose | Key Fields |
|-------|---------|------------|
| `kb.document` | Standalone KB documents | name, content(Html), attachment_ids, document_type, tags, auto_sync, kb_status |
| `kb.voice.agent` | Voice agent configuration | name, voip_provider, greeting/no_answer/goodbye messages, tts_voice, tts_language, confidence_threshold, escalation_mode |
| `kb.test.console` | Test console wizard (transient) | agent_id, mode(text/voice), conversation_html, session_call_uuid |
| `kb.mixin` | Abstract mixin for KB-indexable models | _kb_content_fields, _kb_title_field, action_push_to_kb() |
| `crm.lead` (extended) | CRM lead with AI fields | kb_call_uuid, kb_customer_intent, kb_ai_priority, kb_sentiment, kb_suggested_action |

---

## API Endpoints

### Search & Documents
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/search` | Semantic search across KB |
| POST | `/v1/documents` | Ingest a document |
| POST | `/v1/documents/upload` | Upload file (multipart) |
| GET | `/v1/documents` | List documents (paginated) |
| GET | `/v1/documents/{id}` | Get document metadata |
| DELETE | `/v1/documents/{id}` | Delete document and chunks |

### Voice (Vonage)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/voip/vonage/answer` | Answer URL: greeting + record + input |
| POST | `/v1/voip/vonage/event` | Event URL: speech -> KB search -> RAG answer |
| POST | `/v1/voip/vonage/recording` | Recording callback |
| POST | `/v1/voip/vonage/status` | Call lifecycle events |

### Voice (Generic)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/voip/query` | Provider-agnostic KB query (JSON) |
| POST | `/v1/voip/query/audio` | Same but returns WAV audio |
| POST | `/v1/voip/twilio` | Twilio TwiML webhook |
| POST | `/v1/voip/sip` | Asterisk AGI/ARI webhook |
| PUT | `/v1/voip/agents/{id}` | Create/update agent config |
| GET | `/v1/voip/agents` | List agent configs |
| POST | `/v1/tts/test` | Synthesize text to WAV (no KB search) |

### Calls & CRM
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/calls` | List call records |
| GET | `/v1/calls/{id}` | Full call detail with transcript |
| GET | `/v1/calls/{id}/transcript` | Transcript only |
| GET | `/v1/crm/pending` | Calls awaiting CRM lead creation |
| POST | `/v1/crm/processed/{id}` | Mark call as processed |

### Messaging
| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/messaging/inbound` | Vonage inbound webhook |
| POST | `/v1/messaging/status` | Vonage status webhook |
| GET | `/v1/messaging/log` | Message history |

### Utility
| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/health` | Service health check |
| GET | `/v1/analytics` | Query stats + cache metrics |
| POST | `/v1/stt/transcribe` | Speech-to-text (multipart audio) |

---

## OWL Components (JavaScript)

### LiveCallField (`live_call_field.js`)
- **Widget name**: `live_call`
- **Supported types**: `char`
- **Purpose**: Full bidirectional voice call simulation in Test Console
- **States**: idle -> starting -> listening -> thinking -> speaking -> ended / error
- **Flow**: Start Call -> greeting TTS -> SpeechRecognition loop -> turn RPC -> agent TTS -> listen again
- **Fallback**: MediaRecorder + server STT when browser SpeechRecognition unavailable (Safari)
- **Renders**: Conversation bubbles (caller/agent/system) + call summary panel

### VoiceInputField (`voice_input_field.js`)
- **Widget name**: `voice_input`
- **Supported types**: `char`
- **Purpose**: Single speech-to-text input field with microphone button
- **Uses**: Web Speech API, updates field value with transcribed text

### AutoTTSField (`auto_tts_field.js`)
- **Widget name**: `auto_tts`
- **Supported types**: `text`
- **Purpose**: Invisible field that auto-plays TTS when value changes
- **Triggers**: `/kb/tts/speak/{agent_id}?text=...`

---

## Odoo Controllers (Session Proxy)

### SessionProxyController (`session_proxy.py`)

Backend-for-frontend proxy between browser and KB service. Hides KB service URL/API key from the browser.

| Route | Type | Purpose |
|-------|------|---------|
| `POST /kb/session/start` | jsonrpc | Initialize call session, get greeting |
| `POST /kb/session/turn` | jsonrpc | Send user text, get agent answer |
| `POST /kb/session/end` | jsonrpc | End call, trigger CRM analysis |
| `POST /kb/session/record` | jsonrpc | Poll for final call record + CRM analysis |
| `POST /kb/session/tts` | http | Proxy TTS synthesis (returns WAV) |
| `POST /kb/session/stt` | http | Proxy STT transcription (multipart audio) |

### TTSProxyController (`tts_proxy.py`)

| Route | Type | Purpose |
|-------|------|---------|
| `GET /kb/tts/test/{agent_id}` | http | Test TTS with agent's greeting |
| `GET /kb/tts/speak/{agent_id}` | http | Synthesize arbitrary text |

---

## Search Pipeline (SearchOrchestrator)

12-phase search pipeline in `app/core/search_service.py`:

1. **Cache check** - Return cached results if available
2. **Context expansion** - Resolve pronouns from conversation history
3. **Preprocessing** - Extract intent + filters via LLM
4. **Query expansion** - Add synonyms/related terms
5. **Embedding generation** - Convert query to vector (Gemini/OpenAI)
6. **Parallel backend search** - Fan out to all configured backends
7. **Result merging** - Weighted scoring across backends
8. **Deduplication** - Remove duplicates, keep highest score
9. **Reranking** - Cross-encoder reranking (optional)
10. **Final ranking** - Sort by score, apply limit
11. **Enrichment** - Entity linking, web search (optional)
12. **Logging** - Fire-and-forget analytics

---

## Configuration Reference (.env)

All settings use `KB_` prefix. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_DATABASE_URL` | `postgresql+asyncpg://kb:kb@localhost:5432/kb` | PostgreSQL connection |
| `KB_GEMINI_API_KEY` | (required) | Google Gemini API key |
| `KB_EMBEDDING_PROVIDER` | `openai` | `openai` / `gemini` / `local` |
| `KB_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model name |
| `KB_EMBEDDING_DIMENSIONS` | `1536` | Vector dimensions |
| `KB_SEARCH_BACKEND` | `pgvector` | `pgvector` / `qdrant` |
| `KB_TTS_PROVIDER` | `none` | `none` / `gemini` |
| `KB_TTS_VOICE` | `Kore` | Gemini voice name |
| `KB_TTS_MODEL` | `gemini-2.5-flash-preview-tts` | TTS model |
| `KB_STT_PROVIDER` | `none` | `none` / `gemini` |
| `KB_ANSWER_MODEL` | `gemini-2.5-flash` | RAG answer generation model |
| `KB_CHUNK_SIZE` | `512` | Max tokens per chunk |
| `KB_CHUNK_OVERLAP` | `64` | Overlap between chunks |
| `KB_PUBLIC_URL` | (empty) | Public URL for webhooks (ngrok) |
| `KB_VONAGE_API_KEY` | (empty) | Vonage API key |
| `KB_CRM_AUTO_CREATE` | `true` | Auto-analyze transcripts for CRM |
| `KB_CRM_MIN_DURATION` | `30` | Min call seconds for CRM analysis |

### Odoo System Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `kb.service.url` | `http://localhost:8100` | KB FastAPI service URL |
| `kb.service.api_key` | (empty) | API key for KB service auth |

---

## Available Gemini TTS Voices

| Voice | Gender | Character |
|-------|--------|-----------|
| Puck | Male | Energetic |
| Charon | Male | Calm |
| Kore | Female | Firm (default) |
| Fenrir | Male | Deep |
| Aoede | Female | Warm |
| Leda | Female | Bright |

---

## Dependencies

### Python (from requirements.txt)
- **Web**: fastapi, uvicorn, httpx
- **Data**: pydantic, pydantic-settings, sqlalchemy[asyncio], asyncpg, pgvector
- **AI**: google-genai (Gemini), openai (optional)
- **Documents**: pdfplumber, beautifulsoup4
- **Testing**: pytest, pytest-asyncio
- **Config**: pyyaml

### External Services
- PostgreSQL 15+ with pgvector extension
- Google Gemini API
- Vonage API (optional, for real phone calls)
- Odoo 19.0
