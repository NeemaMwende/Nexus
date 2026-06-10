# Nexus

An AI-powered email processing agent for Technobrain's contact form. Monitors Gmail for new submission notifications, classifies intent using LLM, retrieves relevant knowledge via RAG, and drafts automated replies.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Framework | FastAPI |
| Agent | LangGraph |
| LLM | Groq API or Ollama (local) |
| Vector Store | Qdrant |
| Email | Gmail API (OAuth2) |
| Database | PostgreSQL |
| Language | Python 3.11+ |

## Prerequisites

- **Python** 3.11 or later
- **Node.js** 18.17+ (for the contact-form frontend)
- **PostgreSQL** 12+ (shared with contact-form)
- **Qdrant** vector database (local or remote)
- **Gmail account** with OAuth2 credentials

## Getting Started

1. Clone the repository and navigate into the project:

```bash
cd nexus
```

2. Install dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. Install Playwright browsers (required for Salesmate scraping):

```bash
playwright install chromium
```

4. Configure environment variables by copying `.env.example` to `.env`:

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Gmail OAuth2 (get from Google Cloud Console)
GMAIL_CLIENT_ID=your-client-id.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=your-client-secret
GMAIL_MONITORED_ACCOUNT=technobrain6@gmail.com
GMAIL_LABEL=nexus-contact-form

# LLM (choose groq or ollama)
LLM_PROVIDER=groq
GROQ_API_KEY=your-groq-api-key
GROQ_MODEL=llama-3.3-70b-versatile

# Qdrant
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_COLLECTION=salesmate_knowledge

# Embedding model
EMBED_MODEL=nomic-embed-text
EMBED_DIM=768

# Salesmate CRM
SALESMATE_URL=https://salesmate.technobraingroup.com
SALESMATE_USERNAME=your-username
SALESMATE_PASSWORD=your-password

# PostgreSQL
DATABASE_URL=postgresql://postgres:password@localhost:5432/techno_brain_forms

# Nexus server
NEXUS_PORT=8000
NEXUS_SECRET=change-this-long-random-secret
```

5. Set up Gmail authentication:

```bash
python scripts/gmail_auth.py
```

6. Start Qdrant (if running locally):

```bash
docker run -p 6333:6333 qdrant/qdrant
```

7. Ingest Salesmate knowledge base:

```bash
python -m rag.ingest_salesmate
```

8. Start the Nexus server:

```bash
python main.py
```

## Available Scripts

| Command | Description |
|---------|-------------|
| `python main.py` | Start Nexus server with email poller |
| `python -m rag.ingest_salesmate` | Scrape and ingest Salesmate knowledge |
| `python scripts/gmail_auth.py` | Generate Gmail OAuth token |

## API Endpoints

### `GET /`

Returns service status.

```json
{ "service": "Nexus Agent", "status": "running" }
```

### `GET /health`

Health check with system status.

```json
{
  "status": "ok",
  "qdrant_vectors": 1234,
  "poll_interval": 60,
  "llm_provider": "groq"
}
```

### `GET /api/recent`

Returns recent processing events for dashboard hydration.

### `GET /stream/status`

SSE endpoint for real-time status updates. The contact-form admin page subscribes here.

### `POST /webhook/contact`

Webhook endpoint called by contact-form on submission. Triggers immediate email processing.

**Headers:** `X-Nexus-Secret: <secret>`

### `POST /webhook/gmail-push`

Google Cloud Pub/Sub push endpoint for Gmail push notifications (production).

### `POST /trigger/poll`

Manual trigger for testing without waiting for scheduler.

**Headers:** `X-Nexus-Secret: <secret>`

### `POST /trigger/ingest`

Manually re-run Salesmate knowledge ingestion.

**Headers:** `X-Nexus-Secret: <secret>`

## Architecture Flow

```
1. Contact form submission → Next.js API → Stores in PostgreSQL
2. Nexus polls Gmail (60s interval or webhook-triggered)
3. New email detected → Fetch from Gmail
4. Intent classification → LLM determines user intent
5. RAG retrieval → Fetch relevant knowledge from Qdrant
6. Draft reply → Generate personalized response
7. Send reply → Email response to user (planned)
```

## Project Structure

```
nexus/
├── agent/
│   ├── graph.py          # LangGraph workflow definition
│   ├── state.py          # NexusState type definition
│   └── nodes/            # Graph processing nodes
├── services/
│   ├── gmail_client.py   # Gmail API integration
│   ├── qdrant_store.py   # Vector store operations
│   ├── db.py             # PostgreSQL connection
│   └── llm_client.py     # LLM abstraction layer
├── rag/
│   └── ingest_salesmate.py  # Knowledge base ingestion
├── scripts/
│   └── gmail_auth.py     # OAuth2 authentication flow
├── data/
│   └── gmail_token.json  # OAuth token (generated)
├── docker-compose.yml    # Qdrant service
├── main.py             # FastAPI entry point
└── requirements.txt      # Python dependencies
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GMAIL_CLIENT_ID` | OAuth2 client ID from Google Cloud Console | Yes |
| `GMAIL_CLIENT_SECRET` | OAuth2 client secret | Yes |
| `GMAIL_MONITORED_ACCOUNT` | Gmail account to monitor | Yes |
| `LLM_PROVIDER` | LLM provider: `groq` or `ollama` | Yes |
| `GROQ_API_KEY` | Groq API key (if using groq) | Yes* |
| `QDRANT_HOST` | Qdrant server host | Yes |
| `QDRANT_PORT` | Qdrant server port | Yes |
| `SALESMATE_USERNAME` | Salesmate login username | Yes |
| `SALESMATE_PASSWORD` | Salesmate login password | Yes |
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `NEXUS_PORT` | Port for Nexus server | No (default: 8000) |
| `NEXUS_SECRET` | Secret for webhook authentication | Yes |
| `POLL_INTERVAL` | Gmail poll interval in seconds | No (default: 60) |

*Required only if `LLM_PROVIDER=groq`

## Related Projects

- [contact-form](../Technobrain/contact-form) - Next.js frontend that submits form data and receives webhook notifications from Nexus.