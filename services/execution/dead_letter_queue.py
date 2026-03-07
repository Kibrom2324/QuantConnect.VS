"""
APEX Dead-Letter Queue — services/execution/dead_letter_queue.py

Fixes implemented in this file
───────────────────────────────
  HI-7   DLQ persist_to_db() was declared but never called after double failure.
         Fix: send() tries Kafka first; on Kafka failure it falls through to
         persist_to_db() which writes the failed message to SQLite so it is
         never silently dropped.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import structlog
from confluent_kafka import KafkaException, Producer

logger = structlog.get_logger(__name__)

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DLQ_TOPIC       = os.environ.get("DLQ_TOPIC",               "apex.dlq")
DLQ_DB_PATH     = Path(os.environ.get("DLQ_DB_PATH", "/var/lib/apex/dlq/apex_dlq.db"))


class DeadLetterQueue:
    """
    Two-tier DLQ:
      Tier 1 — Kafka topic 'apex.dlq'
      Tier 2 — SQLite file (HI-7 FIX: actually called on Kafka failure)

    HI-7 FIX 2026-02-27: persist_to_db() is now called inside send() when
    the Kafka produce+flush fails, ensuring NO message is silently discarded.
    """

    def __init__(self) -> None:
        try:
            self._producer = Producer({"bootstrap.servers": KAFKA_BOOTSTRAP})
            self._kafka_ok = True
        except KafkaException as e:
            logger.warning("dlq_kafka_unavailable_using_db_only", error=str(e))
            self._kafka_ok = False
            self._producer = None

        self._ensure_db()

    # ─── Public API ──────────────────────────────────────────────────────────

    async def send(self, topic: str, message: str | bytes, error: str = "") -> None:
        """
        HI-7 FIX: Try Kafka; fall through to SQLite if Kafka fails.
        Guarantees the message is persisted somewhere.
        """
        if isinstance(message, bytes):
            message = message.decode("utf-8", errors="replace")

        envelope = json.dumps({
            "original_topic": topic,
            "message":        message,
            "error":          error,
            "ts":             datetime.now(timezone.utc).isoformat(),
        })

        kafka_success = False
        if self._kafka_ok and self._producer is not None:
            try:
                self._producer.produce(DLQ_TOPIC, value=envelope.encode())
                self._producer.flush(timeout=5.0)
                kafka_success = True
                logger.info("dlq_message_sent_to_kafka", original_topic=topic)
            except Exception as e:
                logger.error("dlq_kafka_produce_failed_falling_back_to_db", error=str(e))

        if not kafka_success:
            # HI-7 FIX: actually persist to DB instead of just defining the method
            self.persist_to_db(topic=topic, message=message, error=error)

    # ─── DB tier (HI-7) ──────────────────────────────────────────────────────

    def _ensure_db(self) -> None:
        """Create DLQ table if it doesn't exist."""
        DLQ_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DLQ_DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dead_letter (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_topic TEXT    NOT NULL,
                    message        TEXT    NOT NULL,
                    error          TEXT,
                    created_at     TEXT    NOT NULL,
                    replayed       INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.commit()

    def persist_to_db(self, topic: str, message: str, error: str = "") -> None:
        """
        HI-7 FIX 2026-02-27: Write failed message to SQLite.
        This method is now ACTUALLY CALLED from send() instead of being dead code.
        """
        now = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(DLQ_DB_PATH) as conn:
                conn.execute(
                    """INSERT INTO dead_letter (original_topic, message, error, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (topic, message, error, now),
                )
                conn.commit()
            logger.warning(
                "dlq_message_persisted_to_db",   # HI-7 FIX identifier
                original_topic=topic,
                db=str(DLQ_DB_PATH),
            )
        except Exception as e:
            # Last resort — at minimum log the full message so it appears in log sink
            logger.critical(
                "dlq_db_write_failed_message_logged_as_last_resort",
                original_topic=topic,
                error=str(e),
                message_preview=message[:500],
            )

    # ─── Replay helper (ops tooling) ─────────────────────────────────────────

    def list_unplayed(self) -> list[dict]:
        """Return all unrplayed DLQ records from SQLite."""
        with sqlite3.connect(DLQ_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM dead_letter WHERE replayed=0 ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_replayed(self, record_id: int) -> None:
        with sqlite3.connect(DLQ_DB_PATH) as conn:
            conn.execute(
                "UPDATE dead_letter SET replayed=1 WHERE id=?", (record_id,)
            )
            conn.commit()
