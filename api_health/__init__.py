"""api-health-trust-system – Weighted Trust Score monitoring for any REST API."""

__version__ = "1.0.0"

from api_health.core import APIHealthResult, HealthCheck
from api_health.scanner import HealthScanner

__all__ = ["HealthCheck", "APIHealthResult", "HealthScanner"]
