"""
APEX Local LLM Sentiment Agent — services/llm_agent/main.py

Uses a locally running Ollama model (qwen2.5:14b by default) to generate
per-symbol sentiment scores.  Scores are written to Redis and optionally
published to Kafka so the EnsembleScorer can include them.

Architecture:
  Ollama (localhost:11434)
      ↓  OpenAI-compatible /v1/chat/completions
  LLMSentimentAgent.run()
      ↓  writes Redis key  apex:llm:sentiment:{SYMBOL}  (TTL 10 min)
      ↓  publishes Kafka   apex.signals.sentiment

Configuration (env vars):
  OLLAMA_HOST          Ollama base URL           (default: http://localhost:11434)
  OLLAMA_MODEL         Model to use              (default: qwen2.5:14b)
  OLLAMA_TIMEOUT       Request timeout seconds   (default: 60)
  LLM_SCAN_INTERVAL    Seconds between full scans (default: 300)
  LLM_SYMBOLS          Comma-separated symbols   (default: top-20 QQQ)
  REDIS_HOST           Redis host                (default: localhost)
  REDIS_PORT           Redis port                (default: 16379)
  KAFKA_BOOTSTRAP_SERVERS Kafka brokers          (default: localhost:9094)
  LLM_SENTIMENT_TOPIC  Kafka topic for scores    (default: apex.signals.sentiment)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
import redis

logger = logging.getLogger("apex.llm_agent")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ── Configuration ──────────────────────────────────────────────────────────────
OLLAMA_HOST     = os.getenv("OLLAMA_HOST",    "http://localhost:11434")
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL",   "qwen2.5:14b")
OLLAMA_TIMEOUT  = int(os.getenv("OLLAMA_TIMEOUT", "60"))

SCAN_INTERVAL   = int(os.getenv("LLM_SCAN_INTERVAL", "300"))   # seconds
REDIS_HOST      = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT      = int(os.getenv("REDIS_PORT", "16379"))         # host-mapped port
REDIS_TTL       = int(os.getenv("LLM_REDIS_TTL", "600"))        # 10 min

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9094")
SENTIMENT_TOPIC = os.getenv("LLM_SENTIMENT_TOPIC", "apex.signals.sentiment")
REDIS_KEY_PREFIX = "apex:llm:sentiment:"

DEFAULT_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META",
    "TSLA", "GOOGL", "AVGO", "ASML", "AMD",
    "QCOM", "NFLX", "ADBE", "CSCO", "INTC",
    "SPY",  "QQQ",  "PLTR", "CRM",  "NOW",
]

SYMBOLS: list[str] = [
    s.strip()
    for s in os.getenv("LLM_SYMBOLS", ",".join(DEFAULT_SYMBOLS)).split(",")
    if s.strip()
]

# ── Prompt template ────────────────────────────────────────────────────────────
SENTIMENT_PROMPT = """\
You are a quantitative finance analyst with expertise in US equity markets.

Today is {date}. Analyze the current market sentiment for the stock ticker: {symbol}

Consider:
- Recent price momentum and trend
- Sector conditions and macro environment
- Any notable news or catalysts for this ticker

Return ONLY a valid JSON object with exactly these fields:
{{
  "sentiment": <float between -1.0 (strong sell) and 1.0 (strong buy)>,
  "confidence": <float between 0.0 and 1.0>,
  "reasoning": "<one sentence max>"
}}

Do not include any text outside the JSON object."""


class OllamaClient:
    """Thin async wrapper around Ollama's OpenAI-compatible chat endpoint."""

    def __init__(self) -> None:
        self._base = OLLAMA_HOST.rstrip("/")
        self._model = OLLAMA_MODEL
        self._client = httpx.AsyncClient(timeout=OLLAMA_TIMEOUT)

    async def chat(self, prompt: str) -> str:
        """Send a chat message and return the assistant's text response."""
        url = f"{self._base}/v1/chat/completions"
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 200,
            "stream": False,
        }
        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()

    async def is_available(self) -> bool:
        """Check if Ollama is reachable and model is loaded."""
        try:
            resp = await self._client.get(f"{self._base}/api/tags", timeout=5.0)
            if resp.status_code != 200:
                return False
            tags = resp.json()
            names = [m["name"] for m in tags.get("models", [])]
            return any(self._model.split(":")[0] in n for n in names)
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()


