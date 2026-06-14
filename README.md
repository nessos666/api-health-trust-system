<p align="center">
  <h1 align="center">API Health Trust System</h1>
  <p align="center">
    <strong>Weighted Trust Score monitoring for any REST API — pluggable checks, composite 0-100 score, Telegram alerts, atomic JSON export.</strong>
  </p>
  <p align="center">
    <a href="#why">Why</a> · <a href="#architecture">Architecture</a> · <a href="#quick-start">Quick Start</a> · <a href="#trust-score">Trust Score</a> · <a href="#custom-checks">Custom Checks</a> · <a href="#strategy-health">Strategy Health</a>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue?logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/checks-pluggable-orange" alt="Pluggable checks">
  <img src="https://img.shields.io/badge/trust_score-0--100-red" alt="Trust Score">
  <img src="https://img.shields.io/badge/extensions-strategy_health-blueviolet" alt="Strategy health">
  <img src="https://img.shields.io/github/stars/nessos666/api-health-trust-system?style=social" alt="Stars">
</p>

---

## Why?

Most API health checks are binary — they tell you "up" or "down". But real-world API health is **not binary**:

| Scenario | Binary check says | Reality |
|----------|-------------------|---------|
| API responds in 3.5s (normally 200ms) | ✅ UP | ⚠️ Degraded — your orders are delayed |
| JWT expires in 2 minutes | ✅ UP | ⚠️ Imminent failure |
| Last bar data is 8 minutes old | ✅ UP | ⚠️ You're trading blind |
| Account drawdown reaches 75% | ✅ UP | 🚨 Risk limit breached |

**The API Health Trust System solves this** by computing a **continuous Trust Score (0-100)** from multiple weighted checks. Instead of "is it up?", you get "how healthy is it, right now?"

---

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
        │Reachability│ │Latency│ │  Custom   │
        │  Check    │ │ Check │ │  Check    │
        │ weight=0.2│ │w=0.1  │ │ w=any     │
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
        │  JSON     │ │Telegram│ │  Custom   │
        │  Export   │ │ Alert  │ │  Export   │
        │ (atomic)  │ │       │ │           │
        └───────────┘ └───────┘ └───────────┘
```

### Key design decisions

| Decision | Why |
|----------|-----|
| **Atomic JSON export** | Uses `.tmp` + `os.replace()` — external tools never read a half-written file |
| **Pluggable checks** | `BaseCheck` abstract class — bring your own checks for any service |
| **No framework lock-in** | Pure Python, single file core — no Flask, no FastAPI, no asyncio required |
| **Graceful shutdown** | Handles SIGTERM/SIGINT — CSV is flushed, JSON is finalized |

---

## Quick Start

### 1. Install

```bash
pip install requests
# Optional: pip install orjson numpy pandas ruptures
```

### 2. Use as a library

```python
import asyncio
from api_health import HealthScanner
from api_health.checks import ReachabilityCheck, LatencyCheck, TokenCheck

async def main():
    scanner = HealthScanner(
        checks=[
            ReachabilityCheck("https://api.example.com/health"),
            LatencyCheck("https://api.example.com/health"),
            TokenCheck("https://api.example.com/health"),
        ],
        output_path="/tmp/api_health.json",
    )

    # Single run
    result = await scanner.run()
    print(f"Trust Score: {result.trust_score:.0f}/100 [{result.status}]")

    # Or run forever (every 5 minutes, handles SIGTERM)
    await scanner.run_forever()

asyncio.run(main())
```

### 3. Run as daemon

```bash
python api_health/scanner.py          # Single pass
python api_health/scanner.py --watch  # Continuous monitoring
```

---

## Trust Score

### How it's computed

```
trust_score = Σ(score_i × weight_i) / Σ(weight_i) × 100
```

Where:
- `score_i` = 0.0 to 1.0 (check passes gives 1.0, fails gives 0.0, partial allowed)
- `weight_i` = importance of this check relative to others

### Levels

| Score | Status | Meaning |
|-------|--------|---------|
| **80-100** | HEALTHY | All systems operational. Trade with confidence. |
| **50-79** | DEGRADED | Some checks failing. Investigate before critical operations. |
| **0-49** | CRITICAL | Do NOT use the API. Fix issues first. |

### Built-in checks

| Check | What it measures | Default weight |
|-------|-----------------|----------------|
| `ReachabilityCheck` | HTTP response code (200 = pass) | 0.20 |
| `LatencyCheck` | P50/P95/P99 response time distribution | 0.10 |
| `TokenCheck` | JWT/auth token validity and remaining lifetime | 0.10 |

### Real example

```
Reachability: ✅ HTTP 200 (weight 0.20, score 1.0)
Latency:      ✅ P50=135ms P95=289ms (weight 0.10, score 1.0)
Token:        ⚠️ Expires in 4h, age 20h (weight 0.10, score 0.7)

