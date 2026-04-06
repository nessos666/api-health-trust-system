"""Core data structures for API Health Trust System."""

from __future__ import annotations

import json as _json
import os
from dataclasses import dataclass

try:
    import orjson as _orjson

    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False


def json_loads(data: str | bytes) -> dict:
    """Parse JSON (orjson if available)."""
    return _orjson.loads(data) if _HAS_ORJSON else _json.loads(data)


def json_write(path: str, data: dict) -> None:
    """Atomic JSON write via .tmp + os.replace().

    Ensures external readers never see a half-written file.
    """
    tmp = str(path) + ".tmp"
    if _HAS_ORJSON:
        with open(tmp, "wb") as f:
            f.write(_orjson.dumps(data, option=_orjson.OPT_INDENT_2))
    else:
        with open(tmp, "w") as f:
            _json.dump(data, f, indent=2)
    os.replace(tmp, str(path))


@dataclass
class HealthCheck:
    """Result of a single health check."""

    name: str
    passed: bool
    score: float  # 0.0 - 1.0
    detail: str
    latency_ms: float = 0.0


@dataclass
class APIHealthResult:
    """Aggregated result of all health checks."""

    timestamp: str
    trust_score: float
    status: str
    checks: list[HealthCheck]
    alerts: list[str]

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "trust_score": round(self.trust_score, 1),
            "status": self.status,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "score": round(c.score, 2),
                    "detail": c.detail,
                    "latency_ms": round(c.latency_ms, 1),
                }
                for c in self.checks
            ],
            "alerts": self.alerts,
        }

    def to_json_file(self, path: str) -> None:
        """Atomically write result to JSON file."""
        json_write(path, self.to_dict())
