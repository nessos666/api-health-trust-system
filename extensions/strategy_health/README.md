# Strategy Health Extension

Monitors whether a trading strategy's edge is still alive using statistical metrics and change-point detection.

## Metrics (30/60/90 day windows)

| Metric | Description | Weight |
|--------|-------------|--------|
| Sortino Ratio | Risk-adjusted return (downside only) | 25% |
| Profit Factor | Sum of wins / sum of losses | 25% |
| Max Drawdown | Percentage from equity peak | 20% |
| Win Rate | EMA over last 20 trades | 15% |
| SQN | System Quality Number (Van Tharp) | 15% |

## Features

- **CUSUM Change-Point Detection** – Uses the `ruptures` library to detect regime changes in the equity curve
- **Multi-Window Analysis** – Compares 30-day vs 60-day vs 90-day performance to detect trends
- **Hysteresis Alarms** – Requires N consecutive days of confirmation before changing alert level (prevents flapping)
- **Three alert levels**: green (healthy), yellow (warning), red (critical)

## Requirements

```
numpy
pandas
ruptures  # optional, for CUSUM detection
```

## Usage

```python
import pandas as pd
from extensions.strategy_health import compute_health, apply_hysteresis, load_states

# Load your trade data
trades = pd.DataFrame({
    "timestamp": [...],
    "pnl_dollar": [...],
})

# Compute metrics
metrics = compute_health("my_strategy", trades)
print(f"Health Score: {metrics.health_score:.0f} ({metrics.level})")
print(f"Sortino: {metrics.sortino:.2f} | PF: {metrics.profit_factor:.2f}")
print(f"Trend: {metrics.trend}")

if metrics.change_point_recent:
    print("WARNING: Regime change detected in last 5 days!")
```
