"""Example: Writing a custom health check."""

import asyncio
import logging
import psutil

from api_health import HealthScanner, HealthCheck
from api_health.checks import BaseCheck, ReachabilityCheck

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


class DiskSpaceCheck(BaseCheck):
    """Custom check: Alerts when disk usage exceeds threshold."""

    name = "disk_space"
    weight = 0.15

    def __init__(self, path: str = "/", warn_pct: float = 80.0) -> None:
        self.path = path
        self.warn_pct = warn_pct

    async def run(self, context: dict) -> HealthCheck:
        usage = psutil.disk_usage(self.path)
        pct = usage.percent

        if pct < self.warn_pct:
            return HealthCheck(self.name, True, 1.0, f"{pct:.1f}% used")
        elif pct < 95:
            return HealthCheck(self.name, False, 0.3, f"{pct:.1f}% used – low space!")
        else:
            return HealthCheck(self.name, False, 0.0, f"{pct:.1f}% used – CRITICAL!")


class MemoryCheck(BaseCheck):
    """Custom check: Alerts when RAM usage is too high."""

    name = "memory"
    weight = 0.10

    def __init__(self, warn_pct: float = 85.0) -> None:
        self.warn_pct = warn_pct

    async def run(self, context: dict) -> HealthCheck:
        mem = psutil.virtual_memory()
        pct = mem.percent

        if pct < self.warn_pct:
            return HealthCheck(
                self.name,
                True,
                1.0,
                f"{pct:.1f}% used ({mem.available // (1024**2)}MB free)",
            )
        else:
            return HealthCheck(self.name, False, 0.3, f"{pct:.1f}% used – high!")


async def main() -> None:
    scanner = HealthScanner(
        checks=[
            ReachabilityCheck("https://httpbin.org/get"),
            DiskSpaceCheck("/"),
            MemoryCheck(),
        ],
    )

    result = await scanner.run()
    print(f"Trust Score: {result.trust_score:.0f}/100 [{result.status}]")
    for check in result.checks:
        icon = "PASS" if check.passed else "FAIL"
        print(f"  [{icon}] {check.name}: {check.detail}")


if __name__ == "__main__":
    asyncio.run(main())
