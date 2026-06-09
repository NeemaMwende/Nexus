"""
Nexus main entry point.
Starts FastAPI server + APScheduler background poll.
"""
import asyncio, json, time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from agent.graph       import nexus_graph
from agent.state       import NexusState
from services.gmail_client import gmail
from services.qdrant_store import vector_store
from services          import db

# ── SSE event bus (in-memory queue per connected client) ─────────────────────

_sse_clients: list[asyncio.Queue] = []


async def broadcast(event: dict):
    """Push a status event to all connected SSE clients."""
    for q in _sse_clients:
        await q.put(event)


# ── Core processing function ──────────────────────────────────────────────────

async def process_email(email_dict: dict, submission_id: int = None):
    """
    Build initial NexusState from a Gmail message dict and run the graph.
    Called by both the poller and the webhook endpoint.
    """
    await broadcast({
        "type":    "processing_started",
        "email_id": email_dict.get("id"),
        "subject": email_dict.get("subject", ""),
        "message": f"Processing: {email_dict.get('subject', 'new email')}",
    })

    initial_state: NexusState = {
        # Gmail fields
        "email_id":        email_dict.get("id", ""),
        "thread_id":       email_dict.get("thread_id", ""),
        "subject":         email_dict.get("subject", ""),
        "sender_address":  email_dict.get("sender", ""),
        "raw_html":        email_dict.get("body_html", ""),
        "raw_text":        email_dict.get("body_text", ""),

        # Will be populated by nodes
        "submission_id":   submission_id,
        "sender_name":     "",
        "sender_email":    "",
        "sender_phone":    "",
        "message":         "",
        "product_interest": "",
        "is_valid_email":  False,
        "is_valid_phone":  False,
        "is_spam":         False,
        "spam_reason":     "",
        "intent":          "unknown",
        "intent_confidence": 0.0,
        "rag_chunks":      [],
        "draft_reply_html": "",
        "draft_reply_text": "",
        "reply_sent":      False,
        "error":           None,
        "status_message":  "Starting...",
    }

    # Run graph synchronously in a thread pool (LangGraph is sync)
    loop = asyncio.get_event_loop()
    try:
        final_state = await loop.run_in_executor(
            None,
            lambda: nexus_graph.invoke(initial_state)
        )
        await broadcast({
            "type":         "processing_done",
            "email_id":     email_dict.get("id"),
            "reply_sent":   final_state.get("reply_sent"),
            "intent":       final_state.get("intent"),
            "message":      final_state.get("status_message"),
        })
    except Exception as e:
        print(f"[Nexus] Graph error for {email_dict.get('id')}: {e}")
        await broadcast({
            "type":    "processing_error",
            "email_id": email_dict.get("id"),
            "message": str(e),
        })


# ── Gmail poller ──────────────────────────────────────────────────────────────

async def poll_gmail():
    """
    Called by APScheduler every POLL_INTERVAL seconds.
    Fetches unread contact-form emails and processes each one.
    """
    try:
        emails = gmail.get_unread_contact_form_emails()
        if not emails:
            return

        print(f"[Poll] Found {len(emails)} new email(s)")
        for email_dict in emails:
            await process_email(email_dict)
            await asyncio.sleep(2)   # small delay between emails
    except Exception as e:
        print(f"[Poll] Error: {e}")


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────

scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("\n=== Nexus starting ===")

    # Ensure Gmail label exists
    try:
        gmail.ensure_label_exists(config.GMAIL_LABEL)
        print(f"✓ Gmail label '{config.GMAIL_LABEL}' ready")
    except Exception as e:
        print(f"⚠ Gmail label setup: {e}")

    # Ensure Qdrant collection exists
    try:
        vector_store.ensure_collection()
        count = vector_store.count()
        print(f"✓ Qdrant ready — {count} vectors in knowledge base")
        if count == 0:
            print("  ⚠ Knowledge base is empty. Run: python -m rag.ingest_salesmate")
    except Exception as e:
        print(f"⚠ Qdrant: {e}")

    # Start email poller
    scheduler.add_job(
        poll_gmail,
        "interval",
        seconds=config.POLL_INTERVAL,
        id="nexus_poll",
        max_instances=1,     # never run two polls concurrently
    )
    scheduler.start()
    print(f"✓ Email poller started (every {config.POLL_INTERVAL}s)")
    print(f"✓ Nexus running at http://localhost:{config.NEXUS_PORT}\n")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    db.close_pool()
    print("Nexus stopped.")


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(title="Nexus Agent", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def verify_secret(request: Request) -> bool:
    provided = request.headers.get("X-Nexus-Secret", "")
    expected = config.NEXUS_SECRET
    if not expected or not provided:
        return False
    if len(provided) != len(expected):
        return False
    diff = 0
    for a, b in zip(provided, expected):
        diff |= ord(a) ^ ord(b)
    return diff == 0


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"service": "Nexus Agent", "status": "running"}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "qdrant_vectors": vector_store.count(),
        "poll_interval":  config.POLL_INTERVAL,
        "llm_provider":   config.LLM_PROVIDER,
    }


@app.post("/webhook/contact")
async def contact_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Called by the Next.js form immediately on submission.
    Processes the new email without waiting for the 60s poll.
    """
    if not verify_secret(request):
        raise HTTPException(status_code=403, detail="Invalid secret")

    payload = await request.json()
    submission_id = payload.get("submissionId")

    # The form sends the submission ID; Nexus reads the actual email from Gmail
    # We trigger an immediate poll rather than re-parsing the form payload
    background_tasks.add_task(poll_gmail)

    return {"received": True, "submission_id": submission_id}


@app.post("/webhook/gmail-push")
async def gmail_push_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Google Cloud Pub/Sub push endpoint (production only).
    Called by Google when a new email arrives — replaces polling.
    """
    # Gmail push sends a validation challenge first
    data = await request.json()

    if "message" in data:
        # Decode the Pub/Sub notification
        import base64
        msg_data = base64.b64decode(data["message"]["data"]).decode("utf-8")
        notification = json.loads(msg_data)
        print(f"[Gmail Push] Notification: historyId={notification.get('historyId')}")

        # Trigger immediate poll to fetch the new message
        background_tasks.add_task(poll_gmail)

    return {"status": "ok"}


@app.get("/stream/status")
async def stream_status(request: Request):
    """
    SSE endpoint — Next.js admin page subscribes here for real-time updates.
    """
    queue: asyncio.Queue = asyncio.Queue()
    _sse_clients.append(queue)

    async def event_generator() -> AsyncGenerator:
        try:
            # Send initial connection event
            yield {
                "event": "connected",
                "data":  json.dumps({"message": "Nexus status stream connected"}),
            }
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield {"event": event["type"], "data": json.dumps(event)}
                except asyncio.TimeoutError:
                    # Heartbeat to keep the connection alive
                    yield {"event": "heartbeat", "data": "{}"}
        finally:
            _sse_clients.remove(queue)

    return EventSourceResponse(event_generator())


@app.post("/trigger/poll")
async def trigger_poll(request: Request, background_tasks: BackgroundTasks):
    """Manual trigger — useful for testing without waiting for the scheduler."""
    if not verify_secret(request):
        raise HTTPException(status_code=403, detail="Invalid secret")
    background_tasks.add_task(poll_gmail)
    return {"triggered": True}


@app.post("/trigger/ingest")
async def trigger_ingest(request: Request, background_tasks: BackgroundTasks):
    """Manually re-run the Salesmate ingestion."""
    if not verify_secret(request):
        raise HTTPException(status_code=403, detail="Invalid secret")

    def run_ingest():
        from rag.ingest_salesmate import run_full_ingest
        run_full_ingest()

    background_tasks.add_task(run_ingest)
    return {"triggered": True, "message": "Ingest running in background"}


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=config.NEXUS_PORT,
        reload=False,
        log_level="info",
    )
