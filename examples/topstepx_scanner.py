#!/usr/bin/env python3
"""
api_health_scanner.py – TopStepX API Trust System
===================================================
Monitors TopStepX API health every 5 minutes with 9 automated checks.
Outputs a Trust Score (0-100) and writes results to JSON for alerting.

The 9 Checks:
  1. Reachability    (HTTP 200?)
  2. Latency         (p95 < 2000ms?)
  3. Data Freshness  (last bar < 5 min old?)
  4. Contract        (not expired?)
  5. Token           (not expired?)
  6. canTrade        (True?)
  7. Bar Quality     (no NaN, volume > 0?)
  8. Balance         (drawdown < 80%?)
  9. Loop Continuity (live scanners running without gaps?)

Usage:
  python api_health_scanner.py

Output:
  /tmp/nq_api_health.json (atomically written, safe for external readers)

Alerting:
  No built-in alerting. Use n8n, systemd timer, or cron to read the JSON
  and trigger alerts (Telegram, email, etc.) based on trust_score/status.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

# Import our API module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from topstep_api import TopstepAPI

# ── Constants ────────────────────────────────────────────────────────────────

HEALTH_FILE = os.environ.get("HEALTH_OUTPUT_FILE", "/tmp/nq_api_health.json")
CHECK_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL", "300"))  # 5 minutes

ACCOUNT_ID = int(os.environ.get("PROJECTX_ACCOUNT_ID", "0"))
STARTING_BALANCE = float(os.environ.get("STARTING_BALANCE", "50000"))

# Trust Score weights (sum = 1.0)
WEIGHTS = {
    "reachability": 0.15,
    "latency": 0.08,
    "data_freshness": 0.18,
    "contract": 0.18,
    "token": 0.08,
    "can_trade": 0.10,
    "bar_quality": 0.06,
    "balance": 0.08,
    "loop_continuity": 0.09,
}

# Thresholds
LATENCY_WARN_MS = 2000
LATENCY_CRIT_MS = 5000
DATA_FRESHNESS_WARN_MIN = 5
DATA_FRESHNESS_CRIT_MIN = 15
DRAWDOWN_WARN_PCT = 80
NQ_PRICE_MIN = 15000
NQ_PRICE_MAX = 30000
LOOP_GAP_WARN_MIN = 3
LOOP_GAP_CRIT_MIN = 10

# Live scanner logs (configure for your setup)
NQ_DIR = Path(__file__).resolve().parent
LIVE_SCANNER_LOGS: dict[str, Path] = {
    # Add your scanner log paths here:
    # "MyScanner": NQ_DIR / "logs" / "myscanner.log",
}


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class HealthCheck:
    """Result of a single check."""

    name: str
    passed: bool
    score: float  # 0.0 - 1.0
    detail: str
    latency_ms: float = 0.0


@dataclass
class APIHealthResult:
    """Overall result of all checks."""

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
        """Write atomically to JSON (via .tmp)."""
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
        os.replace(tmp, path)


# ── Helper Functions ─────────────────────────────────────────────────────────


def is_market_open() -> bool:
    """Check if NQ futures market is currently open.
    Trading hours: Sun 18:00 - Fri 17:00 ET (with daily pause 17:00-18:00 ET).
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

    now_et = datetime.now(ZoneInfo("America/New_York"))
    weekday = now_et.weekday()  # 0=Mon, 6=Sun

    if weekday == 5:  # Saturday: closed
        return False
    if weekday == 6:  # Sunday: open from 18:00 ET
        return now_et.hour >= 18
    if weekday == 4 and now_et.hour >= 17:  # Friday: close at 17:00 ET
        return False
    if 17 <= now_et.hour < 18:  # Daily pause 17:00-18:00 ET
        return False

    return True


def log(msg: str) -> None:
    """Timestamped log output."""
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ── API Health Scanner ───────────────────────────────────────────────────────