Trust Score = (0.20×1.0 + 0.10×1.0 + 0.10×0.7) / (0.20+0.10+0.10) × 100
            = 0.37 / 0.40 × 100
            = 92.5 → HEALTHY
```

---

## Custom Checks

The framework is designed to be extended. Write a check for anything:

```python
from api_health.checks import BaseCheck
from api_health import HealthCheck
import psutil

class DiskSpaceCheck(BaseCheck):
    name = "disk_space"
    weight = 0.15

    async def run(self, context: dict) -> HealthCheck:
        usage = psutil.disk_usage("/")
        free_gb = usage.free / 1e9
        if free_gb > 10:
            return HealthCheck(self.name, True, 1.0, f"{free_gb:.1f}GB free")
        elif free_gb > 1:
            return HealthCheck(self.name, True, 0.5, f"Low: {free_gb:.1f}GB free")
        return HealthCheck(self.name, False, 0.0, f"Critical: {free_gb:.1f}GB free")
```

Register it:

```python
scanner = HealthScanner(checks=[ReachabilityCheck(url), DiskSpaceCheck()])
```

---

## JSON Output

Atomic export ensures external tools never read a partial file:

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

---

## Strategy Health Extension

Included as an optional extension for **trading strategy performance monitoring**:

| Feature | What it detects | Method |
|---------|----------------|--------|
| **CUSUM change-point** | Regime shifts in equity curve | `ruptures` PELT algorithm |
| **Sortino Ratio** | Risk-adjusted return (30/60/90 day windows) | Rolling calculation |
| **Profit Factor** | Gross profit / gross loss | Rolling calculation |
| **SQN** | System Quality Number | Rolling calculation |
| **Multi-window trend** | Improving / stable / degrading | Comparison across windows |
| **Hysteresis alarms** | Prevents alert flapping | Requires N days of confirmed signal |

```python
from extensions.strategy_health.monitor import StrategyHealthMonitor

monitor = StrategyHealthMonitor(
    trade_log_path="trades.csv",
    windows=[30, 60, 90],
    hysteresis_days=3,
)
report = await monitor.run()
print(f"Sortino: {report.sortino:.2f}, Trend: {report.trend}")
```

---

## Integration

The JSON output can be consumed by anything:

| Tool | How |
|------|-----|
| **n8n / Node-RED** | Read file → trigger webhook on trust_score < threshold |
| **Grafana** | JSON data source → dashboard with gauge + alerts |
| **cron** | Parse JSON → email/Slack/Telegram on state change |
| **systemd** | Run as service with `Restart=always` |
| **Telegram** | Built-in `TelegramAlert` notifier |
| **Custom** | It's atomic JSON. Parse it however you want. |

---

## Project Structure

```
api-health-trust-system/
├── api_health/
│   ├── __init__.py          # Public API — HealthScanner, HealthCheck
│   ├── core.py              # Dataclasses: HealthCheck, APIHealthResult
│   ├── scanner.py           # Main scanner + Trust Score aggregation
│   ├── config.py            # Default thresholds and constants
│   └── checks/
│       ├── base.py          # BaseCheck abstract class
│       ├── reachability.py  # HTTP 200 check
│       ├── latency.py       # P50/P95/P99 latency distribution
│       └── token.py         # JWT expiry and age validation
│   └── alerting/
│       ├── telegram.py      # Telegram bot alerts
│       └── json_export.py   # Atomic JSON file writer
├── extensions/
│   └── strategy_health/     # CUSUM, Sortino, PF, SQN monitoring
├── examples/
│   ├── basic_usage.py
│   ├── custom_check.py
│   └── telegram_setup.py
└── README.md
```

---

## Production Deployments

This system is running in production for:

- [TopStepX API Health Monitor](https://github.com/nessos666/topstepx-api-health-monitor) — 9 checks for TopStepX/ProjectX futures trading API
- [Rithmic API Health Monitor](https://github.com/nessos666/rithmic-api-health-monitor) — 10 checks for Rithmic futures data API

Both use the same Trust Score framework from this repo — see `examples/topstepx_scanner.py` and `examples/rithmic_scanner.py` for the complete configurations.

---

## Related

- [topstepx-api-health-monitor](https://github.com/nessos666/topstepx-api-health-monitor) — Production deployment: 9 checks for TopStepX
- [rithmic-api-health-monitor](https://github.com/nessos666/rithmic-api-health-monitor) — Production deployment: 10 checks for Rithmic
- [tv-watch-agent](https://github.com/nessos666/tv-watch-agent) — 24/7 TradingView chart surveillance

---

## Testing

```bash
python3 -m py_compile api_health/*.py
python3 -m py_compile api_health/checks/*.py
python3 -m py_compile api_health/alerting/*.py

# Run examples
python examples/basic_usage.py
```

---

## License

MIT — use it, modify it, integrate it.

<p align="center">
  <small>Don't just check if your API is up — know how healthy it is.<br>
  <strong>github.com/nessos666</strong></small>
</p>
