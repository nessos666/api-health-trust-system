"""Reachability check – Is the API endpoint responding with HTTP 200?"""

from __future__ import annotations

import time

import requests

from api_health.checks.base import BaseCheck
from api_health.core import HealthCheck


class ReachabilityCheck(BaseCheck):
    """Verifies that the target API is reachable and responding."""

    name = "reachability"
    weight = 0.20

    def __init__(self, url: str, timeout: float = 15.0) -> None:
        self.url = url
        self.timeout = timeout

    async def run(self, context: dict) -> HealthCheck:
        try:
            t0 = time.monotonic()
            resp = requests.get(self.url, timeout=self.timeout)
            latency = (time.monotonic() - t0) * 1000

            if resp.status_code < 400:
                context["reachability_latency_ms"] = latency
                return HealthCheck(
                    self.name,
                    True,
                    1.0,
                    f"HTTP {resp.status_code} in {latency:.0f}ms",
                    latency,
                )
            return HealthCheck(
                self.name,
                False,
                0.0,
                f"HTTP {resp.status_code}",
                latency,
            )
        except requests.exceptions.Timeout:
            return HealthCheck(self.name, False, 0.0, f"Timeout (>{self.timeout}s)")
        except requests.exceptions.ConnectionError:
            return HealthCheck(self.name, False, 0.0, "Connection refused")
        except Exception as e:
            return HealthCheck(self.name, False, 0.0, f"Error: {e}")