class LLMSentimentAgent:
    """
    Periodically queries the local Ollama model for each symbol's sentiment
    and publishes the score to Redis + Kafka.
    """

    def __init__(self) -> None:
        self._ollama = OllamaClient()
        self._redis: Optional[redis.Redis] = None
        self._producer = None   # Kafka producer — optional, lazy init

    # ── Redis ──────────────────────────────────────────────────────────────────

    def _get_redis(self) -> Optional[redis.Redis]:
        if self._redis is not None:
            try:
                self._redis.ping()
                return self._redis
            except Exception:
                self._redis = None

        try:
            r = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                socket_timeout=2,
                decode_responses=True,
            )
            r.ping()
            self._redis = r
            logger.info("Redis connected at %s:%d", REDIS_HOST, REDIS_PORT)
            return self._redis
        except Exception as exc:
            logger.warning("Redis unavailable: %s", exc)
            return None

    def _write_redis(self, symbol: str, score: float, confidence: float, reasoning: str) -> None:
        r = self._get_redis()
        if r is None:
            return
        record = json.dumps({
            "symbol":     symbol,
            "sentiment":  score,
            "confidence": confidence,
            "reasoning":  reasoning,
            "ts":         datetime.now(timezone.utc).isoformat(),
            "model":      OLLAMA_MODEL,
        })
        r.setex(f"{REDIS_KEY_PREFIX}{symbol}", REDIS_TTL, record)
        logger.debug("Redis  apex:llm:sentiment:%s = %.3f (conf=%.2f)", symbol, score, confidence)

    # ── Kafka ──────────────────────────────────────────────────────────────────

    def _get_producer(self):
        if self._producer is not None:
            return self._producer
        try:
            from confluent_kafka import Producer
            self._producer = Producer({
                "bootstrap.servers": KAFKA_BOOTSTRAP,
                "acks": "1",
            })
            logger.info("Kafka producer connected to %s", KAFKA_BOOTSTRAP)
        except Exception as exc:
            logger.warning("Kafka unavailable: %s — scores will only go to Redis", exc)
        return self._producer

    def _publish_kafka(self, symbol: str, score: float, confidence: float) -> None:
        producer = self._get_producer()
        if producer is None:
            return
        try:
            msg = json.dumps({
                "symbol":      symbol,
                "llm_score":   score,
                "confidence":  confidence,
                "ts":          datetime.now(timezone.utc).isoformat(),
                "model":       OLLAMA_MODEL,
                "source":      "ollama_local",
            }).encode()
            producer.produce(SENTIMENT_TOPIC, value=msg)
            producer.poll(0)  # non-blocking flush
        except Exception as exc:
            logger.warning("Kafka publish failed for %s: %s", symbol, exc)

    # ── Sentiment scoring ──────────────────────────────────────────────────────

    async def score_symbol(self, symbol: str) -> Optional[dict]:
        """Ask the LLM for sentiment on one symbol. Returns parsed result or None."""
        prompt = SENTIMENT_PROMPT.format(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            symbol=symbol,
        )
        try:
            raw = await self._ollama.chat(prompt)
            # Extract JSON — model may include markdown fences
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)
            sentiment  = max(-1.0, min(1.0,  float(result["sentiment"])))
            confidence = max(0.0,  min(1.0,  float(result["confidence"])))
            reasoning  = str(result.get("reasoning", ""))
            return {"sentiment": sentiment, "confidence": confidence, "reasoning": reasoning}
        except json.JSONDecodeError as e:
            logger.warning("JSON parse failed for %s: %s | raw=%r", symbol, e, raw[:200])
            return None
        except Exception as e:
            logger.error("score_symbol failed for %s: %s", symbol, e)
            return None

    # ── Main loop ──────────────────────────────────────────────────────────────

    async def run_once(self) -> dict[str, float]:
        """Score all symbols once. Returns {symbol: sentiment_score}."""
        scores: dict[str, float] = {}
        for symbol in SYMBOLS:
            result = await self.score_symbol(symbol)
            if result is None:
                logger.warning("No score for %s — skipping", symbol)
                continue

            sentiment  = result["sentiment"]
            confidence = result["confidence"]
            reasoning  = result["reasoning"]

            self._write_redis(symbol, sentiment, confidence, reasoning)
            self._publish_kafka(symbol, sentiment, confidence)
            scores[symbol] = sentiment

            logger.info(
                "scored %-6s  sentiment=%+.3f  conf=%.2f  reason=%s",
                symbol, sentiment, confidence, reasoning[:60],
            )
            # small delay to avoid overwhelming Ollama
            await asyncio.sleep(0.5)

        return scores

    async def run(self) -> None:
        """Run indefinitely, scoring all symbols every SCAN_INTERVAL seconds."""
        logger.info(
            "LLM Sentiment Agent starting | model=%s  symbols=%d  interval=%ds",
            OLLAMA_MODEL, len(SYMBOLS), SCAN_INTERVAL,
        )

        # Verify Ollama is reachable
        if not await self._ollama.is_available():
            logger.error(
                "Ollama not reachable at %s or model %s not found. "
                "Run: ollama pull %s",
                OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_MODEL,
            )
            sys.exit(1)

        logger.info("Ollama OK — model %s is available", OLLAMA_MODEL)

        while True:
            t0 = time.monotonic()
            try:
                scores = await self.run_once()
                elapsed = time.monotonic() - t0
                logger.info(
                    "Scan complete: %d/%d symbols scored in %.1fs",
                    len(scores), len(SYMBOLS), elapsed,
                )
            except Exception as exc:
                logger.exception("Unexpected error in run_once: %s", exc)

            # Wait for next scan
            sleep_secs = max(0, SCAN_INTERVAL - (time.monotonic() - t0))
            logger.info("Next scan in %.0fs", sleep_secs)
            await asyncio.sleep(sleep_secs)

        await self._ollama.close()


async def main() -> None:
    agent = LLMSentimentAgent()
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
