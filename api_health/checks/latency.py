"""Latency check – Tracks P50/P95/P99 response times."""

from __future__ import annotations

import time
from collections import deque

import requests

from api_health.checks.base import BaseCheck
from api_health.core import HealthCheck

# Default thresholds
WARN_MS = 2000
CRIT_MS = 5000


class LatencyCheck(BaseCheck):
    """Measures API response latency and tracks percentiles.

    Maintains a rolling window of the last ``max_samples`` measurements.
    Reports P50, P95, P99, min, max, and average.
    """

    name = "latency"
    weight = 0.10

    def __init__(
        self,
        url: str,
        warn_ms: float = WARN_MS,
        crit_ms: float = CRIT_MS,
        max_samples: int = 100,
        timeout: float = 15.0,
    ) -> None:
        self.url = url
        self.warn_ms = warn_ms
        self.crit_ms = crit_ms
        self.timeout = timeout
        self._samples: deque[float] = deque(maxlen=max_samples)

    def _percentile(self, pct: float) -> float:
        if not self._samples:
            return 0.0
        data = sorted(self._samples)
        idx = int(len(data) * pct / 100)
        return data[min(idx, len(data) - 1)]

    def get_stats(self) -> dict:
        """Return current latency statistics."""
        if not self._samples:
            return {
                "count": 0,
                "p50": 0,
                "p95": 0,
                "p99": 0,
                "min": 0,
                "max": 0,
                "avg": 0,
            }
        return {
            "count": len(self._samples),
            "p50": round(self._percentile(50), 1),
            "p95": round(self._percentile(95), 1),
            "p99": round(self._percentile(99), 1),
            "min": round(min(self._samples), 1),
            "max": round(max(self._samples), 1),
            "avg": round(sum(self._samples) / len(self._samples), 1),
        }

    async def run(self, context: dict) -> HealthCheck:
        try:
            t0 = time.monotonic()
            requests.get(self.url, timeout=self.timeout)
            latency = (time.monotonic() - t0) * 1000
            self._samples.append(latency)
        except Exception:
            pass

        stats = self.get_stats()
        context["latency_stats"] = stats

        if stats["count"] == 0:
            return HealthCheck(self.name, True, 1.0, "No measurements yet")

        p95 = stats["p95"]
        detail = f"P50={stats['p50']:.0f}ms P95={p95:.0f}ms P99={stats['p99']:.0f}ms ({stats['count']} samples)"

        if p95 <= self.warn_ms:
            return HealthCheck(self.name, True, 1.0, detail, p95)
        if p95 <= self.crit_ms:
            return HealthCheck(self.name, False, 0.5, detail, p95)
        return HealthCheck(self.name, False, 0.0, detail, p95)
