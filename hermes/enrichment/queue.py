import hashlib
import logging

from psycopg2.extras import RealDictCursor

from hermes_utils.db import get_connection, _write

logger = logging.getLogger(__name__)


def _make_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def enqueue(home, profile_id: int) -> bool:
    """Insert into enrichment_queue. Return False if duplicate (same url hash + profile_id)."""
    queue_id = _make_id(home.url)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO hermes.enrichment_queue "
                "(id, profile_id, url, address, city, price, agency, sqm) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id, profile_id) DO NOTHING",
                [queue_id, profile_id, home.url, home.address, home.city,
                 home.price, home.agency, home.sqm],
            )
            inserted = cur.rowcount > 0
            conn.commit()
            return inserted
    except Exception as e:
        logger.error("Failed to enqueue url=%s profile_id=%s: %r", home.url, profile_id, e)
        return False
    finally:
        if conn:
            conn.close()


def drain_pending(limit: int = 50) -> list[dict]:
    """Atomically select pending items and mark them as processing.

    Uses UPDATE ... RETURNING inside a single transaction to prevent
    race conditions between concurrent analyzer instances.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "UPDATE hermes.enrichment_queue "
                "SET status = 'processing' "
                "WHERE (id, profile_id) IN ("
                "  SELECT id, profile_id FROM hermes.enrichment_queue "
                "  WHERE status = 'pending' "
                "  ORDER BY enqueued_at "
                "  LIMIT %s "
                "  FOR UPDATE SKIP LOCKED"
                ") RETURNING *",
                [limit],
            )
            rows = cur.fetchall()
            conn.commit()
            return list(rows)
    except Exception as e:
        logger.error("Failed to drain pending queue: %r", e)
        conn.rollback()
        return []
    finally:
        if conn:
            conn.close()


def mark_done(queue_id: str, profile_id: int) -> None:
    """Set status='done'."""
    _write(
        "UPDATE hermes.enrichment_queue SET status = 'done' "
        "WHERE id = %s AND profile_id = %s",
        [queue_id, profile_id],
    )


def mark_failed(queue_id: str, profile_id: int, reason: str) -> None:
    """Set status='failed' and log reason."""
    _write(
        "UPDATE hermes.enrichment_queue SET status = 'failed' "
        "WHERE id = %s AND profile_id = %s",
        [queue_id, profile_id],
    )
    logger.warning("Queue item %s (profile %s) failed: %s", queue_id, profile_id, reason)


def increment_retry(queue_id: str, profile_id: int) -> None:
    """Increment retry_count and reset status to 'pending'."""
    _write(
        "UPDATE hermes.enrichment_queue "
        "SET retry_count = retry_count + 1, status = 'pending' "
        "WHERE id = %s AND profile_id = %s",
        [queue_id, profile_id],
    )


def update_page_text(queue_id: str, profile_id: int, text: str, method: str) -> None:
    """Store fetched page text and fetch method on the queue row."""
    _write(
        "UPDATE hermes.enrichment_queue "
        "SET page_text = %s, fetch_method = %s "
        "WHERE id = %s AND profile_id = %s",
        [text, method, queue_id, profile_id],
    )
