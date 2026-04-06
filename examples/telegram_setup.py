"""Example: Health monitoring with Telegram alerts."""

import asyncio
import logging
import os

from api_health import HealthScanner
from api_health.checks import ReachabilityCheck, LatencyCheck
from api_health.alerting import TelegramAlerter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


async def main() -> None:
    # Configure Telegram (set these environment variables)
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.")
        return

    alerter = TelegramAlerter(bot_token, chat_id, alert_threshold=80.0)

    scanner = HealthScanner(
        checks=[
            ReachabilityCheck("https://your-api.example.com/health"),
            LatencyCheck("https://your-api.example.com/health"),
        ],
        output_path="/tmp/api_health.json",
    )

    result = await scanner.run()
    print(f"Trust Score: {result.trust_score:.0f}/100 [{result.status}]")

    # Send alert if degraded
    if alerter.send(result):
        print("Telegram alert sent!")
    else:
        print("No alert needed (healthy).")


if __name__ == "__main__":
    asyncio.run(main())