class APIHealthScanner:
    """Runs 9 health checks against the TopStepX API."""

    def __init__(self) -> None:
        self.api = TopstepAPI(account_id=ACCOUNT_ID, live=False)
        self._last_result: Optional[APIHealthResult] = None
        self._latency_stats: dict = {}

        # Cache for API data (per run, not per check)
        self._cached_accounts: Optional[list] = None
        self._cached_bars: Optional[list] = None

    # ── Market Pause Helper ─────────────────────────────────────────────────

    def _market_pass(self, name: str) -> Optional[HealthCheck]:
        """Return automatic PASS when market is closed."""
        if not is_market_open():
            return HealthCheck(name, True, 1.0, "Market closed (auto-pass)")
        return None

    # ── The 9 Checks ────────────────────────────────────────────────────────

    def check_reachability(self) -> HealthCheck:
        """Check 1: Is the API reachable? (HTTP 200)"""
        try:
            t0 = time.monotonic()
            accounts = self.api.get_accounts()
            latency = (time.monotonic() - t0) * 1000
            self._cached_accounts = accounts
            return HealthCheck(
                "reachability",
                True,
                1.0,
                f"HTTP 200 in {latency:.0f}ms",
                latency,
            )
        except requests.exceptions.Timeout:
            return HealthCheck("reachability", False, 0.0, "Timeout (>15s)")
        except requests.exceptions.ConnectionError:
            return HealthCheck("reachability", False, 0.0, "Connection failed")
        except Exception as e:
            return HealthCheck("reachability", False, 0.0, f"Error: {e}")

    def check_latency(self) -> HealthCheck:
        """Check 2: Per-endpoint latency measurement (p50/p95/min/max per endpoint)"""
        stats = self.api.get_latency_stats()
        self._latency_stats = stats

        if not stats:
            return HealthCheck("latency", True, 1.0, "No measurements yet")

        worst_p95 = 0.0
        details = []
        for ep, s in sorted(stats.items()):
            if s["count"] == 0:
                continue
            worst_p95 = max(worst_p95, s["p95"])
            details.append(f"{ep}: p95={s['p95']:.0f}ms")

        if not details:
            return HealthCheck("latency", True, 1.0, "No measurements yet")

        if worst_p95 <= LATENCY_WARN_MS:
            score, passed = 1.0, True
        elif worst_p95 <= LATENCY_CRIT_MS:
            score, passed = 0.5, False
        else:
            score, passed = 0.0, False

        return HealthCheck(
            "latency",
            passed,
            score,
            " | ".join(details),
            worst_p95,
        )

    def check_data_freshness(self) -> HealthCheck:
        """Check 3: Last bar < 5 min old (only when market is open)"""
        mp = self._market_pass("data_freshness")
        if mp:
            return mp

        try:
            if self._cached_bars is None:
                self._cached_bars = self.api.get_bars(minutes=10)

            bars = self._cached_bars
            if not bars:
                return HealthCheck("data_freshness", False, 0.0, "No bars received")

            last_bar = bars[-1]
            ts_str = last_bar.get("t") or last_bar.get("timestamp", "")
            if not ts_str:
                return HealthCheck("data_freshness", False, 0.0, "No timestamp in bar")

            bar_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            now_utc = datetime.now(timezone.utc)
            age_min = (now_utc - bar_time).total_seconds() / 60

            if age_min <= DATA_FRESHNESS_WARN_MIN:
                return HealthCheck(
                    "data_freshness", True, 1.0, f"Last bar {age_min:.1f} min ago"
                )
            elif age_min <= DATA_FRESHNESS_CRIT_MIN:
                return HealthCheck(
                    "data_freshness", False, 0.5, f"Bar {age_min:.1f} min old!"
                )
            else:
                return HealthCheck(
                    "data_freshness", False, 0.0, f"Bar {age_min:.0f} min old!"
                )
        except Exception as e:
            return HealthCheck("data_freshness", False, 0.0, f"Error: {e}")

    def check_contract(self) -> HealthCheck:
        """Check 4: Contract not expired (MOST IMPORTANT CHECK!)"""
        try:
            is_valid, reason = self.api.check_contract_valid()
            if is_valid:
                return HealthCheck("contract", True, 1.0, reason)
            if not is_market_open() and "no bars" in reason.lower():
                return HealthCheck("contract", True, 0.8, f"{reason} (market closed)")
            return HealthCheck("contract", False, 0.0, reason)
        except Exception as e:
            return HealthCheck("contract", False, 0.0, f"Error: {e}")

    def check_token(self) -> HealthCheck:
        """Check 5: Token not expired"""
        try:
            age_h = self.api.get_token_age_hours()
            if age_h is None:
                self.api.refresh_token()
                return HealthCheck("token", True, 0.8, "Token freshly created")

            if age_h < 23:
                return HealthCheck(
                    "token", True, 1.0, f"Token valid ({age_h:.1f}h old)"
                )
            else:
                self.api.refresh_token()
                return HealthCheck("token", True, 0.8, "Token renewed")
        except Exception as e:
            return HealthCheck("token", False, 0.0, f"Login failed: {e}")

    def check_can_trade(self) -> HealthCheck:
        """Check 6: canTrade = True"""
        try:
            accounts = self._cached_accounts or self.api.get_accounts()
            for acc in accounts:
                if acc.get("id") == ACCOUNT_ID:
                    if acc.get("canTrade", False):
                        return HealthCheck("can_trade", True, 1.0, "canTrade=True")
                    else:
                        name = acc.get("name", str(ACCOUNT_ID))
                        return HealthCheck(
                            "can_trade", False, 0.0, f"{name}: canTrade=False"
                        )
            return HealthCheck("can_trade", False, 0.0, "Account not found")
        except Exception as e:
            return HealthCheck("can_trade", False, 0.0, f"Error: {e}")

    def check_bar_quality(self) -> HealthCheck:
        """Check 7: No NaN values, volume > 0, OHLC plausible"""
        mp = self._market_pass("bar_quality")
        if mp:
            return mp

        try:
            if self._cached_bars is None:
                self._cached_bars = self.api.get_bars(minutes=10)

            bars = self._cached_bars
            if not bars:
                return HealthCheck("bar_quality", False, 0.0, "No bars to check")

            problems = []
            for i, bar in enumerate(bars[-10:]):
                o = bar.get("o", 0) or bar.get("open", 0)
                h = bar.get("h", 0) or bar.get("high", 0)
                l = bar.get("l", 0) or bar.get("low", 0)
                c = bar.get("c", 0) or bar.get("close", 0)
                v = bar.get("v", 0) or bar.get("volume", 0)

                if any(
                    x is None or (isinstance(x, float) and math.isnan(x))
                    for x in [o, h, l, c]
                ):
                    problems.append(f"Bar {i}: NaN value")
                elif h < l:
                    problems.append(f"Bar {i}: high < low")
                elif not (NQ_PRICE_MIN < c < NQ_PRICE_MAX):
                    problems.append(
                        f"Bar {i}: price {c} outside {NQ_PRICE_MIN}-{NQ_PRICE_MAX}"
                    )
                elif v is not None and v == 0:
                    problems.append(f"Bar {i}: volume=0")

            if problems:
                return HealthCheck(
                    "bar_quality", False, 0.3, f"{len(problems)} issues: {problems[0]}"
                )
            return HealthCheck(
                "bar_quality", True, 1.0, f"{min(len(bars), 10)} bars OK"
            )
        except Exception as e:
            return HealthCheck("bar_quality", False, 0.0, f"Error: {e}")

    def check_balance(self) -> HealthCheck:
        """Check 8: Drawdown < 80% of limit"""
        try:
            accounts = self._cached_accounts or self.api.get_accounts()
            worst_dd = 0.0
            details = []
            for acc in accounts:
                if acc.get("id") != ACCOUNT_ID:
                    continue
                balance = acc.get("balance", STARTING_BALANCE)
                loss = STARTING_BALANCE - balance
                dd_pct = max(0, loss / STARTING_BALANCE * 100)
                worst_dd = max(worst_dd, dd_pct)
                details.append(f"${balance:,.0f} (DD {dd_pct:.0f}%)")

            if not details:
                return HealthCheck("balance", False, 0.0, "Account not found")

            if worst_dd >= DRAWDOWN_WARN_PCT:
                return HealthCheck("balance", False, 0.0, " | ".join(details))
            elif worst_dd >= 60:
                return HealthCheck("balance", True, 0.5, " | ".join(details))
            else:
                return HealthCheck("balance", True, 1.0, " | ".join(details))
        except Exception as e:
            return HealthCheck("balance", False, 0.0, f"Error: {e}")

    def check_loop_continuity(self) -> HealthCheck:
        """Check 9: Are live scanner loops running without gaps?

        Reads the last 10 lines of each live scanner log and checks
        if the last scan was < 3 min ago. Detects crashes/hangs.
        """
        mp = self._market_pass("loop_continuity")
        if mp:
            return mp

        if not LIVE_SCANNER_LOGS:
            return HealthCheck(
                "loop_continuity", True, 1.0, "No scanner logs configured (skipped)"
            )

        try:
            import re

            now_utc = datetime.now(timezone.utc)
            problems = []
            checked = 0

            for name, log_path in LIVE_SCANNER_LOGS.items():
                if not log_path.exists():
                    problems.append(f"{name}: Log missing!")
                    continue

                try:
                    with open(log_path, "rb") as f:
                        f.seek(0, 2)
                        size = f.tell()
                        f.seek(max(0, size - 4096))
                        tail = f.read().decode("utf-8", errors="replace")
                except Exception:
                    problems.append(f"{name}: Log not readable")
                    continue

                lines = tail.strip().splitlines()

                last_ts = None
                for line in reversed(lines):
                    m = re.search(r"(\d{2}):(\d{2}):\d{2} UTC", line)
                    if m:
                        h, mi = int(m.group(1)), int(m.group(2))
                        last_ts = now_utc.replace(
                            hour=h, minute=mi, second=0, microsecond=0
                        )
                        if last_ts > now_utc:
                            last_ts -= timedelta(days=1)
                        break
                    m = re.search(r"\[(\d{2}):(\d{2}) ET", line)
                    if m:
                        h, mi = int(m.group(1)), int(m.group(2))
                        # Convert ET to UTC using zoneinfo (handles EDT/EST automatically)
                        try:
                            from zoneinfo import ZoneInfo
                        except ImportError:
                            from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]
                        et_time = now_utc.astimezone(
                            ZoneInfo("America/New_York")
                        ).replace(
                            hour=h,
                            minute=mi,
                            second=0,
                            microsecond=0,
                        )
                        last_ts = et_time.astimezone(timezone.utc).replace(tzinfo=None)
                        last_ts = last_ts.replace(tzinfo=timezone.utc)
                        if last_ts > now_utc:
                            last_ts -= timedelta(days=1)
                        break

                if last_ts is None:
                    problems.append(f"{name}: No timestamp in log")
                    continue

                age_min = (now_utc - last_ts).total_seconds() / 60
                checked += 1

                if age_min > LOOP_GAP_CRIT_MIN:
                    problems.append(f"{name}: {age_min:.0f} min gap!")
                elif age_min > LOOP_GAP_WARN_MIN:
                    problems.append(f"{name}: {age_min:.0f} min since last scan")

            if not checked and problems:
                return HealthCheck("loop_continuity", False, 0.0, "No logs found")
            if problems:
                score = 0.0 if any("gap" in p.lower() for p in problems) else 0.5
                return HealthCheck(
                    "loop_continuity",
                    False,
                    score,
                    f"{len(problems)} issue(s): {problems[0]}",
                )
            return HealthCheck(
                "loop_continuity", True, 1.0, f"All {checked} scanners running"
            )
        except Exception as e:
            return HealthCheck("loop_continuity", False, 0.0, f"Error: {e}")

    # ── Orchestration ───────────────────────────────────────────────────────

    def run_all_checks(self) -> APIHealthResult:
        """Run all 9 checks and calculate Trust Score."""
        self._cached_accounts = None
        self._cached_bars = None

        checks: list[HealthCheck] = []
        check_fns = [
            self.check_reachability,
            self.check_latency,
            self.check_data_freshness,
            self.check_contract,
            self.check_token,
            self.check_can_trade,
            self.check_bar_quality,
            self.check_balance,
            self.check_loop_continuity,
        ]

        for fn in check_fns:
            try:
                checks.append(fn())
            except Exception as e:
                checks.append(
                    HealthCheck(
                        name=fn.__name__.replace("check_", ""),
                        passed=False,
                        score=0.0,
                        detail=f"CHECK CRASHED: {e}",
                    )
                )

        trust_score = sum(c.score * WEIGHTS.get(c.name, 0.1) for c in checks) * 100

        if trust_score >= 80:
            status = "HEALTHY"
        elif trust_score >= 50:
            status = "DEGRADED"
        else:
            status = "CRITICAL"

        alerts = [f"{c.name}: {c.detail}" for c in checks if not c.passed]

        result = APIHealthResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            trust_score=trust_score,
            status=status,
            checks=checks,
            alerts=alerts,
        )
        self._last_result = result
        return result

    # ── JSON Export ──────────────────────────────────────────────────────────

    def write_health_json(self, result: APIHealthResult) -> None:
        """Write result to JSON file (atomically).
        Includes per-endpoint latency stats.
        """
        data = result.to_dict()
        data["latency"] = self._latency_stats
        tmp = HEALTH_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, HEALTH_FILE)
        self._append_latency_csv()

    def _append_latency_csv(self) -> None:
        """Append latency stats to logs/latency_history.csv (for trending)."""
        if not self._latency_stats:
            return
        csv_path = NQ_DIR / "logs" / "latency_history.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not csv_path.exists()
        try:
            with open(csv_path, "a") as f:
                if write_header:
                    f.write("timestamp,endpoint,p50,p95,min,max,avg,count\n")
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                for ep, s in sorted(self._latency_stats.items()):
                    if s["count"] == 0:
                        continue
                    f.write(
                        f"{ts},{ep},{s['p50']},{s['p95']},{s['min']},{s['max']},{s['avg']},{s['count']}\n"
                    )
        except Exception as e:
            log(f"Latency CSV error: {e}")

    # ── Formatting ──────────────────────────────────────────────────────────

    def get_summary_text(self) -> str:
        """Formatted text summary (e.g. for Telegram or logging)."""
        r = self._last_result
        if not r:
            return "No check performed yet"

        if r.trust_score >= 80:
            status = "HEALTHY"
        elif r.trust_score >= 50:
            status = "DEGRADED"
        else:
            status = "CRITICAL"

        lines = [
            "API Trust System",
            f"Trust Score: {r.trust_score:.0f}/100 ({status})",
            "",
            "Checks:",
        ]

        for check in r.checks:
            icon = "OK" if check.passed else "FAIL"
            lines.append(f"  [{icon}] {check.name}: {check.detail}")

        ts = r.timestamp[:19].replace("T", " ")
        lines.append(f"\nLast check: {ts} UTC")
        return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    log(f"API Health Scanner started | Interval: {CHECK_INTERVAL}s")
    log(f"Health JSON: {HEALTH_FILE}")

    scanner = APIHealthScanner()

    # First check immediately
    try:
        result = scanner.run_all_checks()
        scanner.write_health_json(result)
        passed = sum(1 for c in result.checks if c.passed)
        log(
            f"First check: Trust={result.trust_score:.0f} [{result.status}] | {passed}/9 passed"
        )
        if result.trust_score < 80:
            log(f"  Trust low! Alerts: {result.alerts}")
    except Exception as e:
        log(f"ERROR on first check: {e}")
        traceback.print_exc()

    # Loop
    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            result = scanner.run_all_checks()
            scanner.write_health_json(result)

            passed = sum(1 for c in result.checks if c.passed)
            log(f"Trust={result.trust_score:.0f} [{result.status}] | {passed}/9 passed")
        except Exception as e:
            log(f"ERROR: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
