"""Telegram alerting integration."""

from __future__ import annotations

import logging
import urllib.request
import json

from api_health.core import APIHealthResult

logger = logging.getLogger("api_health.alerting")


class TelegramAlerter:
    """Sends alerts to a Telegram chat when the Trust Score drops.

    Usage::

        alerter = TelegramAlerter(
            bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
            chat_id=os.environ["TELEGRAM_CHAT_ID"],
        )
        alerter.send(result)

    Only sends when ``trust_score < alert_threshold`` (default: 80).
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        alert_threshold: float = 80.0,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.alert_threshold = alert_threshold

    def send(self, result: APIHealthResult) -> bool:
        """Send an alert if trust score is below threshold.

        Returns True if a message was sent, False otherwise.
        """
        if result.trust_score >= self.alert_threshold:
            return False

        if result.trust_score < 50:
            icon = "CRITICAL"
        else:
            icon = "DEGRADED"

        failed = [c for c in result.checks if not c.passed]
        text = (
            f"API Health: {icon}\n"
            f"Trust Score: {result.trust_score:.0f}/100\n\n"
            f"Failed checks:\n" + "\n".join(f"  - {c.name}: {c.detail}" for c in failed)
        )

        return self._send_message(text)

    def _send_message(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        data = json.dumps({"chat_id": self.chat_id, "text": text}).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            return True
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)
            return False
