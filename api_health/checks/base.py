"""Base class for all health checks.

Subclass this to create your own custom checks.
"""

from __future__ import annotations

import abc

from api_health.core import HealthCheck


class BaseCheck(abc.ABC):
    """Abstract base for a health check.

    Every check has a unique ``name`` and a ``weight`` (0.0 - 1.0) that
    determines its contribution to the composite Trust Score.

    Implement ``run()`` to execute the actual check logic.
    """

    name: str = "unnamed"
    weight: float = 0.1

    @abc.abstractmethod
    async def run(self, context: dict) -> HealthCheck:
        """Execute the check and return a HealthCheck result.

        Args:
            context: Shared state dict.  Checks can read cached data
                     (e.g. ``context["accounts"]``) or store results
                     for later checks.

        Returns:
            A :class:`HealthCheck` with ``passed``, ``score`` (0.0-1.0),
            and a human-readable ``detail`` string.
        """
        ...
