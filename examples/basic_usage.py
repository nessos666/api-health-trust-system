"""Basic usage example – Monitor any REST API with Trust Score."""

import asyncio
import logging

from api_health import HealthScanner
from api_health.checks import ReachabilityCheck, LatencyCheck

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


async def main() -> None:
    # Configure checks for your API
    scanner = HealthScanner(
        checks=[
            ReachabilityCheck("https://httpbin.org/get"),
            LatencyCheck("https://httpbin.org/get", warn_ms=1000, crit_ms=3000),
        ],
        output_path="/tmp/api_health.json",
        interval_seconds=60,
    )

    # Single check
    result = await scanner.run()
    print(f"Trust Score: {result.trust_score:.0f}/100 [{result.status}]")
    for check in result.checks:
        icon = "PASS" if check.passed else "FAIL"
        print(f"  [{icon}] {check.name}: {check.detail}")

    # Or run in a loop (Ctrl+C to stop):
    # await scanner.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
