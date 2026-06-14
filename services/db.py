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


def close_pool() -> None:
    """Close all connections in the pool."""
    global _pool
    if _pool:
        _pool.close()
        _pool = None
