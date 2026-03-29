# Odoo Knowledge Base Service

A FastAPI microservice that provides **semantic search** over indexed documents, with multi-channel access via **Voice (Vonage SIP)**, **WhatsApp**, **SMS**, and **REST API**. Includes an Odoo addon for document management, voice agent configuration, and AI-powered CRM lead creation from call transcripts.

## Architecture

```
                          ┌──────────────────────────────────────────────────────┐
                          │  Odoo 19                                              │
                          │  ┌──────────────┐  ┌──────────────┐  ┌────────────┐  │
                          │  │ kb.document   │  │ kb.voice.agent│  │ crm.lead   │  │
                          │  │ (manage docs, │  │ (greeting,    │  │ (AI triage │  │
                          │  │  push to KB)  │  │  TTS, escal.) │  │  via cron) │  │
                          │  └──────┬───────┘  └──────┬───────┘  └─────┬──────┘  │
                          └─────────┼─────────────────┼────────────────┼──────────┘
                                    │ POST /v1/docs   │ PUT /v1/voip   │ GET /v1/crm/pending
                                    │                 │                │
┌─────────────┐  ┌──────────────────▼─────────────────▼────────────────┼──────────┐
│  Phone Call  │──│  KB Service (FastAPI :8100)                                    │
│  (Vonage)    │  │                                                                │
├─────────────┤  │  /v1/search             – semantic search + intent detection    │
│  WhatsApp   │──│  /v1/documents          – ingest, list, delete documents        │
│  (Vonage)    │  │  /v1/voip/vonage/*      – answer, event, recording, status     │
├─────────────┤  │  /v1/messaging/*         – WhatsApp/SMS inbound + status        │
│  SMS        │──│  /v1/calls              – call records + transcripts            │
│  (Vonage)    │  │  /v1/analytics          – query stats + cache metrics          │
├─────────────┤  │                                                                │
│  REST API   │──│  Core: SearchOrchestrator → pgvector backend                   │
│  (any client)│  │  AI:   Gemini (embeddings, TTS, CRM analysis)                  │
└─────────────┘  └────────────────────────────────────────────────────────────────┘
                                    │
                          ┌─────────▼──────────┐
                          │  PostgreSQL + pgvector │
                          │  (embeddings, docs,    │
                          │   calls, messages)     │
                          └────────────────────────┘
```

## Table of Contents

