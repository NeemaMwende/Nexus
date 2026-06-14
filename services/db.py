"""
PostgreSQL connection using psycopg (v3) with connection pool.
Reads/updates the contact_messages table from the Next.js project.
"""
from typing import Optional
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

import config

_pool: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    """Return or initialise the shared connection pool."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=config.DATABASE_URL,
            min_size=1,
            max_size=10,
        )
    return _pool


@contextmanager
def _get_conn():
    """Context manager that yields a connection and returns it to the pool."""
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


def update_status(
    submission_id: int,
    status: str,
    intent: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    """
    Update nexus_status (and optionally nexus_intent, nexus_notes)
    on the contact_messages row.

    Statuses: pending | processing | replied | spam | invalid | error
    """
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE contact_messages
                SET nexus_status       = %s,
                    nexus_intent       = COALESCE(%s, nexus_intent),
                    nexus_notes        = COALESCE(%s, nexus_notes),
                    nexus_processed_at = NOW()
                WHERE id = %s
                """,
                (status, intent, notes, submission_id),
            )
        conn.commit()


def get_pending_submissions(limit: int = 20) -> list[dict]:
    """
    Fetch rows with nexus_status='pending' that Nexus hasn't processed yet.
    Used as a fallback when the form doesn't fire the webhook.
    """
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, first_name, last_name, email, phone,
                       message, topic, created_at
                FROM   contact_messages
                WHERE  nexus_status = 'pending'
                ORDER  BY created_at ASC
                LIMIT  %s
                """,
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def mark_processing(submission_id: int) -> None:
    update_status(submission_id, "processing")


# ── Nexus persistence (B1) ──────────────────────────────────────────────────────

def ensure_schema() -> None:
    """
    Create Nexus's own persistence table + augment contact_messages.
    Idempotent — safe to call on every startup.
    """
    # 1) Nexus's own audit/history table (the dashboard's system of record)
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS nexus_email_log (
                    id                BIGSERIAL PRIMARY KEY,
                    email_id          TEXT NOT NULL UNIQUE,
                    thread_id         TEXT,
                    submission_id     BIGINT,
                    sender_name       TEXT,
                    sender_email      TEXT,
                    sender_phone      TEXT,
                    subject           TEXT,
                    message           TEXT,
                    product_interest  TEXT,
                    intent            TEXT,
                    intent_confidence REAL,
                    is_valid_email    BOOLEAN,
                    is_valid_phone    BOOLEAN,
                    is_spam           BOOLEAN,
                    spam_reason       TEXT,
                    outcome           TEXT,
                    reply_sent        BOOLEAN DEFAULT FALSE,
                    draft_reply_html  TEXT,
                    draft_reply_text  TEXT,
                    rag_chunks        JSONB,
                    node_timings      JSONB,
                    duration_ms       INTEGER,
                    error             TEXT,
                    status_message    TEXT,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS nexus_email_log_created_idx "
                "ON nexus_email_log (created_at DESC)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS nexus_email_log_outcome_idx "
                "ON nexus_email_log (outcome)"
            )
        conn.commit()

    # 2) Augment the form-owned contact_messages table (best-effort — separate
    #    transaction so a failure here never blocks creating our own table).
    try:
        with _get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    ALTER TABLE contact_messages
                        ADD COLUMN IF NOT EXISTS nexus_status       TEXT,
                        ADD COLUMN IF NOT EXISTS nexus_intent       TEXT,
                        ADD COLUMN IF NOT EXISTS nexus_confidence   REAL,
                        ADD COLUMN IF NOT EXISTS nexus_notes        TEXT,
                        ADD COLUMN IF NOT EXISTS nexus_processed_at TIMESTAMPTZ
                    """
                )
            conn.commit()
    except Exception as e:
        print(f"[db] contact_messages augment skipped: {e}")


