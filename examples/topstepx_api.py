#!/usr/bin/env python3
"""
topstep_api.py – TopStepX API Module
=====================================
Central API module for TopStepX / ProjectX trading platform.
Handles authentication, market data, order execution, and latency tracking.

Usage:
  from topstep_api import TopstepAPI
  api = TopstepAPI()
  api.place_order("long", sl_pts=5.0, tp_pts=15.0)
  api.close_position()

Required ENV variables (.env file or environment):
  PROJECTX_USERNAME     – Your TopStepX username
  PROJECTX_API_KEY      – Your TopStepX API key
  PROJECTX_CONTRACT_ID  – Contract ID (e.g. CON.F.US.MNQ.M26)
  PROJECTX_ACCOUNT_ID   – Your account ID
  PROJECTX_LIVE_TRADING – Set to "1" for live orders (default: simulation)
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:
    print("Please install: pip install requests")
    sys.exit(1)

# ── Load .env ────────────────────────────────────────────────────────────────


def _load_env() -> None:
    for env_file in (
        Path(__file__).resolve().parent / ".env",
        Path.cwd() / ".env",
    ):
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip("'\"")
            if k and k not in os.environ:
                os.environ[k] = v
        break


_load_env()

# ── Constants ────────────────────────────────────────────────────────────────

BASE_URL = os.environ.get("PROJECTX_BASE_URL", "https://api.topstepx.com")
USERNAME = os.environ.get("PROJECTX_USERNAME") or os.environ.get(
    "PROJECTX_USER_NAME", ""
)
API_KEY = os.environ.get("PROJECTX_API_KEY", "")
CONTRACT_ID = os.environ.get("PROJECTX_CONTRACT_ID", "CON.F.US.MNQ.M26")
ACCOUNT_ID = int(os.environ.get("PROJECTX_ACCOUNT_ID", "0"))

TICK_SIZE = 0.25  # MNQ
TICK_VALUE = 0.50  # $ per tick MNQ

# Order types
ORDER_MARKET = 2
ORDER_LIMIT = 1
ORDER_STOP = 4

# Order sides
SIDE_BID = 0  # Buy / Long
SIDE_ASK = 1  # Sell / Short


# ── API Client ──────────────────────────────────────────────────────────────


@dataclass
class TopstepAPI:
    """
    Central API module for TopStepX.

    account_id:  which account to trade on
    contract_id: which contract (default: MNQ from .env)
    live:        True = real orders | False = simulation (log only)
    """

    account_id: int = ACCOUNT_ID
    contract_id: str = CONTRACT_ID
    live: bool = field(
        default_factory=lambda: (
            os.environ.get("PROJECTX_LIVE_TRADING", "").strip().lower()
            in ("1", "true", "yes")
        )
    )

    _token: str = field(default="", init=False, repr=False)
    _token_time: Optional[dt.datetime] = field(default=None, init=False, repr=False)
    _last_latency_ms: float = field(default=0.0, init=False, repr=False)
    _latency_history: list = field(default_factory=list, init=False, repr=False)
    _latency_by_endpoint: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not USERNAME or not API_KEY:
            raise RuntimeError(
                "PROJECTX_USERNAME and PROJECTX_API_KEY must be set in .env or environment."
            )

    # ── HTTP with Retry + Latency ──────────────────────────────────────────

    def _request_with_retry(
        self,
        url: str,
        json: dict,
        timeout: int = 15,
        retries: int = 2,
        backoff: float = 1.0,
        endpoint: str = "unknown",
    ) -> requests.Response:
        """HTTP POST with retry logic and latency measurement.

        - Measures latency per request (stored in _latency_history + _latency_by_endpoint)
        - On ConnectionError, Timeout, HTTP 5xx: retry with exponential backoff
        - On HTTP 4xx: raise immediately (no retry on auth errors)
        - Max 2 retries = 3 attempts total
        """
        last_exc: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                t0 = time.monotonic()
                r = requests.post(
                    url, json=json, headers=self._headers(), timeout=timeout
                )
                latency = (time.monotonic() - t0) * 1000
                self._last_latency_ms = latency
                self._latency_history.append(latency)
                if len(self._latency_history) > 20:
                    self._latency_history.pop(0)
                # Per-endpoint tracking
                ep_list = self._latency_by_endpoint.setdefault(endpoint, [])
                ep_list.append(latency)
                if len(ep_list) > 20:
                    ep_list.pop(0)

                if r.status_code >= 500 and attempt < retries:
                    time.sleep(backoff * (2**attempt))
                    continue
                r.raise_for_status()
                return r
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as e:
                last_exc = e
                if attempt < retries:
                    time.sleep(backoff * (2**attempt))
                    continue
                raise
            except requests.exceptions.HTTPError:
                raise
        raise last_exc  # type: ignore[misc]

    def get_latency_p95(self) -> float:
        """P95 latency of last 20 requests in ms (all endpoints mixed)."""
        if not self._latency_history:
            return 0.0
        import math

        sorted_lat = sorted(self._latency_history)
        idx = min(int(math.ceil(0.95 * len(sorted_lat))) - 1, len(sorted_lat) - 1)
        return sorted_lat[idx]

    def get_latency_stats(self) -> dict:
        """Latency statistics per endpoint.

        Returns: {
            "get_accounts": {"p50": 280, "p95": 450, "min": 180, "max": 620, "avg": 310, "count": 20},
            "get_open_positions": {...},
            ...
        }
        """
        import math

        stats = {}
        for ep, history in self._latency_by_endpoint.items():
            if not history:
                stats[ep] = {
                    "p50": 0,
                    "p95": 0,
                    "min": 0,
                    "max": 0,
                    "avg": 0,
                    "count": 0,
                }
                continue
            s = sorted(history)
            n = len(s)
            stats[ep] = {
                "p50": round(s[n // 2], 1),
                "p95": round(s[min(int(math.ceil(0.95 * n)) - 1, n - 1)], 1),
                "min": round(s[0], 1),
                "max": round(s[-1], 1),
                "avg": round(sum(s) / n, 1),
                "count": n,
            }
        return stats

    # ── Token API (public) ──────────────────────────────────────────────────

    def get_token_age_hours(self) -> Optional[float]:
        """Token age in hours (None if no token yet)."""
        if not self._token_time:
            return None
        age = (
            dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - self._token_time
        ).total_seconds() / 3600
        return round(age, 2)

    def refresh_token(self) -> None:
        """Force token renewal."""
        self._token = ""
        self._token_time = None
        self._ensure_token()

    # ── Auth (internal) ─────────────────────────────────────────────────────

    def _ensure_token(self) -> str:
        """Get or renew token (24h validity)."""
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        if self._token and self._token_time:
            age = (now - self._token_time).total_seconds()
            if age < 82800:  # 23h
                return self._token

        r = requests.post(
            f"{BASE_URL}/api/Auth/loginKey",
            json={"userName": USERNAME, "apiKey": API_KEY},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Login failed: {data.get('errorMessage')}")
        self._token = data["token"]
        self._token_time = now
        return self._token

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # ── Helper functions ────────────────────────────────────────────────────

    def pts_to_ticks(self, pts: float) -> int:
        """Points to ticks (MNQ: 1pt = 4 ticks)."""
        return max(1, round(pts / TICK_SIZE))

    def get_accounts(self) -> list:
        """Get all active accounts."""
        r = self._request_with_retry(
            f"{BASE_URL}/api/Account/search",
            json={"onlyActiveAccounts": True},
            endpoint="get_accounts",
        )
        return r.json().get("accounts", [])

    def get_open_positions(self) -> list:
        """Get open positions for this account."""
        r = self._request_with_retry(
            f"{BASE_URL}/api/Position/searchOpen",
            json={"accountId": self.account_id},
            endpoint="get_open_positions",
        )
        return r.json().get("positions", [])

    def get_bars(self, minutes: int = 600) -> list:
        """Get recent 1-minute bars.

        minutes: How many minutes back (max ~13,888 at 20,000 bar limit)
        Note: live=False for TopStep Combine accounts (otherwise returns 0 bars)
        """
        end = dt.datetime.now(dt.timezone.utc)
        start = end - dt.timedelta(minutes=minutes)
        r = self._request_with_retry(
            f"{BASE_URL}/api/History/retrieveBars",
            json={
                "contractId": self.contract_id,
                "live": False,
                "startTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "unit": 2,  # Minute
                "unitNumber": 1,  # 1m bars
                "limit": 700,
                "includePartialBar": False,
            },
            timeout=20,
            endpoint="get_bars",
        )
        return r.json().get("bars", [])

    def get_open_orders(self) -> list:
        """Get open orders for this account."""
        r = self._request_with_retry(
            f"{BASE_URL}/api/Order/searchOpen",
            json={"accountId": self.account_id},
            endpoint="get_open_orders",
        )
        return r.json().get("orders", [])

    def get_trade_history(self, start_date: str = "", end_date: str = "") -> list:
        """Get executed trades from broker.

        start_date/end_date: ISO format e.g. "2026-03-20"
        Without parameters: today.
        Returns: List of trade dicts with id, price, profitAndLoss, side, size etc.
        """
        if not start_date:
            today = dt.datetime.now(dt.timezone.utc).date().isoformat()
            start_date = today
            end_date = today
        r = self._request_with_retry(
            f"{BASE_URL}/api/Trade/search",
            json={
                "accountId": self.account_id,
                "startDate": start_date,
                "endDate": end_date,
            },
            timeout=20,
            endpoint="get_trade_history",
        )
        data = r.json()
        return data.get("trades", [])

    def check_contract_valid(self) -> tuple:
        """Check if the current contract delivers valid bars.

        Returns: (is_valid: bool, reason: str)

        Rollover logic: Contract changes on 2nd Friday of quarterly month.
        H=March->M, M=June->U, U=Sept->Z, Z=Dec->H(+1)
        """
        CONTRACT_CYCLE = [
            (3, "H", "M"),  # March: H -> M
            (6, "M", "U"),  # June:  M -> U
            (9, "U", "Z"),  # Sept:  U -> Z
            (12, "Z", "H"),  # Dec:   Z -> H (next year)
        ]

        now = dt.datetime.now()
        year = now.year % 100

        def _second_friday(y: int, m: int) -> int:
            """Day of 2nd Friday in month."""
            import calendar

            first_day_weekday = calendar.weekday(y, m, 1)
            days_to_friday = (4 - first_day_weekday) % 7
            return 1 + days_to_friday + 7  # 2nd Friday

        # Determine which contract should be active now
        expected = None
        for month, old_code, new_code in CONTRACT_CYCLE:
            rollover_day = _second_friday(now.year, month)
            if now.month == month and now.day >= rollover_day:
                next_year = year + 1 if old_code == "Z" else year
                expected = f"{new_code}{next_year}"
                break
            elif now.month == month and now.day < rollover_day:
                expected = f"{old_code}{year}"
                break

        if expected is None:
            if now.month < 3 or (
                now.month == 3 and now.day < _second_friday(now.year, 3)
            ):
                expected = f"H{year}"
            elif now.month < 6 or (
                now.month == 6 and now.day < _second_friday(now.year, 6)
            ):
                expected = f"M{year}"
            elif now.month < 9 or (
                now.month == 9 and now.day < _second_friday(now.year, 9)
            ):
                expected = f"U{year}"
            elif now.month < 12 or (
                now.month == 12 and now.day < _second_friday(now.year, 12)
            ):
                expected = f"Z{year}"
            else:
                expected = f"H{year + 1}"

        current_code = self.contract_id.split(".")[-1]

        if current_code != expected:
            return (
                False,
                f"Contract WRONG! Configured: {current_code}, expected: {expected}",
            )

        try:
            bars = self.get_bars(minutes=10)
            if not bars:
                return (False, f"{current_code} returns no bars")
            return (True, f"{current_code} OK | {len(bars)} bars")
        except Exception as e:
            return (False, f"API error fetching bars: {e}")

    # ── Order Execution ─────────────────────────────────────────────────────

    def place_order(
        self,
        side: str,  # "long" or "short"
        sl_pts: float,  # Stop-Loss in points
        tp_pts: float,  # Take-Profit in points
        size: int = 1,
    ) -> dict:
        """Place market order WITH bracket orders (SL + TP).

        SL is set as stop-market at the broker (safety net).
        TP is set as limit at the broker (exact fill).

        side:   "long" = buy | "short" = sell
        sl_pts: Stop-Loss distance in points (converted to ticks)
        tp_pts: Take-Profit distance in points (converted to ticks)
        """
        order_side = SIDE_BID if side == "long" else SIDE_ASK
        sl_ticks = self.pts_to_ticks(sl_pts)
        tp_ticks = self.pts_to_ticks(tp_pts)

        if side == "long":
            sl_ticks_signed = -sl_ticks
            tp_ticks_signed = tp_ticks
        else:
            sl_ticks_signed = sl_ticks
            tp_ticks_signed = -tp_ticks

        payload = {
            "accountId": self.account_id,
            "contractId": self.contract_id,
            "type": ORDER_MARKET,
            "side": order_side,
            "size": size,
            "stopLossBracket": {
                "ticks": sl_ticks_signed,
                "type": ORDER_STOP,
            },
            "takeProfitBracket": {
                "ticks": tp_ticks_signed,
                "type": ORDER_LIMIT,
            },
        }

        if not self.live:
            print(
                f"  [SIM] ORDER {side.upper()} | SL {sl_pts}pt ({sl_ticks}T) | TP {tp_pts}pt ({tp_ticks}T)"
            )
            return {"orderId": -1, "success": True, "sim": True}

        print(
            f"  [LIVE] SENDING: {side.upper()} | SL {sl_pts}pt ({sl_ticks}T) | TP {tp_pts}pt ({tp_ticks}T)"
        )
        r = self._request_with_retry(
            f"{BASE_URL}/api/Order/place",
            json=payload,
            endpoint="place_order",
        )
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Order failed: {data.get('errorMessage')}")
        print(
            f"  [LIVE] ORDER {side.upper()} placed | OrderID {data.get('orderId')} "
            f"| SL {sl_pts}pt ({sl_ticks}T) | TP {tp_pts}pt ({tp_ticks}T) | BRACKET active"
        )
        return data

    def close_position(self) -> dict:
        """Close open position immediately (market order)."""
        if not self.live:
            print("  [SIM] CLOSE POSITION")
            return {"success": True, "sim": True}

        r = self._request_with_retry(
            f"{BASE_URL}/api/Position/closeContract",
            json={"accountId": self.account_id, "contractId": self.contract_id},
            endpoint="close_position",
        )
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"Close failed: {data.get('errorMessage')}")
        print("  [LIVE] POSITION CLOSED")
        return data

    def cancel_all_orders(self) -> None:
        """Cancel all open orders."""
        orders = self.get_open_orders()
        for o in orders:
            try:
                self._request_with_retry(
                    f"{BASE_URL}/api/Order/cancel",
                    json={"accountId": self.account_id, "orderId": o["id"]},
                    timeout=10,
                    endpoint="cancel_order",
                )
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(
                    "Failed to cancel order %s: %s", o.get("id"), e
                )


# ── Quick Test ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== TopStepX API Test ===\n")

    api = TopstepAPI(live=False)

    print("Accounts:")
    for a in api.get_accounts():
        print(
            f"  {a['name']} | ID {a['id']} | Balance ${a.get('balance', 0):,.2f} | "
            f"canTrade={a.get('canTrade')}"
        )

    print(f"\nContract: {CONTRACT_ID}")
    print(f"Tick: {TICK_SIZE}pt = ${TICK_VALUE}")

    print("\n--- SIM Order Tests ---")
    api.place_order("long", sl_pts=5.0, tp_pts=15.0)
    api.place_order("short", sl_pts=5.0, tp_pts=15.0)
    api.close_position()

    print("\nAll tests OK.")
