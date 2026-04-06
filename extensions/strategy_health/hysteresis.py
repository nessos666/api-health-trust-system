"""Hysteresis alarm system – Prevents alert flapping.

Instead of immediately changing status when a threshold is crossed,
this module requires N consecutive days of confirmation before
transitioning between green/yellow/red states.

This prevents false alarms from single bad days and ensures that
recoveries are real before clearing alerts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

# Thresholds
SCORE_GREEN = 70
SCORE_YELLOW = 40
HYSTERESIS_BUFFER = 10
DAYS_TO_CONFIRM_DOWN = 3
DAYS_TO_CONFIRM_UP = 2


@dataclass
class HealthState:
    """Persisted state for hysteresis tracking."""

    level: str = "green"
    days_below: int = 0
    days_above: int = 0
    last_score: float = 100.0
    last_update: str = ""


def load_states(path: Path) -> dict[str, HealthState]:
    """Load persisted hysteresis states from JSON file."""
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return {k: HealthState(**v) for k, v in data.items()}
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def save_states(states: dict[str, HealthState], path: Path) -> None:
    """Save hysteresis states to JSON file."""
    path.write_text(json.dumps({k: asdict(v) for k, v in states.items()}, indent=2))


def apply_hysteresis(
    strategy: str,
    current_score: float,
    states: dict[str, HealthState],
) -> tuple[str, list[str]]:
    """Apply hysteresis logic to prevent alert flapping.

    Args:
        strategy: Strategy identifier.
        current_score: Current health score (0-100).
        states: Mutable dict of persisted states (will be updated in-place).

    Returns:
        Tuple of (final_level, alerts_list).
    """
    state = states.get(strategy, HealthState())
    alerts: list[str] = []
    today = datetime.now().strftime("%Y-%m-%d")

    if state.level == "green":
        if current_score < SCORE_GREEN:
            state.days_below += 1
            state.days_above = 0
            if state.days_below >= DAYS_TO_CONFIRM_DOWN:
                state.level = "yellow"
                alerts.append(
                    f"YELLOW: Score below {SCORE_GREEN} for {state.days_below} days"
                )
                state.days_below = 0
        else:
            state.days_below = 0

    elif state.level == "yellow":
        if current_score < SCORE_YELLOW:
            state.days_below += 1
            state.days_above = 0
            if state.days_below >= DAYS_TO_CONFIRM_UP:
                state.level = "red"
                alerts.append(
                    f"RED: Score below {SCORE_YELLOW} for {state.days_below} days!"
                )
                state.days_below = 0
        elif current_score > SCORE_GREEN + HYSTERESIS_BUFFER:
            state.days_above += 1
            state.days_below = 0
            if state.days_above >= DAYS_TO_CONFIRM_DOWN:
                state.level = "green"
                alerts.append("GREEN: Recovery confirmed")
                state.days_above = 0
        else:
            state.days_below = 0
            state.days_above = 0

    elif state.level == "red":
        if current_score > SCORE_YELLOW + HYSTERESIS_BUFFER:
            state.days_above += 1
            state.days_below = 0
            if state.days_above >= DAYS_TO_CONFIRM_UP:
                state.level = "yellow"
                alerts.append("YELLOW: Recovery detected, not yet green")
                state.days_above = 0
        else:
            state.days_above = 0

    state.last_score = current_score
    state.last_update = today
    states[strategy] = state

    return state.level, alerts