def log_email(p: dict) -> None:
    """
    Upsert one processed-email record into nexus_email_log (keyed by email_id,
    so reprocessing the same Gmail message updates the row instead of duplicating).
    JSONB columns are passed as JSON strings and cast in SQL.
    """
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO nexus_email_log (
                    email_id, thread_id, submission_id, sender_name, sender_email,
                    sender_phone, subject, message, product_interest, intent,
                    intent_confidence, is_valid_email, is_valid_phone, is_spam,
                    spam_reason, outcome, reply_sent, draft_reply_html, draft_reply_text,
                    rag_chunks, node_timings, duration_ms, error, status_message
                ) VALUES (
                    %(email_id)s, %(thread_id)s, %(submission_id)s, %(sender_name)s, %(sender_email)s,
                    %(sender_phone)s, %(subject)s, %(message)s, %(product_interest)s, %(intent)s,
                    %(intent_confidence)s, %(is_valid_email)s, %(is_valid_phone)s, %(is_spam)s,
                    %(spam_reason)s, %(outcome)s, %(reply_sent)s, %(draft_reply_html)s, %(draft_reply_text)s,
                    %(rag_chunks)s::jsonb, %(node_timings)s::jsonb, %(duration_ms)s, %(error)s, %(status_message)s
                )
                ON CONFLICT (email_id) DO UPDATE SET
                    thread_id         = EXCLUDED.thread_id,
                    submission_id     = EXCLUDED.submission_id,
                    sender_name       = EXCLUDED.sender_name,
                    sender_email      = EXCLUDED.sender_email,
                    sender_phone      = EXCLUDED.sender_phone,
                    subject           = EXCLUDED.subject,
                    message           = EXCLUDED.message,
                    product_interest  = EXCLUDED.product_interest,
                    intent            = EXCLUDED.intent,
                    intent_confidence = EXCLUDED.intent_confidence,
                    is_valid_email    = EXCLUDED.is_valid_email,
                    is_valid_phone    = EXCLUDED.is_valid_phone,
                    is_spam           = EXCLUDED.is_spam,
                    spam_reason       = EXCLUDED.spam_reason,
                    outcome           = EXCLUDED.outcome,
                    reply_sent        = EXCLUDED.reply_sent,
                    draft_reply_html  = EXCLUDED.draft_reply_html,
                    draft_reply_text  = EXCLUDED.draft_reply_text,
                    rag_chunks        = EXCLUDED.rag_chunks,
                    node_timings      = EXCLUDED.node_timings,
                    duration_ms       = EXCLUDED.duration_ms,
                    error             = EXCLUDED.error,
                    status_message    = EXCLUDED.status_message,
                    created_at        = NOW()
                """,
                p,
            )
        conn.commit()


def fetch_events(
    limit: int = 50,
    offset: int = 0,
    outcome: Optional[str] = None,
    intent: Optional[str] = None,
    search: Optional[str] = None,
    since=None,
) -> dict:
    """
    Paginated processed-email history from nexus_email_log (B2).
    Returns {"events": [...], "total": N, "limit": L, "offset": O}.
    List view omits the heavy columns (reply HTML, rag passages) — those load per-row via fetch_event.
    """
    where, params = [], []
    if outcome:
        where.append("outcome = %s"); params.append(outcome)
    if intent:
        where.append("intent = %s"); params.append(intent)
    if search:
        where.append("(sender_name ILIKE %s OR sender_email ILIKE %s OR subject ILIKE %s OR message ILIKE %s)")
        s = f"%{search}%"; params.extend([s, s, s, s])
    if since:
        where.append("created_at >= %s"); params.append(since)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cols = ("id, email_id, created_at, sender_name, sender_email, subject, intent, "
            "intent_confidence, outcome, reply_sent, is_spam, is_valid_email, is_valid_phone, duration_ms")

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM nexus_email_log {where_sql}", params)
            total = cur.fetchone()[0]
            cur.execute(
                f"SELECT {cols} FROM nexus_email_log {where_sql} "
                f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
                params + [limit, offset],
            )
            names = [d[0] for d in cur.description]
            events = [dict(zip(names, row)) for row in cur.fetchall()]
    return {"events": events, "total": total, "limit": limit, "offset": offset}


def fetch_event(event_id: int) -> Optional[dict]:
    """Full detail for one processed email (B2) — includes reply HTML, rag passages, node timings."""
    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM nexus_email_log WHERE id = %s", (event_id,))
            row = cur.fetchone()
            if not row:
                return None
            names = [d[0] for d in cur.description]
            return dict(zip(names, row))


def fetch_stats(rng: str = "today") -> dict:
    """
    Aggregate KPIs + deltas vs the previous equal window + throughput buckets (B3).
    rng ∈ {today, 7d, 30d}. All windows computed in SQL against DB time.
    """
    if rng not in ("today", "7d", "30d"):
        rng = "today"
    cur_start = {
        "today": "date_trunc('day', now())",
        "7d":    "now() - interval '7 days'",
        "30d":   "now() - interval '30 days'",
    }[rng]
    prev_start = {
        "today": "date_trunc('day', now()) - interval '1 day'",
        "7d":    "now() - interval '14 days'",
        "30d":   "now() - interval '60 days'",
    }[rng]
    bucket = "hour" if rng == "today" else "day"

    def _norm(rows: list) -> dict:
        d = {"processed": 0, "replied": 0, "spam": 0, "invalid": 0, "errors": 0, "held": 0}
        keymap = {"replied": "replied", "spam": "spam", "invalid": "invalid", "error": "errors", "held": "held"}
        for outcome, c in rows:
            d["processed"] += c
            if outcome in keymap:
                d[keymap[outcome]] = c
        return d

    with _get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT outcome, count(*) FROM nexus_email_log WHERE created_at >= {cur_start} GROUP BY outcome")
            cur_tot = _norm(cur.fetchall())
            cur.execute(
                f"SELECT outcome, count(*) FROM nexus_email_log "
                f"WHERE created_at >= {prev_start} AND created_at < {cur_start} GROUP BY outcome"
            )
            prev_tot = _norm(cur.fetchall())
            cur.execute(
                f"""
                SELECT date_trunc('{bucket}', created_at) AS t,
                       count(*) FILTER (WHERE outcome = 'replied') AS replied,
                       count(*) FILTER (WHERE outcome = 'held')    AS held,
                       count(*) AS total
                FROM nexus_email_log
                WHERE created_at >= {cur_start}
                GROUP BY t ORDER BY t
                """
            )
            names = [d[0] for d in cur.description]
            buckets = [dict(zip(names, row)) for row in cur.fetchall()]

    def _pct(c: int, p: int):
        return round((c - p) / p * 100) if p else None

    deltas = {k: _pct(cur_tot[k], prev_tot[k]) for k in cur_tot}
    return {"range": rng, "totals": cur_tot, "previous": prev_tot,
            "deltas": deltas, "bucket": bucket, "buckets": buckets}


def close_pool() -> None:
    """Close all connections in the pool."""
    global _pool
    if _pool:
        _pool.close()
        _pool = None