- [Quick Start](#quick-start)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [Option A: Docker (Recommended)](#option-a-docker-recommended)
  - [Option B: Manual Setup](#option-b-manual-setup)
- [Configuration](#configuration)
- [Populating the Knowledge Base](#populating-the-knowledge-base)
- [Vonage Voice Integration](#vonage-voice-integration)
- [WhatsApp & SMS Integration](#whatsapp--sms-integration)
- [CRM Integration](#crm-integration)
- [Odoo Addons](#odoo-addons)
- [API Reference](#api-reference)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

```bash
# 1. Clone and enter the project
cd odoo-kb

# 2. Start PostgreSQL with pgvector
docker compose up kb-db -d

# 3. Create a virtual environment and install
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Copy and configure environment
cp .env.example .env
# Edit .env — at minimum set KB_GEMINI_API_KEY

# 5. Start the service
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload

# 6. Verify
curl http://localhost:8100/v1/health
# → {"status": "healthy", ...}

# 7. Seed some test documents
make demo-seed

# 8. Search
curl -X POST http://localhost:8100/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the return policy?"}'
```

---

## Prerequisites

| Requirement | Version | Purpose |
|-------------|---------|---------|
| Python | 3.10+ | Runtime |
| PostgreSQL | 15+ with pgvector | Vector storage and search |
| Docker & Docker Compose | Latest | Database (and optional full-stack deployment) |
| Gemini API Key | — | Embeddings, TTS, and CRM transcript analysis |
| Vonage Account | — | Voice calls, WhatsApp, SMS *(optional)* |
| Odoo | 19.0 | UI for document management, voice agents, CRM *(optional)* |
| ngrok | — | Public URL for Vonage webhooks during development *(optional)* |

---

## Installation

### Option A: Docker (Recommended)

Starts both PostgreSQL (pgvector) and the KB service:

```bash
# Set your API key
export OPENAI_API_KEY=sk-...  # or configure Gemini in docker-compose

# Start everything
docker compose up -d

# Seed demo data
make demo-seed
```

Services:
- **KB Service**: http://localhost:8100
- **API Docs** (Swagger): http://localhost:8100/docs
- **PostgreSQL**: localhost:5433 (user: `kb`, password: `kb`, database: `kb`)

### Option B: Manual Setup

#### 1. Database

Start PostgreSQL with pgvector. The docker-compose includes one, or use your own:

```bash
docker compose up kb-db -d
```

This starts pgvector on **port 5433** with:
- Database: `kb`
- User: `kb`
- Password: `kb`

#### 2. Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Optional: JWT auth for Vonage Messages API
pip install PyJWT
```

#### 3. Environment Configuration

```bash
cp .env.example .env
```

Edit `.env` with your settings (see [Configuration](#configuration) below).

#### 4. Start the Service

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

The service auto-creates all database tables on startup (documents, chunks, call records, message logs).

#### 5. Install Odoo Addons (Optional)

Copy both addons into your Odoo addons path:

```bash
cp -r odoo_addon/kb_connector /path/to/odoo/addons/
cp -r odoo_addon/kb_crm /path/to/odoo/addons/
```

Then in Odoo: **Apps → Update Apps List → Search "Knowledge Base" → Install**.

---

## Configuration

All environment variables use the `KB_` prefix. Copy `.env.example` to `.env` and configure:

### Required

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_DATABASE_URL` | `postgresql+asyncpg://kb:kb@localhost:5433/kb` | PostgreSQL connection string |
| `KB_GEMINI_API_KEY` | — | Google Gemini API key (for embeddings, TTS, CRM analysis) |

### Embeddings

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_EMBEDDING_PROVIDER` | `gemini` | `gemini`, `openai`, or `local` |
| `KB_EMBEDDING_MODEL` | `models/gemini-embedding-001` | Embedding model name |
| `KB_EMBEDDING_DIMENSIONS` | `768` | Vector dimensions (768 for Gemini, 1536 for OpenAI) |
| `KB_OPENAI_API_KEY` | — | Required only if using OpenAI provider |

### Text-to-Speech

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_TTS_PROVIDER` | `gemini` | `none` or `gemini` |
| `KB_TTS_MODEL` | `gemini-2.5-flash` | TTS model |
| `KB_TTS_VOICE` | `Kore` | Voice name |
| `KB_TTS_LANGUAGE` | `en-US` | BCP-47 language code |

### Vonage (Voice + Messaging)

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_PUBLIC_URL` | — | Public URL for webhooks (e.g. ngrok URL) |
| `KB_VONAGE_API_KEY` | — | Vonage API key |
| `KB_VONAGE_API_SECRET` | — | Vonage API secret |
| `KB_VONAGE_APPLICATION_ID` | — | Vonage Application ID (for JWT auth) |
| `KB_VONAGE_PRIVATE_KEY_PATH` | — | Path to Vonage private key file |
| `KB_VONAGE_WHATSAPP_NUMBER` | — | WhatsApp-enabled Vonage number |
| `KB_VONAGE_SMS_FROM` | — | SMS sender number |

### CRM Integration (Pull-Based)

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_CRM_AUTO_CREATE` | `true` | Auto-analyze call transcripts for CRM |
| `KB_CRM_MIN_DURATION` | `30` | Minimum call duration (seconds) to trigger analysis |
| `KB_CRM_ANALYSIS_MODEL` | `gemini-2.5-flash` | Gemini model for transcript analysis |

> **Note:** No Odoo credentials are needed in the KB service. The Odoo `kb_crm` addon pulls pending leads via cron using the already-configured KB service URL.

### Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_NOTIFY_TEAM_NUMBERS` | `[]` | JSON array of team WhatsApp numbers |
| `KB_NOTIFY_ON_PRIORITY` | `hot,warm` | Priorities that trigger team notifications |
| `KB_CALLER_FOLLOWUP_ENABLED` | `true` | Send follow-up to caller after call |
| `KB_CALLER_FOLLOWUP_CHANNEL` | `whatsapp` | `whatsapp` or `sms` |

### Search Tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_CHUNK_SIZE` | `512` | Max tokens per chunk |
| `KB_CHUNK_OVERLAP` | `64` | Overlap between chunks |
| `KB_RERANKER_PROVIDER` | `none` | `none`, `openai`, or `local` |
| `KB_QUERY_EXPANSION_PROVIDER` | `static` | `static` (synonyms) or `llm` |
| `KB_EMBEDDING_CACHE_SIZE` | `5000` | Max cached embeddings |
| `KB_SEARCH_CACHE_SIZE` | `500` | Max cached search results |
| `KB_CONVERSATION_TTL_SECONDS` | `1800` | Multi-turn context window (30 min) |

---

## Populating the Knowledge Base

The voice agent is only useful if there are documents to search. There are three ways to add content:

### Via API

```bash
# Single document
curl -X POST http://localhost:8100/v1/documents \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Our return policy allows returns within 30 days...",
    "title": "Return Policy",
    "tags": ["policy", "returns"]
  }'

# File upload (PDF, CSV, HTML, text)
curl -X POST http://localhost:8100/v1/documents/upload \
  -F "file=@knowledge-base.pdf" \
  -F "title=Product Manual" \
  -F "tags=manual,product"

# Bulk ingestion
curl -X POST http://localhost:8100/v1/documents/bulk \
  -H "Content-Type: application/json" \
  -d '{"documents": [{"content": "...", "title": "Doc 1"}, ...]}'
```

### Via Odoo UI

1. Go to **Knowledge Base → Documents**
2. Click **New** → enter title, content (rich text), attach files
3. Click **Push to KB** — the document is chunked, embedded, and indexed
4. Status changes to "Indexed" with chunk count

Documents with **Auto-sync** enabled are automatically re-pushed on save.

### Via Odoo Record Indexing

Any Odoo model can be made KB-searchable by inheriting `kb.mixin`:

```python
class ProductTemplateKB(models.Model):
    _name = "product.template"
    _inherit = ["product.template", "kb.mixin"]
    _kb_content_fields = ["name", "description", "description_sale"]
    _kb_title_field = "name"
    _kb_tags = ["product"]
```

Then click **Push to KB** on any product record to index it.

### Verify Search Quality

```bash
curl -X POST http://localhost:8100/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the return policy?"}'
```

Check that results have `relevance_score > 0.5` for relevant queries.

---

## Vonage Voice Integration

### How It Works

```
Phone Call → Vonage → Answer URL → Greeting + Record + Listen
                   → Event URL  → Speech → KB Search → Answer → Listen (loop)
                   → Status URL → Track lifecycle (started → answered → completed)
                   → Recording URL → Save recording metadata
                   → Call Completed → AI Analysis → CRM Lead + Notifications
```

### Setup Steps

#### 1. Expose the service publicly

```bash
# Install ngrok (https://ngrok.com)
ngrok http 8100
# Note the HTTPS URL, e.g. https://abc123.ngrok-free.app
```

Add to `.env`:
```
KB_PUBLIC_URL=https://abc123.ngrok-free.app
```

#### 2. Create a Vonage Application

1. Go to [Vonage Dashboard](https://dashboard.nexmo.com) → Applications → Create
2. Enable **Voice** capability
3. Set webhook URLs:
   - **Answer URL**: `https://<your-ngrok>/v1/voip/vonage/answer?agent_id=1`
   - **Event URL**: `https://<your-ngrok>/v1/voip/vonage/event?agent_id=1`
4. Download the `private.key` file and save it to the project root
5. Note the **Application ID**

#### 3. Buy and link a phone number

1. In Vonage Dashboard → Numbers → Buy Numbers
2. Link the number to your Application

#### 4. Configure credentials

Add to `.env`:
```
KB_VONAGE_API_KEY=your_api_key
KB_VONAGE_API_SECRET=your_api_secret
KB_VONAGE_APPLICATION_ID=your_application_id
KB_VONAGE_PRIVATE_KEY_PATH=./private.key
```

#### 5. Restart the service

```bash
# Kill existing process
lsof -ti:8100 | xargs kill -9

# Start fresh
uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

#### 6. Test

Call your Vonage number from any phone. You should:
1. Hear the greeting ("Hello! How can I help you today?")
2. Speak a question → hear a KB-sourced answer
3. Be asked "Is there anything else I can help with?"
4. See the call record at `GET /v1/calls`

### Voice Agent Configuration

Voice agents are configurable via the Odoo UI or the API:

| Setting | Default | Description |
|---------|---------|-------------|
| Greeting Message | "Hello! How can I help you today?" | First thing the caller hears |
| No Answer Message | "I'm sorry, I couldn't find..." | When KB returns no results |
| Low Confidence Message | "I'm not fully confident..." | Below confidence threshold |
| Confidence Threshold | 0.3 | Score below which to escalate |
| Escalation Number | — | Phone number to transfer to |
| Max Answer Length | 500 | Truncate answers for TTS |
| Follow-up Enabled | true | Suggest related questions |
| TTS Language | en-US | Speech language |

### Vonage Webhook Endpoints

| Endpoint | Vonage Config | Purpose |
|----------|--------------|---------|
| `POST /v1/voip/vonage/answer` | Answer URL | Returns greeting NCCO + record action |
| `POST /v1/voip/vonage/event` | Event URL | Receives speech, searches KB, returns answer |
| `POST /v1/voip/vonage/recording` | (auto) | Receives recording URL after call |
| `POST /v1/voip/vonage/status` | (auto) | Tracks call lifecycle events |

---

## WhatsApp & SMS Integration

### How It Works

Customers send a WhatsApp message or SMS to your Vonage number. The service searches the KB and replies via the same channel. Conversations are tracked with per-sender context.

### Setup Steps

#### 1. Enable Messages API on your Vonage Application

1. In Vonage Dashboard → Your Application → Edit
2. Enable **Messages** capability
3. Set webhook URLs:
   - **Inbound URL**: `https://<your-ngrok>/v1/messaging/inbound`
   - **Status URL**: `https://<your-ngrok>/v1/messaging/status`

#### 2. Register for WhatsApp (Sandbox)

For development, use the [Vonage WhatsApp Sandbox](https://dashboard.nexmo.com/messages/sandbox).

For production, apply for a WhatsApp Business Account through Vonage.

#### 3. Configure

Add to `.env`:
```
KB_VONAGE_WHATSAPP_NUMBER=14155550123
KB_VONAGE_SMS_FROM=14155550123
```

#### 4. Test

Send a WhatsApp message or SMS to your Vonage number. The service will search the KB and reply with the answer.

Check message history:
```bash
curl http://localhost:8100/v1/messaging/log?limit=10
```

### Messaging Endpoints

| Endpoint | Purpose |
|----------|---------|
| `POST /v1/messaging/inbound` | Receives WhatsApp/SMS, searches KB, replies |
| `POST /v1/messaging/status` | Delivery status updates (delivered, read, failed) |
| `GET /v1/messaging/log` | Message history with channel/sender filters |

---

## CRM Integration

### How It Works (Pull-Based)

The CRM integration uses a **pull-based** approach — the KB service stores analysis results locally, and the Odoo addon pulls them via cron. **No Odoo credentials are needed on the KB service side.**

```
Call ends → KB analyzes transcript (Gemini AI) → stores analysis locally (crm_status="pending")
                                                        ↓
Odoo cron (every 5 min) → GET /v1/crm/pending → creates crm.lead via ORM
                         → POST /v1/crm/processed/{id} → acknowledges
```

When a voice call completes, the KB service:
1. **Analyzes** the transcript using Gemini AI
2. **Extracts** customer intent, sentiment, priority, product interest, contact info
3. **Stores** the analysis in the `kb_call_records` table with `crm_status="pending"`
4. **Notifies** the sales team via WhatsApp (for hot/warm leads)
5. **Follows up** with the caller via WhatsApp/SMS

Then the Odoo `kb_crm` addon:
6. **Polls** `GET /v1/crm/pending` every 5 minutes (using existing KB URL + API key)
7. **Creates** a CRM opportunity with all AI fields via native ORM
8. **Attaches** the transcript and recording link as a chatter note
9. **Acknowledges** via `POST /v1/crm/processed/{id}` so the call is not re-imported

### AI Analysis Output

The Gemini-powered analyzer extracts:

| Field | Example Values |
|-------|---------------|
| Summary | "Customer asked about Enterprise pricing for 50 users" |
| Intent | `purchase`, `support`, `inquiry`, `complaint`, `return` |
| Priority | `hot` (ready to buy), `warm` (interested), `cold` (browsing) |
| Urgency | `high`, `medium`, `low` |
| Sentiment | `positive`, `neutral`, `negative` |
| Products | ["Enterprise Plan"] |
| Contact | {name: "Sarah", company: "Acme Corp"} |
| Suggested Action | `send_quote`, `schedule_demo`, `follow_up_call`, `escalate` |

### Setup

1. Ensure `KB_CRM_AUTO_CREATE=true` in `.env` (default)

2. Install the **kb_crm** addon in Odoo:
   ```bash
   cp -r odoo_addon/kb_crm /path/to/odoo/addons/
   # In Odoo: Apps → Update Apps List → Search "KB CRM" → Install
   ```

3. The Odoo addon uses the KB service URL and API key already configured in **Settings → Knowledge Base** — no additional credentials needed.

4. The cron job (`KB: Sync CRM Leads from Voice Calls`) runs every 5 minutes. You can also click **"Sync from KB"** in the CRM pipeline view for manual sync.

5. Optionally configure notifications:
   ```
   KB_NOTIFY_TEAM_NUMBERS=["14155551234","14155555678"]
   KB_NOTIFY_ON_PRIORITY=hot,warm
   KB_CALLER_FOLLOWUP_ENABLED=true
   ```

6. Restart the KB service — you should see:
   ```
   CRM analyzer initialized (model=gemini-2.5-flash)
   ```

### CRM API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /v1/crm/pending` | List calls with pending CRM lead creation |
| `POST /v1/crm/processed/{id}` | Mark a call as processed (body: `{"lead_id": 123}`) |

### CRM Lead Fields (Odoo)

The `kb_crm` addon adds a **Voice AI** tab to CRM leads with:

- AI Conversation Summary
- Customer Intent (selection)
- AI Priority (hot/warm/cold)
- Sentiment (positive/neutral/negative)
- Suggested Action
- AI Confidence (progress bar)
- Call UUID and Recording URL
- Full Transcript

Filters available: "Voice Calls", "Hot (AI)", "Negative Sentiment".

---

## Odoo Addons

### kb_connector (v19.0.2.0.0)

The main integration addon. Depends on: `base`, `mail`, `product`.

**Models:**
- `kb.mixin` — Abstract mixin to make any model KB-indexable
- `kb.document` — Standalone document management with rich text editor
- `kb.voice.agent` — Voice agent configuration (greeting, TTS, escalation)
- `res.config.settings` — KB service URL and API key
- Product template extension (`product.template` + `kb.mixin`)

**Menu:** Knowledge Base → Documents / Voice Agents / Settings

### kb_crm (v19.0.1.0.0)

CRM extension for AI-powered lead creation. Depends on: `crm`, `kb_connector`.

**Models:**
- `crm.lead` extension with KB fields (summary, intent, priority, sentiment, transcript)
- `_cron_sync_from_kb()` — pulls pending leads from KB service via HTTP

**Views:**
- Voice AI tab on lead form (visible when call UUID is set)
- "Sync from KB" button in pipeline tree view
- Optional columns: AI Priority, Customer Intent, Sentiment
- Search filters: "Voice Calls", "Hot (AI)", "Negative Sentiment"

**Cron:** Runs every 5 minutes, polls `GET /v1/crm/pending`, creates leads via ORM

---

## API Reference

### Search

```
POST /v1/search
```
```json
{
  "query": "What is your return policy?",
  "limit": 5,
  "source": "api",
  "conversation_id": "optional-for-context"
}
```

### Documents

```
POST   /v1/documents              # Ingest single document
POST   /v1/documents/bulk         # Ingest multiple documents
POST   /v1/documents/upload       # Upload file (PDF/CSV/HTML/text)
GET    /v1/documents              # List documents (paginated)
GET    /v1/documents/{id}         # Get document metadata
DELETE /v1/documents/{id}         # Delete document and chunks
```

### Voice (Vonage)

```
POST /v1/voip/vonage/answer       # Answer URL — greeting NCCO
POST /v1/voip/vonage/event        # Event URL — speech → KB → answer
POST /v1/voip/vonage/recording    # Recording callback
POST /v1/voip/vonage/status       # Call lifecycle events
```

### Voice (Generic / Other Providers)

```
POST /v1/voip/query               # JSON in/out (any provider)
POST /v1/voip/query/audio         # JSON in, WAV audio out
POST /v1/voip/twilio              # Twilio TwiML webhook
POST /v1/voip/sip                 # Asterisk AGI/ARI webhook
PUT  /v1/voip/agents/{agent_id}   # Create/update voice agent config
GET  /v1/voip/agents              # List voice agents
```

### Calls

```
GET /v1/calls                     # List call records (filters: status, caller)
GET /v1/calls/{id}                # Full call detail with transcript
GET /v1/calls/{id}/transcript     # Structured transcript only
GET /v1/calls/{id}/recording      # Stream recording audio file
```

### Messaging (WhatsApp/SMS)

```
POST /v1/messaging/inbound        # Inbound message webhook
POST /v1/messaging/status         # Delivery status webhook
GET  /v1/messaging/log            # Message history (filters: channel, sender)
```

### CRM (Pull-Based)

```
GET  /v1/crm/pending              # List calls awaiting CRM lead creation
POST /v1/crm/processed/{call_id}  # Mark call as processed (body: {"lead_id": N})
```

### Health & Analytics

```
GET /v1/health                    # Service health check
GET /v1/analytics                 # Query stats + cache performance
GET /v1/analytics/recent-queries  # Recent query history
```

Full interactive docs at **http://localhost:8100/docs** (Swagger UI).

---

## Project Structure

```
odoo-kb/
├── app/
│   ├── main.py                    # App factory + lifespan (service initialization)
│   ├── dependencies.py            # Dependency injection container
│   ├── api/
│   │   ├── auth.py                # API key verification
│   │   └── v1/
│   │       ├── router.py          # Route aggregator
│   │       ├── search.py          # POST /v1/search
│   │       ├── ingest.py          # POST /v1/documents
│   │       ├── documents.py       # GET/DELETE /v1/documents
│   │       ├── vonage.py          # Vonage answer/event/recording/status
│   │       ├── voip.py            # Generic VOIP + Twilio/SIP webhooks
│   │       ├── calls.py           # Call record retrieval API
│   │       ├── crm.py             # CRM pending/processed endpoints
│   │       ├── messaging.py       # WhatsApp/SMS inbound + log
│   │       ├── health.py          # Health check
│   │       └── analytics.py       # Query analytics
│   ├── core/
│   │   ├── search_service.py      # SearchOrchestrator (main pipeline)
│   │   ├── query_preprocessor.py  # Intent and filter extraction
│   │   ├── query_expansion.py     # Static/LLM synonym expansion
│   │   ├── reranker.py            # Result reranking (OpenAI/local)
│   │   ├── conversation.py        # Multi-turn context tracking
│   │   ├── cache.py               # LRU caches (embedding + search)
│   │   ├── query_logger.py        # Query analytics logging
│   │   ├── call_tracker.py        # In-memory call tracking + DB persistence
│   │   ├── crm_analyzer.py        # Gemini-powered transcript analysis
│   │   ├── notifications.py       # Triage notifications (WhatsApp/SMS)
│   │   ├── tts.py                 # Text-to-speech (Gemini)
│   │   ├── voice_agent_config.py  # Voice agent config store
│   │   └── exceptions.py          # Custom exceptions
│   ├── backends/
│   │   ├── pgvector.py            # PostgreSQL + pgvector (cosine distance)
│   │   ├── qdrant.py              # Qdrant vector DB
│   │   └── registry.py            # Backend factory
│   ├── ingestion/
│   │   ├── pipeline.py            # Extract → chunk → embed → index
│   │   ├── chunker.py             # Recursive text chunking
│   │   ├── embedder.py            # Embedding provider factory
│   │   └── extractors/            # PDF, HTML, CSV, text extractors
│   ├── clients/
│   │   └── vonage_messages.py     # Vonage Messages API (WhatsApp/SMS)
│   ├── models/
│   │   ├── document.py            # SQLAlchemy: documents + chunks
│   │   ├── call_record.py         # SQLAlchemy: call records + transcripts
│   │   └── message_log.py         # SQLAlchemy: WhatsApp/SMS message log
│   ├── schemas/
│   │   ├── search.py              # SearchRequest/SearchResponse
│   │   ├── document.py            # IngestRequest/DocumentResponse
│   │   └── common.py              # Shared types
│   └── enrichment/
│       ├── odoo_linker.py         # Odoo entity linking
│       └── tavily_web.py          # Web search enrichment
├── odoo_addon/
│   ├── kb_connector/              # Main Odoo addon
│   │   ├── models/
│   │   │   ├── kb_mixin.py        # Abstract KB-indexable mixin
│   │   │   ├── kb_document.py     # Document management model
│   │   │   ├── kb_voice_agent.py  # Voice agent config model
│   │   │   ├── product_kb.py      # Product → KB extension
│   │   │   └── res_config_settings.py
│   │   ├── views/                 # XML views for all models
│   │   ├── tools/kb_client.py     # Python HTTP client for KB service
│   │   └── security/              # Access control
│   └── kb_crm/                    # CRM integration addon
│       ├── models/crm_lead_kb.py  # CRM lead extension + cron sync
│       ├── data/ir_cron.xml       # Cron: sync every 5 min
│       ├── views/crm_lead_views.xml
│       └── security/
├── config/
│   ├── settings.py                # Pydantic settings (env vars)
│   └── filters.yaml               # Intent/filter taxonomy
├── tests/                         # pytest suite
├── docker-compose.yml             # PostgreSQL + KB service
├── Dockerfile
├── Makefile                       # Dev commands
├── requirements.txt
└── .env.example                   # Environment template
```

---

## Testing

```bash
# Run full test suite
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ -v --cov=app

# Quick VOIP test
make demo-voip q='What is the return policy?'

# Quick search test
make demo-search q='pricing plans'
```

### Manual End-to-End Test

```bash
# 1. Simulate a voice call
curl -X POST http://localhost:8100/v1/voip/vonage/answer \
  -H "Content-Type: application/json" \
  -d '{"uuid": "test-001", "from": "+15551234567", "to": "+15559876543"}'

# 2. Simulate speech input
curl -X POST http://localhost:8100/v1/voip/vonage/event \
  -H "Content-Type: application/json" \
  -d '{"uuid": "test-001", "speech": {"results": [{"text": "What are your pricing plans?", "confidence": 0.95}]}}'

# 3. Complete the call
curl -X POST http://localhost:8100/v1/voip/vonage/status \
  -H "Content-Type: application/json" \
  -d '{"uuid": "test-001", "status": "completed"}'

# 4. Check call record
curl http://localhost:8100/v1/calls/test-001

# 5. Check CRM pending (AI analysis should appear after ~10s)
curl http://localhost:8100/v1/crm/pending

# 6. Test WhatsApp inbound
curl -X POST http://localhost:8100/v1/messaging/inbound \
  -H "Content-Type: application/json" \
  -d '{"channel": "whatsapp", "from": "15551234567", "to": "15559876543", "text": "Do you offer free shipping?"}'
```

---

## Troubleshooting

### Service won't start

**"No module named 'google'"**
```bash
pip install google-genai
```

**"Connection refused" on database**
```bash
# Check if PostgreSQL is running
docker compose ps
# Start it if not
docker compose up kb-db -d
```

**"expected 1536 dimensions, not 768"**

The embedding column was created with OpenAI dimensions. Fix:
```sql
PGPASSWORD=kb psql -h localhost -p 5433 -U kb -d kb -c "
  DROP INDEX IF EXISTS ix_kb_chunks_embedding;
  ALTER TABLE kb_chunks ALTER COLUMN embedding TYPE vector(768);
  CREATE INDEX ix_kb_chunks_embedding ON kb_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
"
```

### Vonage calls not working

1. Check `KB_PUBLIC_URL` is set to your ngrok URL (not localhost)
2. Verify Answer URL in Vonage Dashboard matches: `https://<ngrok>/v1/voip/vonage/answer?agent_id=1`
3. Check service logs for incoming webhook requests
4. Test the answer endpoint directly:
   ```bash
   curl -X POST http://localhost:8100/v1/voip/vonage/answer \
     -H "Content-Type: application/json" \
     -d '{"uuid": "test", "from": "+1234"}'
   ```

### CRM leads not being created

1. Check startup logs for `"CRM analyzer initialized"` — if missing, verify `KB_GEMINI_API_KEY` is set and `KB_CRM_AUTO_CREATE=true`
2. Check `GET /v1/crm/pending` — if calls appear here but not in Odoo, the Odoo cron isn't running:
   - Verify the `kb_crm` addon is installed in Odoo
   - Check the cron job is active: **Odoo → Settings → Technical → Scheduled Actions → "KB: Sync CRM Leads"**
   - Verify KB service URL is set in **Odoo → Settings → Knowledge Base**
3. Ensure `KB_CRM_MIN_DURATION` isn't too high (default: 30 seconds)
4. Try manual sync: click **"Sync from KB"** in the CRM pipeline view

### WhatsApp replies not sending

1. Verify `KB_VONAGE_APPLICATION_ID` and `KB_VONAGE_PRIVATE_KEY_PATH` are set
2. Check startup logs for "Vonage Messages client initialized"
3. The inbound webhook still returns the KB answer in the response body even when the client is disabled — the reply just won't be sent via Vonage

---

## Make Commands

```bash
make help          # Show all commands
make install       # Install production dependencies
make dev           # Install all dependencies (dev + optional)
make test          # Run test suite
make lint          # Run ruff linter
make up            # Start all Docker services
make down          # Stop all services
make logs          # Tail service logs
make demo          # Start services + seed demo data
make demo-seed     # Seed 5 demo documents
make demo-search   # Search (usage: make demo-search q='query')
make demo-voip     # Test VOIP webhook
make clean         # Stop services and remove volumes
```
