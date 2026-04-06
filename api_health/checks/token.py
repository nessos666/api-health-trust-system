"""Token check – Validates JWT/Auth token age and validity."""

from __future__ import annotations


from api_health.checks.base import BaseCheck
from api_health.core import HealthCheck


class TokenCheck(BaseCheck):
    """Checks if a JWT or API token is still valid.

    Provide a callable ``get_token_age_hours`` that returns the token age
    in hours, or None if no token exists.  Optionally provide
    ``refresh_token`` to auto-refresh when the token is close to expiry.
    """

    name = "token"
    weight = 0.10

    def __init__(
        self,
        get_token_age_hours: callable,
        refresh_token: callable | None = None,
        max_age_hours: float = 23.0,
    ) -> None:
        self._get_age = get_token_age_hours
        self._refresh = refresh_token
        self.max_age_hours = max_age_hours

    async def run(self, context: dict) -> HealthCheck:
        try:
            age_h = self._get_age()

            if age_h is None:
                if self._refresh:
                    await self._refresh()
                    return HealthCheck(self.name, True, 0.8, "Token created")
                return HealthCheck(self.name, False, 0.0, "No token available")

            if age_h < self.max_age_hours:
                return HealthCheck(self.name, True, 1.0, f"Valid ({age_h:.1f}h old)")

            if self._refresh:
                await self._refresh()
                return HealthCheck(self.name, True, 0.8, "Token refreshed")

            return HealthCheck(self.name, False, 0.0, f"Expired ({age_h:.1f}h old)")
        except Exception as e:
            return HealthCheck(self.name, False, 0.0, f"Error: {e}")
