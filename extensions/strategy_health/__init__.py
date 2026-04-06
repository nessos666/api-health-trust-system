"""Strategy Health Extension – Signal decay detection with CUSUM and Sortino."""

from extensions.strategy_health.monitor import StrategyMetrics, compute_health
from extensions.strategy_health.hysteresis import HealthState, apply_hysteresis

__all__ = ["StrategyMetrics", "compute_health", "HealthState", "apply_hysteresis"]
