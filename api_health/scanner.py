"""Health Scanner – Runs all checks and computes the weighted Trust Score."""

from __future__ import annotations

import asyncio
import logging
import signal
import traceback
from datetime import datetime, timezone
from typing import Optional

from api_health.checks.base import BaseCheck
from api_health.core import APIHealthResult, HealthCheck, json_write

logger = logging.getLogger("api_health")


class HealthScanner:
    """Orchestrates health checks and computes a composite Trust Score.

    Usage::

        scanner = HealthScanner(
            checks=[
                ReachabilityCheck("https://api.example.com/health"),
                LatencyCheck("https://api.example.com/health"),
            ],
            output_path="/tmp/api_health.json",
        )
        result = await scanner.run()
        print(f"Trust Score: {result.trust_score}")

    The Trust Score is a weighted sum of individual check scores (0-100):
      - 80-100 = HEALTHY
      - 50-79  = DEGRADED
      - 0-49   = CRITICAL
    """

    HEALTHY_THRESHOLD = 80
    DEGRADED_THRESHOLD = 50

    def __init__(
        self,
        checks: list[BaseCheck],
        output_path: Optional[str] = None,
        interval_seconds: int = 300,
    ) -> None:
        self.checks = checks
        self.output_path = output_path
        self.interval = interval_seconds
        self._last_result: Optional[APIHealthResult] = None
        self._running = True

    async def run(self) -> APIHealthResult:
        """Execute all checks once and return the aggregated result."""
        context: dict = {}
        results: list[HealthCheck] = []

        for check in self.checks:
            try:
                result = await check.run(context)
                results.append(result)
            except Exception as e:
                logger.warning("Check %s crashed: %s", check.name, e)
                results.append(
                    HealthCheck(
                        name=check.name,
                        passed=False,
                        score=0.0,
                        detail=f"CHECK CRASHED: {e}",
                    )
                )

        # Compute weighted Trust Score
        total_weight = sum(c.weight for c in self.checks)
        if total_weight > 0:
            trust_score = (
                sum(r.score * c.weight for r, c in zip(results, self.checks))
                / total_weight
                * 100
            )
        else:
            trust_score = 0.0

        # Determine status
        if trust_score >= self.HEALTHY_THRESHOLD:
            status = "HEALTHY"
        elif trust_score >= self.DEGRADED_THRESHOLD:
            status = "DEGRADED"
        else:
            status = "CRITICAL"

        alerts = [f"{c.name}: {c.detail}" for c in results if not c.passed]

        result = APIHealthResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            trust_score=trust_score,
            status=status,
            checks=results,
            alerts=alerts,
        )
        self._last_result = result

        # Write to file
        if self.output_path:
            data = result.to_dict()
            data["latency"] = context.get("latency_stats", {})
            json_write(self.output_path, data)

        return result

    async def run_forever(self) -> None:
        """Run checks in a loop until stopped.

        Handles SIGTERM/SIGINT for graceful shutdown.
        """
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown)

        logger.info(
            "Health Scanner started | interval=%ds | checks=%d | output=%s",
            self.interval,
            len(self.checks),
            self.output_path or "none",
        )

        # First check immediately
        try:
            result = await self.run()
            passed = sum(1 for c in result.checks if c.passed)
            logger.info(
                "Initial check: Trust=%d [%s] | %d/%d passed",
                result.trust_score,
                result.status,
                passed,
                len(result.checks),
            )
        except Exception as e:
            logger.error("Initial check failed: %s", e)
            traceback.print_exc()

        # Loop
        while self._running:
            await asyncio.sleep(self.interval)
            if not self._running:
                break
            try:
                result = await self.run()
                passed = sum(1 for c in result.checks if c.passed)
                logger.info(
                    "Trust=%d [%s] | %d/%d passed",
                    result.trust_score,
                    result.status,
                    passed,
                    len(result.checks),
                )
            except Exception as e:
                logger.error("Check cycle failed: %s", e)
                traceback.print_exc()

        logger.info("Health Scanner stopped.")

    def _shutdown(self) -> None:
        logger.info("Shutdown signal received.")
        self._running = False

    @property
    def last_result(self) -> Optional[APIHealthResult]:
        return self._last_result
