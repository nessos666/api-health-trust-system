"""Strategy Health Monitor – Detects signal decay in trading strategies.

Inspired by Renaissance Technologies (signal decay monitoring) and
Two Sigma (regime detection).

Metrics per strategy (30/60/90 day windows):
  - Sortino Ratio (downside volatility only)
  - Profit Factor (gains / losses)
  - Max Drawdown (percent from peak)
  - Win Rate (EMA over last 20 trades)
  - SQN (System Quality Number, Van Tharp)

Uses CUSUM change-point detection on the equity curve to identify
regime changes in strategy performance.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCORE_GREEN = 70
SCORE_YELLOW = 40
MIN_TRADES = 20


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class StrategyMetrics:
    """Computed health metrics for a single strategy."""

    strategy: str
    total_trades: int = 0
    # 30-day window (basis for health score)
    sortino: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    sqn: float = 0.0
    health_score: float = 0.0
    level: str = "green"
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    change_point_recent: bool = False
    # 60-day window
    sortino_60: float = 0.0
    profit_factor_60: float = 0.0
    win_rate_60: float = 0.0
    max_drawdown_pct_60: float = 0.0
    # 90-day window
    sortino_90: float = 0.0
    profit_factor_90: float = 0.0
    win_rate_90: float = 0.0
    max_drawdown_pct_90: float = 0.0
    # Trend (improving / stable / degrading)
    trend: str = "stable"


# ---------------------------------------------------------------------------
# Metric functions
# ---------------------------------------------------------------------------


def _sortino_ratio(pnl: pd.Series, target: float = 0.0) -> float:
    """Sortino Ratio: only penalises downside volatility."""
    if len(pnl) < 5:
        return 0.0
    excess = pnl - target
    downside = pnl[pnl < target]
    if len(downside) < 2:
        return 5.0
    downside_std = downside.std()
    if downside_std < 1e-8:
        return 5.0
    return float(np.clip(excess.mean() / downside_std, -5.0, 5.0))


def _profit_factor(pnl: pd.Series) -> float:
    """Profit Factor: sum of wins / sum of losses."""
    gains = pnl[pnl > 0].sum()
    losses = abs(pnl[pnl < 0].sum())
    if losses < 1e-8:
        return 10.0
    return float(np.clip(gains / losses, 0.0, 10.0))


def _max_drawdown_pct(pnl: pd.Series) -> float:
    """Max Drawdown as percentage from peak."""
    equity = pnl.cumsum()
    peak = equity.cummax()
    dd = peak - equity
    if peak.max() < 1e-8:
        return 0.0
    return float(dd.max() / max(peak.max(), 1.0) * 100)


def _win_rate_ema(results: pd.Series, span: int = 20) -> float:
    """Win rate as EMA over the last N trades."""
    wins = (results > 0).astype(float)
    if len(wins) < 3:
        return 0.0
    return float(wins.ewm(span=span, min_periods=3).mean().iloc[-1] * 100)


def _sqn(pnl: pd.Series) -> float:
    """System Quality Number (Van Tharp): sqrt(N) * mean(R) / std(R)."""
    if len(pnl) < 10:
        return 0.0
    std = pnl.std()
    if std < 1e-8:
        return 0.0
    n = min(len(pnl), 100)
    recent = pnl.tail(n)
    return float(np.sqrt(n) * recent.mean() / recent.std())


def _normalize(value: float, low: float, high: float) -> float:
    """Normalize value to 0-100 scale."""
    if high <= low:
        return 50.0
    return float(np.clip((value - low) / (high - low) * 100, 0.0, 100.0))


def _detect_change_points(daily_pnl: np.ndarray) -> bool:
    """CUSUM change-point detection on equity curve.

    Requires the ``ruptures`` library (optional dependency).
    Returns True if a change point was detected in the last 5 days.
    """
    try:
        import ruptures
    except ImportError:
        return False

    if len(daily_pnl) < 15:
        return False

    algo = ruptures.Pelt(model="rbf").fit(daily_pnl.reshape(-1, 1))
    change_points = algo.predict(pen=10)

    n = len(daily_pnl)
    return any(n - 5 <= cp < n for cp in change_points[:-1])


def _pnl_window(
    pnl_all: pd.Series,
    timestamps: pd.Series,
    days: int,
) -> pd.Series:
    """Return PnL series for the last N days."""
    now = pd.Timestamp.now(tz="UTC")
    cutoff = now - pd.Timedelta(days=days)
    mask = pd.to_datetime(timestamps, format="ISO8601", utc=True) > cutoff
    return pnl_all[mask] if mask.any() else pd.Series(dtype=float)


def _trend_arrow(val_30: float, val_90: float, threshold: float = 0.05) -> str:
    """Return trend indicator."""
    if val_90 == 0:
        return "stable"
    change = (val_30 - val_90) / abs(val_90)
    if change > threshold:
        return "improving"
    if change < -threshold:
        return "degrading"
    return "stable"


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------


def compute_health(
    strategy: str,
    trades_df: pd.DataFrame,
    pnl_column: str = "pnl_dollar",
    timestamp_column: str = "timestamp",
) -> StrategyMetrics:
    """Compute health metrics for a strategy over 30/60/90 day windows.

    Args:
        strategy: Strategy name/identifier.
        trades_df: DataFrame with at least ``pnl_column`` and ``timestamp_column``.
        pnl_column: Column name for PnL values.
        timestamp_column: Column name for trade timestamps.

    Returns:
        A StrategyMetrics dataclass with all computed values.
    """
    metrics = StrategyMetrics(strategy=strategy)

    if trades_df.empty or len(trades_df) < MIN_TRADES:
        metrics.level = "gray"
        return metrics

    pnl_all = trades_df[pnl_column].astype(float)
    timestamps = trades_df[timestamp_column]

    metrics.total_trades = len(trades_df)
    metrics.total_pnl = float(pnl_all.sum())
    metrics.avg_pnl = float(pnl_all.mean())

    # --- 30-day window (basis for health score) ---
    pnl_30 = _pnl_window(pnl_all, timestamps, 30)
    if pnl_30.empty:
        pnl_30 = pnl_all.tail(30)

    metrics.sortino = _sortino_ratio(pnl_30)
    metrics.profit_factor = _profit_factor(pnl_30)
    metrics.max_drawdown_pct = _max_drawdown_pct(pnl_30)
    metrics.win_rate = _win_rate_ema(pnl_all)
    metrics.sqn = _sqn(pnl_all)

    # --- 60-day window ---
    pnl_60 = _pnl_window(pnl_all, timestamps, 60)
    if not pnl_60.empty:
        metrics.sortino_60 = _sortino_ratio(pnl_60)
        metrics.profit_factor_60 = _profit_factor(pnl_60)
        metrics.win_rate_60 = _win_rate_ema(pnl_60)
        metrics.max_drawdown_pct_60 = _max_drawdown_pct(pnl_60)

    # --- 90-day window ---
    pnl_90 = _pnl_window(pnl_all, timestamps, 90)
    if not pnl_90.empty:
        metrics.sortino_90 = _sortino_ratio(pnl_90)
        metrics.profit_factor_90 = _profit_factor(pnl_90)
        metrics.win_rate_90 = _win_rate_ema(pnl_90)
        metrics.max_drawdown_pct_90 = _max_drawdown_pct(pnl_90)

    # --- Trend ---
    if not pnl_90.empty:
        metrics.trend = _trend_arrow(metrics.sortino, metrics.sortino_90)
    elif not pnl_60.empty:
        metrics.trend = _trend_arrow(metrics.sortino, metrics.sortino_60)

    # --- Health Score (weighted) ---
    sortino_n = _normalize(metrics.sortino, -1.0, 3.0)
    pf_n = _normalize(metrics.profit_factor, 0.5, 3.0)
    dd_n = _normalize(20.0 - metrics.max_drawdown_pct, 0.0, 20.0)
    wr_n = _normalize(metrics.win_rate, 30.0, 80.0)
    sqn_n = _normalize(metrics.sqn, 0.0, 5.0)

    metrics.health_score = (
        sortino_n * 0.25 + pf_n * 0.25 + dd_n * 0.20 + wr_n * 0.15 + sqn_n * 0.15
    )

    # Level
    if metrics.health_score >= SCORE_GREEN:
        metrics.level = "green"
    elif metrics.health_score >= SCORE_YELLOW:
        metrics.level = "yellow"
    else:
        metrics.level = "red"

    # CUSUM change-point detection
    if len(pnl_all) >= 15:
        metrics.change_point_recent = _detect_change_points(pnl_all.values)

    return metrics
