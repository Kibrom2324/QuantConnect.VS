"""
APEX LEAN Alpha Bridge — services/lean_alpha/main.py

Fixes implemented in this file
───────────────────────────────
  Bug-B   Kafka consumer auto-commit = False + explicit commit after successful
          downstream publish.  Same pattern as signal_engine/main.py.

          Before fix: enable.auto.commit defaulted to True, meaning the Kafka
          broker was told the message was processed as soon as it was fetched,
          regardless of whether the LEAN backtest trigger or the downstream
          signal publish actually succeeded.  A crash in that window would
          silently drop the alpha event.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import structlog
from confluent_kafka import Consumer, KafkaError, Producer

from services.graceful_shutdown import GracefulShutdown

logger = structlog.get_logger(__name__)

KAFKA_BOOTSTRAP  = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
ALPHA_IN_TOPIC   = os.environ.get("LEAN_ALPHA_IN_TOPIC",  "apex.lean.triggers")
ALPHA_OUT_TOPIC  = os.environ.get("LEAN_ALPHA_OUT_TOPIC", "apex.signals.raw")
GROUP_ID         = os.environ.get("LEAN_ALPHA_GROUP_ID",  "apex-lean-alpha-v1")

LEAN_PROJECT_DIR = Path(
    os.environ.get("LEAN_PROJECT_DIR", str(Path(__file__).parent.parent.parent / "MyProject"))
)


class LeanAlphaBridge:
    """
    Listens for alpha trigger events on Kafka, runs the LEAN ensemble algorithm
    as a subprocess, parses the resulting signals, and republishes them to the
    raw signals topic.

    Bug-B FIX: manual Kafka commit — only after successful LEAN run + publish.
    """

    def __init__(self) -> None:
        self._shutdown  = GracefulShutdown()

        # Bug-B FIX 2026-02-27: enable.auto.commit MUST be False
        self._consumer  = Consumer({
            "bootstrap.servers":  KAFKA_BOOTSTRAP,
            "group.id":           GROUP_ID,
            "auto.offset.reset":  "latest",
            "enable.auto.commit": False,   # Bug-B FIX
        })
        self._producer = Producer({
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "acks": "all",
        })

    async def run(self) -> None:
        self._consumer.subscribe([ALPHA_IN_TOPIC])
        logger.info("lean_alpha_bridge_started", topic=ALPHA_IN_TOPIC)

        while not self._shutdown.is_shutdown:
            msg = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._consumer.poll(timeout=1.0)
            )
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    logger.error("kafka_consumer_error", error=str(msg.error()))
                continue

            await self._process(msg)

        self._consumer.close()
        self._producer.flush()
        logger.info("lean_alpha_bridge_stopped")

    async def _process(self, msg) -> None:
        """
        Bug-B FIX: Commit consumer offset only after LEAN run succeeds AND
        signals are flushed to Kafka.  If any step fails, the offset is NOT
        advanced — the container restart will reprocess the trigger.
        """
        try:
            trigger = json.loads(msg.value().decode("utf-8"))
        except Exception as e:
            logger.error("invalid_trigger_json", error=str(e))
            # Bad message: skip it
            self._consumer.commit(message=msg, asynchronous=False)
            return

        symbol   = trigger.get("symbol", "SPY")
        algo     = trigger.get("algorithm", "APEXEnsembleAlgorithm")
        run_date = trigger.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

        logger.info("lean_alpha_trigger_received", symbol=symbol, algo=algo, date=run_date)

        # Run LEAN
        signals = await self._run_lean(algo=algo, symbol=symbol, run_date=run_date)
        if signals is None:
            logger.error("lean_run_failed_not_committing", symbol=symbol)
            return  # Bug-B: do NOT commit on failure

        # Publish signals
        publish_ok = await self._publish_signals(signals, symbol)
        if not publish_ok:
            logger.error("signal_publish_failed_not_committing", symbol=symbol)
            return  # Bug-B: do NOT commit on failure

        # Bug-B FIX: commit only on full success
        self._consumer.commit(message=msg, asynchronous=False)
        logger.info("lean_alpha_committed", symbol=symbol, n_signals=len(signals))

    async def _run_lean(
        self, algo: str, symbol: str, run_date: str
    ) -> list[dict] | None:
        """Execute LEAN in subprocess and parse output signals."""
        cmd = [
            "dotnet", "run",
            "--project", str(LEAN_PROJECT_DIR / ".." / "Lean" / "Launcher"),
            "--algorithm-type-name", algo,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(LEAN_PROJECT_DIR),
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=300
            )
        except asyncio.TimeoutError:
            logger.error("lean_run_timeout", algo=algo)
            return None
        except Exception as e:
            logger.error("lean_run_subprocess_error", error=str(e))
            return None

        if proc.returncode != 0:
            logger.error(
                "lean_run_nonzero_exit",
                returncode=proc.returncode,
                stderr=stderr.decode("utf-8", errors="replace")[-1000:],
            )
            return None

        # Parse signals from stdout (LEAN writes JSON lines with "SIGNAL:" prefix)
        signals = []
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            if line.startswith("SIGNAL:"):
                try:
                    sig = json.loads(line[len("SIGNAL:"):].strip())
                    signals.append(sig)
                except json.JSONDecodeError:
                    pass

        logger.info("lean_run_complete", n_signals=len(signals), algo=algo)
        return signals

    async def _publish_signals(self, signals: list[dict], symbol: str) -> bool:
        """Produce all signals to Kafka and flush."""
        try:
            for sig in signals:
                payload = json.dumps({
                    **sig,
                    "source":  "lean_alpha",
                    "ts":      datetime.now(timezone.utc).isoformat(),
                }).encode()
                self._producer.produce(ALPHA_OUT_TOPIC, value=payload)
            self._producer.flush()
            return True
        except Exception as e:
            logger.error("signal_publish_error", symbol=symbol, error=str(e))
            return False


async def main() -> None:
    bridge = LeanAlphaBridge()
    await bridge.run()


if __name__ == "__main__":
    asyncio.run(main())
