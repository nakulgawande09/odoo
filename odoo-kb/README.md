# Odoo Knowledge Base Microservice

A FastAPI microservice that provides semantic search over Odoo content, with multi-channel access (API, livechat, WhatsApp, VOIP) and an Odoo addon for configuration.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Odoo (UI)                                                          │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Settings Page     │  │ Voice Agents     │  │ kb.mixin         │  │
│  │ (KB URL, API key, │  │ (greeting, TTS,  │  │ (push records    │  │
│  │  VOIP provider)   │  │  escalation,     │  │  to KB index)    │  │
│  │                   │  │  confidence)     │  │                  │  │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
└───────────┼──────────────────────┼──────────────────────┼───────────┘
            │                      │ PUT /v1/voip/agents   │ POST /v1/documents
            │                      │                       │
┌───────────▼──────────────────────▼───────────────────────▼───────────┐
│  KB Service (FastAPI)                                                │
│                                                                      │
│  /v1/search          – semantic search with intent detection         │
│  /v1/documents       – ingest/delete documents                       │
│  /v1/voip/query      – provider-agnostic VOIP webhook                │
│  /v1/voip/twilio     – Twilio TwiML webhook                          │
│  /v1/voip/vonage     – Vonage NCCO webhook                           │
│  /v1/voip/sip        – Asterisk AGI/ARI webhook                      │
│  /v1/voip/agents     – voice agent config CRUD (from Odoo UI)        │
│  /v1/health          – health check                                  │
│                                                                      │
│  Core: SearchOrchestrator → pgvector + Qdrant backends               │
│  Features: query expansion, reranking, caching, tenant isolation     │
└──────────────────────────────────────────────────────────────────────┘
```

## Quick Start

### 1. Start the KB Service

```bash
cd odoo-kb
pip install -r requirements.txt
uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8100
```

### 2. Install the Odoo Addon

Copy `odoo_addon/kb_connector` into your Odoo addons path, then install via Apps.

### 3. Configure in Odoo

1. **Settings → Knowledge Base** — Set the KB service URL and API key
2. **Knowledge Base → Voice Agents** — Create agents for each VOIP use case

## VOIP Integration

Voice agents are fully configurable through the Odoo UI — no backend changes needed.

### Setup Flow

1. **Create a Voice Agent** in Odoo (Knowledge Base → Voice Agents)
2. **Configure** greeting message, TTS voice, confidence threshold, escalation rules
3. **Copy the Webhook URL** (auto-generated with `?agent_id=N`)
4. **Paste** into your VOIP provider's dashboard:
   - **Twilio**: Use as the Action URL in a `<Gather>` verb
   - **Vonage**: Set as the Answer URL in your Vonage Application
   - **Asterisk**: Call via AGI/ARI with the webhook URL
5. **Click "Sync to KB Service"** to push the config
6. **Click "Test Query"** to verify end-to-end

### Voice Agent Settings

| Setting | Description |
|---------|------------|
| Greeting Message | First thing the caller hears |
| No Answer Message | When KB has no results |
| Low Confidence Message | When confidence is below threshold |
| Confidence Threshold | Score below which to escalate (default: 0.3) |
| Escalation Mode | Transfer / Voicemail / Callback / None |
| TTS Voice | Default, Male, Female, Neural variants |
| TTS Language | BCP-47 language code (default: en-US) |
| Max Answer Length | Truncate for TTS (default: 500 chars) |
| Working Hours | Restrict agent to business hours |

### Provider-Specific Endpoints

| Provider | Endpoint | Format |
|----------|----------|--------|
| Any | `POST /v1/voip/query?agent_id=N` | JSON in/out |
| Twilio | `POST /v1/voip/twilio?agent_id=N` | Form → TwiML JSON |
| Vonage | `POST /v1/voip/vonage?agent_id=N` | JSON → NCCO |
| Asterisk | `POST /v1/voip/sip?agent_id=N` | JSON → JSON |

## Odoo Addon: `kb_connector`

### Models

- **`kb.mixin`** — Abstract mixin; add to any model to make it KB-indexable
- **`kb.voice.agent`** — Voice agent configuration (VOIP behavior, per-agent)
- **`res.config.settings`** — KB service URL, API key, VOIP provider defaults

### Key Features

- **Push to KB** button on any model using `kb.mixin`
- **Voice Agent management** with form/tree views, stat buttons
- **Test Connection** — one-click health check
- **Test Query** — sends a sample query through the full pipeline
- **Sync Config** — pushes agent settings to KB service (no restart needed)
- **Webhook URL** — auto-generated, copy-to-clipboard widget

## KB Service Structure

```
app/
├── api/v1/
│   ├── router.py          # Route registry
│   ├── search.py          # /v1/search
│   ├── documents.py       # /v1/documents
│   ├── voip.py            # /v1/voip/* endpoints
│   └── health.py          # /v1/health
├── core/
│   ├── search_service.py  # SearchOrchestrator
│   ├── voice_agent_config.py  # VoiceAgentStore
│   ├── query_preprocessor.py  # Intent/filter extraction
│   ├── query_expansion.py     # Synonym expansion
│   ├── reranker.py            # Result reranking
│   ├── conversation.py        # Multi-turn context
│   ├── cache.py               # LRU + embedding cache
│   └── query_logger.py        # Analytics
├── backends/
│   ├── pgvector.py        # PostgreSQL + pgvector
│   └── qdrant.py          # Qdrant vector DB
├── ingestion/
│   ├── chunker.py         # Text chunking
│   └── extractors.py      # HTML/CSV/text extraction
├── dependencies.py        # DI container
└── main.py                # App factory + lifespan
```

## Testing

```bash
cd odoo-kb
python -m pytest tests/ -v
```

88 tests covering search, caching, VOIP, conversation tracking, query expansion, and more.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_API_KEY` | (none) | API key for authentication |
| `DATABASE_URL` | `postgresql://...` | PostgreSQL connection |
| `QDRANT_URL` | (none) | Qdrant server URL |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence transformer model |
| `TAVILY_API_KEY` | (none) | Tavily enrichment API key |
