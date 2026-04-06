"""JSON file exporter – Atomic writes for external readers."""

from __future__ import annotations

import logging

from api_health.core import APIHealthResult, json_write

logger = logging.getLogger("api_health.alerting")


class JsonExporter:
    """Exports health results to a JSON file atomically.

    External tools (n8n, Grafana, cron scripts) can safely read the
    output file at any time without encountering partial writes.

    Usage::

        exporter = JsonExporter("/tmp/api_health.json")
        exporter.export(result)
    """

    def __init__(self, path: str) -> None:
        self.path = path

    def export(self, result: APIHealthResult, extra: dict | None = None) -> None:
        """Write result to JSON file.

        Args:
            result: The health check result to export.
            extra: Optional additional data to include in the JSON.
        """
        data = result.to_dict()
        if extra:
            data.update(extra)
        json_write(self.path, data)
        logger.debug("Exported health JSON to %s", self.path)
