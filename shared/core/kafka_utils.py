"""
shared/core/kafka_utils.py — Kafka consumer/producer factory helpers.

Features:
- Consumer factory with enable.auto.commit=False enforced
- Manual commit helper (commit after successful downstream action)
- Stale message gate: reject if (now - signal_timestamp) > 30 seconds
- Dead letter queue publish on unrecoverable decode errors
- Structured JSON logging via structlog

Usage:
    consumer = make_consumer("alpha.signals", "risk-engine-group")
    producer = make_producer()

    for msg in consumer_iter(consumer):
        payload = decode_message(msg)
        if payload is None:
            continue                    # logged + DLQ'd internally
        if is_stale(payload, max_age_s=30):
            consumer.commit(msg)        # drop stale, still commit
            continue
        # ... process ...
        producer.produce("risk.approved", json.dumps(result).encode())
        producer.flush()
        consumer.commit(msg)
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Generator

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Lazy import so tests can patch confluent_kafka before this module loads
# ---------------------------------------------------------------------------
def _kafka():
    import confluent_kafka  # noqa: PLC0415
    return confluent_kafka


# ---------------------------------------------------------------------------
# Defaults (override via env)
# ---------------------------------------------------------------------------
_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
_DLQ_TOPIC  = os.getenv("KAFKA_DLQ_TOPIC", "apex.dlq")


# ---------------------------------------------------------------------------
# Consumer factory
# ---------------------------------------------------------------------------
def make_consumer(
    topic: str,
    group_id: str,
    *,
    bootstrap_servers: str = _BOOTSTRAP,
    extra_config: dict[str, Any] | None = None,
) -> Any:
    """Return a Consumer subscribed to *topic* with manual commit enforced."""
    ck = _kafka()
    cfg: dict[str, Any] = {
        "bootstrap.servers": bootstrap_servers,
        "group.id": group_id,
        "enable.auto.commit": False,   # MANDATORY — never override
        "auto.offset.reset": "earliest",
        "session.timeout.ms": 30_000,
        "max.poll.interval.ms": 300_000,
        "fetch.min.bytes": 1,
        "fetch.wait.max.ms": 500,
    }
    if extra_config:
        # Prevent accidental override of the safety config
        extra_config.pop("enable.auto.commit", None)
        cfg.update(extra_config)

    consumer = ck.Consumer(cfg)
    consumer.subscribe([topic])
    log.info("kafka_consumer_subscribed", topic=topic, group=group_id)
    return consumer


# ---------------------------------------------------------------------------
# Producer factory
# ---------------------------------------------------------------------------
def make_producer(
    *,
    bootstrap_servers: str = _BOOTSTRAP,
    extra_config: dict[str, Any] | None = None,
) -> Any:
    """Return a configured Producer."""
    ck = _kafka()
    cfg: dict[str, Any] = {
        "bootstrap.servers": bootstrap_servers,
        "acks": "all",
        "retries": 5,
        "retry.backoff.ms": 500,
        "linger.ms": 5,
        "compression.type": "lz4",
    }
    if extra_config:
        cfg.update(extra_config)
    producer = ck.Producer(cfg)
    log.info("kafka_producer_created", bootstrap=bootstrap_servers)
    return producer


# ---------------------------------------------------------------------------
# Poll iterator
# ---------------------------------------------------------------------------
def consumer_iter(
    consumer: Any,
    poll_timeout: float = 1.0,
) -> Generator[Any, None, None]:
    """
    Yield non-error Kafka messages.  Skips internal EOF partitions.
    Logs and yields None for decode errors so callers can DLQ them.
    """
    ck = _kafka()
    while True:
        msg = consumer.poll(poll_timeout)
        if msg is None:
            continue
        err = msg.error()
        if err:
            if err.code() == ck.KafkaError._PARTITION_EOF:
                continue
            log.error("kafka_consumer_error", error=str(err))
            continue
        yield msg


# ---------------------------------------------------------------------------
# Message decoding
# ---------------------------------------------------------------------------
def decode_message(
    msg: Any,
    producer: Any | None = None,
) -> dict[str, Any] | None:
    """
    Deserialise a Kafka message value as JSON.

    Returns None (and publishes to DLQ if producer given) on failure.
    """
    raw = msg.value()
    if raw is None:
        log.warning("kafka_null_message", topic=msg.topic())
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.error("kafka_decode_error", exc=str(exc), raw_bytes=repr(raw[:120]))
        if producer is not None:
            try:
                producer.produce(
                    _DLQ_TOPIC,
                    value=raw,
                    headers={"error": str(exc).encode()},
                )
                producer.flush()
            except Exception as dlq_exc:  # noqa: BLE001
                log.error("kafka_dlq_publish_failed", exc=str(dlq_exc))
        return None


# ---------------------------------------------------------------------------
# Stale message gate
# ---------------------------------------------------------------------------
def is_stale(
    payload: dict[str, Any],
    *,
    max_age_s: float = 30.0,
    ts_key: str = "signal_timestamp",
) -> bool:
    """
    Return True if the message is older than *max_age_s* seconds.

    The timestamp field must be a Unix epoch float or ISO-8601 string.
    Missing timestamp → treated as stale (fail-closed).
    """
    ts_raw = payload.get(ts_key)
    if ts_raw is None:
        log.warning("kafka_stale_gate_no_timestamp", keys=list(payload.keys()))
        return True

    try:
        if isinstance(ts_raw, (int, float)):
            ts = float(ts_raw)
        else:
            from datetime import datetime, timezone  # noqa: PLC0415
            ts = datetime.fromisoformat(str(ts_raw)).replace(
                tzinfo=timezone.utc
            ).timestamp()
    except (ValueError, TypeError) as exc:
        log.warning("kafka_stale_gate_parse_error", raw=ts_raw, exc=str(exc))
        return True

    age = time.time() - ts
    if age > max_age_s:
        log.warning("kafka_stale_message_dropped", age_s=round(age, 2), limit_s=max_age_s)
        return True
    return False


# ---------------------------------------------------------------------------
# Safe commit helper
# ---------------------------------------------------------------------------
def safe_commit(consumer: Any, msg: Any) -> None:
    """Commit a single message offset.  Logs but never raises."""
    try:
        consumer.commit(msg)
    except Exception as exc:  # noqa: BLE001
        log.error("kafka_commit_failed", exc=str(exc))


# ---------------------------------------------------------------------------
# Convenience: publish + flush + commit in one atomic call
# ---------------------------------------------------------------------------
def publish_and_commit(
    producer: Any,
    consumer: Any,
    msg: Any,
    *,
    topic: str,
    value: bytes,
    key: bytes | None = None,
) -> None:
    """
    Produce to *topic*, flush, then commit *msg*.

    Order: produce → flush → commit (CF-7 fix).
    Raises on flush error so caller can handle without committing.
    """
    producer.produce(topic, value=value, key=key)
    producer.flush()          # raise on delivery failure BEFORE commit
    safe_commit(consumer, msg)


# ---------------------------------------------------------------------------
# Delivery callback for async producers
# ---------------------------------------------------------------------------
def make_delivery_callback(
    log_extra: dict[str, Any] | None = None,
) -> Callable[[Any, Any], None]:
    """Return a delivery report callback compatible with producer.produce(on_delivery=...)."""
    extra = log_extra or {}

    def _cb(err: Any, _msg: Any) -> None:
        if err:
            log.error("kafka_delivery_failed", error=str(err), **extra)
        else:
            log.debug("kafka_delivery_ok", **extra)

    return _cb
