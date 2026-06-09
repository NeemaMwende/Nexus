"""
Raw PostgreSQL connection using psycopg2.
Reads/updates the contact_messages table from the Next.js project.
"""
import psycopg2
from psycopg2 import pool as pg_pool
from typing import Optional
import config

_pool: Optional[pg_pool.ThreadedConnectionPool] = None


def get_pool() -> pg_pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = pg_pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=config.DATABASE_URL,
        )
    return _pool


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
    pool = get_pool()
    conn = pool.getconn()
    try:
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
    finally:
        pool.putconn(conn)


def get_pending_submissions(limit: int = 20) -> list[dict]:
    """
    Fetch rows with nexus_status='pending' that Nexus hasn't processed yet.
    Used as a fallback when the form doesn't fire the webhook.
    """
    pool = get_pool()
    conn = pool.getconn()
    try:
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
    finally:
        pool.putconn(conn)


def mark_processing(submission_id: int) -> None:
    update_status(submission_id, "processing")


def close_pool() -> None:
    global _pool
    if _pool:
        _pool.closeall()
        _pool = None
