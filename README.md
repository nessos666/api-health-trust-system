# API Health Trust System

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Checks](https://img.shields.io/badge/checks-pluggable-orange)

**Weighted Trust Score monitoring for any REST API.**

Don't just check if your API is "up" – know *how healthy* it is with a composite score from 0-100, computed from multiple weighted checks.

```
Trust Score: 87/100 [HEALTHY]
  [PASS] reachability: HTTP 200 in 142ms
  [PASS] latency: P50=135ms P95=289ms P99=412ms (47 samples)
  [PASS] token: Valid (2.3h old)
```

## Architecture

```
                    ┌─────────────┐
                    │ Your REST   │
                    │    API      │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴─────┐ ┌───┴───┐ ┌─────┴─────┐
        │Reachability│ │Latency│ │  Token    │
        │  Check     │ │ Check │ │  Check    │
        │ weight=0.2 │ │w=0.1  │ │ w=0.1     │
        └─────┬──────┘ └───┬───┘ └─────┬─────┘
              │            │            │
              └────────────┼────────────┘
                           │
                    ┌──────┴──────┐
                    │ Aggregator  │
                    │ Σ(score×w)  │──→ Trust Score (0-100)
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────┴─────┐ ┌───┴───┐ ┌─────┴─────┐
        │  JSON     │ │Telegram│ │  Stdout   │
        │  Export   │ │ Alert  │ │  Logging  │
        └───────────┘ └───────┘ └───────────┘
```

## Features

- **Weighted Trust Score** (0-100) – Each check contributes proportionally to its importance
- **Pluggable checks** – Use built-in checks or write your own by subclassing `BaseCheck`
- **Atomic JSON export** – External tools never read half-written files (`.tmp` + `os.replace`)
- **Telegram alerts** – Get notified when the score drops below threshold
- **Graceful shutdown** – Handles SIGTERM/SIGINT cleanly
- **orjson support** – Optional fast JSON for high-frequency monitoring
- **Zero config** – Works out of the box with sensible defaults

### Strategy Health Extension

Included as an optional extension for monitoring trading strategy performance:

- **CUSUM change-point detection** on equity curves (requires `ruptures`)
- **Sortino Ratio, Profit Factor, SQN** over 30/60/90 day windows
- **Hysteresis alarms** – Prevents alert flapping (requires N days confirmation)
- Multi-window trend analysis (improving / stable / degrading)

## Quick Start

### 1. Install

```bash
pip install requests
# Optional: pip install orjson numpy pandas ruptures
```

### 2. Run

```python
import asyncio
from api_health import HealthScanner
from api_health.checks import ReachabilityCheck, LatencyCheck

async def main():
    scanner = HealthScanner(
        checks=[
            ReachabilityCheck("https://api.example.com/health"),
            LatencyCheck("https://api.example.com/health"),
        ],
        output_path="/tmp/api_health.json",
    )
    result = await scanner.run()
    print(f"Trust Score: {result.trust_score:.0f}/100 [{result.status}]")

asyncio.run(main())
```

### 3. Run as daemon

```python
# Runs checks every 5 minutes, writes JSON, handles SIGTERM
await scanner.run_forever()
```

## Trust Score Levels

| Score | Status | Action |
|-------|--------|--------|
| 80-100 | HEALTHY | All systems operational |
| 50-79 | DEGRADED | Investigate before relying on the API |
| 0-49 | CRITICAL | Do NOT use the API for critical operations |

## Built-in Checks

| Check | What it measures | Default weight |
|-------|-----------------|----------------|
| `ReachabilityCheck` | HTTP response code | 0.20 |
| `LatencyCheck` | P50/P95/P99 response time | 0.10 |
| `TokenCheck` | JWT/auth token validity and age | 0.10 |

## Writing Custom Checks

```python
from api_health.checks import BaseCheck
from api_health import HealthCheck

class DatabaseCheck(BaseCheck):
    name = "database"
    weight = 0.25

    async def run(self, context: dict) -> HealthCheck:
        try:
            # Your check logic here
            response_time = ping_database()
            if response_time < 100:
                return HealthCheck(self.name, True, 1.0, f"OK ({response_time}ms)")
            return HealthCheck(self.name, False, 0.5, f"Slow ({response_time}ms)")
        except Exception as e:
            return HealthCheck(self.name, False, 0.0, f"Error: {e}")
```

## JSON Output Format

```json
{
  "timestamp": "2026-04-07T14:30:00+00:00",
  "trust_score": 87.5,
  "status": "HEALTHY",
  "checks": [
    {
      "name": "reachability",
      "passed": true,
      "score": 1.0,
      "detail": "HTTP 200 in 142ms",
      "latency_ms": 142.3
    }
  ],
  "alerts": [],
  "latency": {
    "count": 47,
    "p50": 135.2,
    "p95": 289.1,
    "p99": 412.7,
    "min": 98.4,
    "max": 523.1,
    "avg": 156.8
  }
}
```

## Integration

The JSON output can be consumed by:
- **n8n / Node-RED** – Read file, trigger alerts on state changes
- **Grafana** – JSON data source or Prometheus exporter
- **cron scripts** – Parse JSON and send custom notifications
- **systemd** – Run as a service with automatic restarts

## Project Structure

```
api-health-trust-system/
  api_health/
    __init__.py          # Public API
    core.py              # HealthCheck + APIHealthResult dataclasses
    scanner.py           # Main scanner with Trust Score aggregation
    config.py            # Default constants
    checks/
      base.py            # BaseCheck abstract class
      reachability.py    # HTTP reachability check
      latency.py         # P50/P95/P99 latency tracking
      token.py           # JWT/auth token validation
    alerting/
      telegram.py        # Telegram bot integration
      json_export.py     # Atomic JSON file export
  extensions/
    strategy_health/     # Signal decay detection (CUSUM, Sortino, SQN)
  examples/
    basic_usage.py       # Minimal example
    custom_check.py      # Write your own checks
    telegram_setup.py    # Telegram integration
```

## License

MIT License. See [LICENSE](LICENSE).
