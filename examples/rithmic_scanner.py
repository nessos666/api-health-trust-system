#!/usr/bin/env python3
"""
rithmic_health_scanner.py – Rithmic API Trust System (file-based)
==================================================================
Monitors Rithmic API health every 5 minutes with 10 automated checks.
Outputs a Trust Score (0-100) and writes results to JSON for alerting.

IMPORTANT: This scanner does NOT open its own Rithmic connection!
It reads the status JSON written by your live scanner and checks
local files/processes. This prevents session conflicts.

The 10 Checks (all file-based):
  1. Reachability    (scanner status: connected?)
  2. Latency         (scanner status: age < 2 min?)
  3. Data Freshness  (scanner status: last_bar_time current?)
  4. Contract        (scanner status: symbol present?)
  5. Auth            (scanner status: fcm_id + ib_id present?)
  6. canTrade        (scanner status: connected + process running?)
  7. Data Quality    (scanner status: last_tick_price in NQ range?)
  8. Balance         (scan mode: auto-pass)
  9. Loop Continuity (log files: scanner running without gaps?)
 10. Truth Layer     (cross-validation state file)

Usage:
  python rithmic_health_scanner.py

Output:
  JSON file (configurable via HEALTH_OUTPUT_FILE env var)
"""

from __future__ import annotations

import json as _json

try:
    import orjson as _orjson

    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False


def _json_loads(data: str | bytes) -> dict:
    return _orjson.loads(data) if _HAS_ORJSON else _json.loads(data)


def _json_load_file(path: str) -> dict:
    """Read JSON file (orjson if available)."""
    if _HAS_ORJSON:
        with open(path, "rb") as f:
            return _orjson.loads(f.read())
    else:
        with open(path) as f:
            return _json.load(f)


def _json_write(path: str, data: dict) -> None:
    """Atomic JSON write via .tmp + os.replace()."""
    tmp = str(path) + ".tmp"
    if _HAS_ORJSON:
        with open(tmp, "wb") as f:
            f.write(_orjson.dumps(data, option=_orjson.OPT_INDENT_2))
    else:
        with open(tmp, "w") as f:
            _json.dump(data, f, indent=2)
    os.replace(tmp, str(path))


import math
import os
import signal
import subprocess
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ── Constants ────────────────────────────────────────────────────────────────

HEALTH_FILE = os.environ.get("HEALTH_OUTPUT_FILE", "/tmp/rithmic_health.json")
STATUS_FILE = os.environ.get("RITHMIC_STATUS_FILE", "/tmp/rithmic_scanner_status.json")
CHECK_INTERVAL = int(os.environ.get("HEALTH_CHECK_INTERVAL", "300"))  # 5 minutes

# Account ID for display (no trading on scan-only accounts)
RITHMIC_ACCOUNT_ID = os.environ.get("RITHMIC_ACCOUNT_ID", "")

# Process names to check (comma-separated)
SCANNER_PROCESS_NAMES = os.environ.get("RITHMIC_SCANNER_PROCESSES", "").split(",")

# Trust Score weights (sum = 1.0)
WEIGHTS = {
    "reachability": 0.15,
    "latency": 0.10,
    "data_freshness": 0.18,
    "contract": 0.15,
    "auth": 0.08,
    "can_trade": 0.10,
    "data_quality": 0.06,
    "balance": 0.08,
    "loop_continuity": 0.02,
    "truth_layer": 0.08,
}

# Thresholds
STATUS_STALE_WARN_SEC = 120  # 2 min: status JSON too old
STATUS_STALE_CRIT_SEC = 300  # 5 min: status JSON critically old
DATA_FRESHNESS_WARN_MIN = 5
DATA_FRESHNESS_CRIT_MIN = 15
NQ_PRICE_MIN = 15000
NQ_PRICE_MAX = 30000
LOOP_GAP_WARN_MIN = 3
LOOP_GAP_CRIT_MIN = 10

# Live scanner logs (configure for your setup)
SCANNER_LOG_DIR = Path(os.environ.get("SCANNER_LOG_DIR", "."))
LIVE_SCANNER_LOGS: dict[str, Path] = {
    # Add your scanner log paths here:
    # "MyScanner": SCANNER_LOG_DIR / "scanner.log",
}

# Truth Layer state file (optional)
TRUTH_STATE_FILE = Path(os.environ.get("TRUTH_STATE_FILE", "/tmp/truth_state.json"))
TRUTH_STALE_SEC = 300


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


def _read_status_json() -> Optional[dict]:
    """Read the scanner status JSON. Returns None on error."""
    try:
        if not os.path.exists(STATUS_FILE):
            return None
        return _json_load_file(STATUS_FILE)
    except Exception:
        return None


def _scanner_running() -> bool:
    """Check if any configured scanner process is running."""
    try:
        for pattern in SCANNER_PROCESS_NAMES:
            pattern = pattern.strip()
            if not pattern:
                continue
            result = subprocess.run(
                ["pgrep", "-f", pattern],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return True
        return False
    except Exception:
        return False


# ── Rithmic Health Scanner ───────────────────────────────────────────────────


class RithmicHealthScanner:
    """Runs 10 health checks – entirely file-based, NO Rithmic connection.

    Why file-based?
    ===============
    Rithmic allows only ONE session per user. If the health scanner opened
    its own connection, it would either:
    (a) disconnect the live trading scanner, or
    (b) get rejected by Rithmic.

    Instead, this scanner reads the status JSON that the live scanner writes,
    and checks local files/processes. Zero interference, full visibility.
    """

    def __init__(self) -> None:
        self._last_result: Optional[APIHealthResult] = None

    # ── Market Pause Helper ──────────────────────────────────────────────

    def _market_pass(self, name: str) -> Optional[HealthCheck]:
        """Return automatic PASS when market is closed."""
        if not is_market_open():
            return HealthCheck(name, True, 1.0, "Market closed (auto-pass)")
        return None

    # ── The 10 Checks (all file-based) ───────────────────────────────────

    def check_reachability(self, status: Optional[dict]) -> HealthCheck:
        """Check 1: Is the Rithmic scanner connected?"""
        if status is None:
            if not _scanner_running():
                return HealthCheck("reachability", False, 0.0, "Scanner not started")
            return HealthCheck(
                "reachability", False, 0.0, "Status JSON missing (scanner running)"
            )

        connected = status.get("connected", False)
        symbol = status.get("symbol", "?")

        if connected:
            return HealthCheck(
                "reachability",
                True,
                1.0,
                f"Rithmic connected | {symbol}",
            )
        return HealthCheck(
            "reachability", False, 0.0, f"Rithmic disconnected | {symbol}"
        )

    def check_latency(self, status: Optional[dict]) -> HealthCheck:
        """Check 2: Is the status JSON current? (proxy for latency)"""
        if status is None:
            return HealthCheck("latency", False, 0.0, "No status JSON available")

        try:
            ts = datetime.fromisoformat(status["timestamp"])
            age_sec = (datetime.now(timezone.utc) - ts).total_seconds()

            if age_sec <= STATUS_STALE_WARN_SEC:
                return HealthCheck(
                    "latency", True, 1.0, f"Status {age_sec:.0f}s old (OK)"
                )
            if age_sec <= STATUS_STALE_CRIT_SEC:
                return HealthCheck(
                    "latency", False, 0.5, f"Status {age_sec:.0f}s old (slow)"
                )
            return HealthCheck(
                "latency", False, 0.0, f"Status {age_sec / 60:.0f} min old (stale!)"
            )
        except Exception as e:
            return HealthCheck("latency", False, 0.0, f"Error: {e}")

    def check_data_freshness(self, status: Optional[dict]) -> HealthCheck:
        """Check 3: Last bar < 5 min old?"""
        mp = self._market_pass("data_freshness")
        if mp:
            return mp

        if status is None:
            return HealthCheck("data_freshness", False, 0.0, "No status JSON available")

        last_bar = status.get("last_bar_time")
        if not last_bar:
            return HealthCheck("data_freshness", False, 0.0, "No bar data received")

        try:
            bar_time = datetime.fromisoformat(last_bar)
            age_min = (datetime.now(timezone.utc) - bar_time).total_seconds() / 60

            if age_min <= DATA_FRESHNESS_WARN_MIN:
                return HealthCheck(
                    "data_freshness", True, 1.0, f"Last bar {age_min:.1f} min ago"
                )
            if age_min <= DATA_FRESHNESS_CRIT_MIN:
                return HealthCheck(
                    "data_freshness", False, 0.5, f"Bar {age_min:.1f} min old!"
                )
            return HealthCheck(
                "data_freshness", False, 0.0, f"Bar {age_min:.0f} min old!"
            )
        except Exception as e:
            return HealthCheck("data_freshness", False, 0.0, f"Error: {e}")

    def check_contract(self, status: Optional[dict]) -> HealthCheck:
        """Check 4: Symbol present and valid?"""
        if status is None:
            mp = self._market_pass("contract")
            if mp:
                return mp
            return HealthCheck("contract", False, 0.0, "No status JSON available")

        symbol = status.get("symbol", "")
        if symbol and ("NQ" in symbol or "MNQ" in symbol):
            return HealthCheck("contract", True, 1.0, f"Active symbol: {symbol}")
        if symbol:
            return HealthCheck(
                "contract", True, 0.8, f"Symbol: {symbol} (unknown type)"
            )
        return HealthCheck("contract", False, 0.0, "No symbol in status")

    def check_auth(self, status: Optional[dict]) -> HealthCheck:
        """Check 5: Auth data present?"""
        if status is None:
            return HealthCheck("auth", False, 0.0, "No status JSON available")

        fcm = status.get("fcm_id")
        ib = status.get("ib_id")

        if fcm and ib:
            return HealthCheck("auth", True, 1.0, f"Auth OK | FCM={fcm} | IB={ib}")
        if fcm:
            return HealthCheck(
                "auth", True, 0.8, f"Partial auth | FCM={fcm} | IB missing"
            )
        return HealthCheck("auth", False, 0.0, "No auth data in status")

    def check_can_trade(self, status: Optional[dict]) -> HealthCheck:
        """Check 6: Account connected and process running?"""
        running = _scanner_running()

        if status is None:
            if running:
                return HealthCheck(
                    "can_trade", True, 0.5, "Process running but no status JSON"
                )
            return HealthCheck("can_trade", False, 0.0, "Scanner not active")

        connected = status.get("connected", False)

        if connected and running:
            return HealthCheck("can_trade", True, 1.0, "Connected and process active")
        if running:
            return HealthCheck(
                "can_trade", True, 0.5, "Process running but not connected"
            )
        return HealthCheck("can_trade", False, 0.0, "Scanner process stopped")

    def check_data_quality(self, status: Optional[dict]) -> HealthCheck:
        """Check 7: Tick price in NQ range?"""
        mp = self._market_pass("data_quality")
        if mp:
            return mp

        if status is None:
            return HealthCheck("data_quality", False, 0.0, "No status JSON available")

        price = status.get("last_tick_price")
        if price is None:
            return HealthCheck("data_quality", False, 0.0, "No tick data in status")

        try:
            price = float(price)
        except (ValueError, TypeError):
            return HealthCheck(
                "data_quality", False, 0.0, f"Price not parseable: {price}"
            )

        if isinstance(price, float) and math.isnan(price):
            return HealthCheck("data_quality", False, 0.0, "Tick price is NaN!")

        if not (NQ_PRICE_MIN < price < NQ_PRICE_MAX):
            return HealthCheck(
                "data_quality",
                False,
                0.3,
                f"Price {price} outside {NQ_PRICE_MIN}-{NQ_PRICE_MAX}",
            )

        bars = status.get("bars_count", 0)
        return HealthCheck(
            "data_quality", True, 1.0, f"Price OK: NQ={price:.2f} ({bars} bars)"
        )

    def check_balance(self) -> HealthCheck:
        """Check 8: Balance – Auto-pass in scan mode (no trading)."""
        account_info = (
            f"Account #{RITHMIC_ACCOUNT_ID}" if RITHMIC_ACCOUNT_ID else "Scan mode"
        )
        return HealthCheck(
            "balance", True, 1.0, f"{account_info} (scan only, no trading)"
        )

    def check_loop_continuity(self) -> HealthCheck:
        """Check 9: Are live scanner loops running without gaps?"""
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
                    m = re.search(
                        r"(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2}):(\d{2})", line
                    )
                    if m:
                        try:
                            last_ts = datetime.strptime(
                                f"{m.group(1)} {m.group(2)}:{m.group(3)}:{m.group(4)}",
                                "%Y-%m-%d %H:%M:%S",
                            ).replace(tzinfo=timezone.utc)
                        except ValueError:
                            continue
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

            if not checked:
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
                "loop_continuity",
                True,
                1.0,
                f"All {checked} scanners running",
            )
        except Exception as e:
            return HealthCheck("loop_continuity", False, 0.0, f"Error: {e}")

    def check_truth_layer(self) -> HealthCheck:
        """Check 10: Cross-validation layer healthy?"""
        try:
            if not TRUTH_STATE_FILE.exists():
                return HealthCheck(
                    "truth_layer", True, 1.0, "No truth layer active (scan mode)"
                )

            raw = TRUTH_STATE_FILE.read_text()
            state = _json_loads(raw)

            ts = datetime.fromisoformat(state["timestamp"])
            age_sec = (datetime.now(timezone.utc) - ts).total_seconds()

            if age_sec > TRUTH_STALE_SEC:
                return HealthCheck(
                    "truth_layer",
                    False,
                    0.3,
                    f"Truth layer not responding ({age_sec / 60:.0f} min)",
                )

            if state.get("blocked"):
                reason = state.get("block_reason", "unknown")
                return HealthCheck("truth_layer", False, 0.0, f"BLOCKED: {reason}")

            api_errors = state.get("api_errors", 0)
            if api_errors >= 3:
                return HealthCheck(
                    "truth_layer", False, 0.0, f"Too many API errors ({api_errors}/3)"
                )

            circuit = state.get("circuit_state", "CLOSED")
            if circuit != "CLOSED":
                return HealthCheck(
                    "truth_layer", False, 0.5, f"Circuit Breaker: {circuit}"
                )

            recon_count = state.get("recon_count", 0)
            scanner = state.get("scanner", "?")
            pos = "position open" if state.get("position_open") else "flat"
            return HealthCheck(
                "truth_layer",
                True,
                1.0,
                f"OK | {scanner} | Recon #{recon_count} | {pos}",
            )

        except Exception as e:
            return HealthCheck("truth_layer", False, 0.0, f"Error: {e}")

    # ── Orchestration ────────────────────────────────────────────────────

    def run_all_checks(self) -> APIHealthResult:
        """Run all 10 checks and calculate Trust Score."""
        # Read status JSON once, share across all checks
        status = _read_status_json()

        checks: list[HealthCheck] = [
            self.check_reachability(status),
            self.check_latency(status),
            self.check_data_freshness(status),
            self.check_contract(status),
            self.check_auth(status),
            self.check_can_trade(status),
            self.check_data_quality(status),
            self.check_balance(),
            self.check_loop_continuity(),
            self.check_truth_layer(),
        ]

        # Calculate Trust Score
        trust_score = sum(c.score * WEIGHTS.get(c.name, 0.1) for c in checks) * 100

        if trust_score >= 80:
            status_str = "HEALTHY"
        elif trust_score >= 50:
            status_str = "DEGRADED"
        else:
            status_str = "CRITICAL"

        alerts = [f"{c.name}: {c.detail}" for c in checks if not c.passed]

        result = APIHealthResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            trust_score=trust_score,
            status=status_str,
            checks=checks,
            alerts=alerts,
        )
        self._last_result = result
        return result

    # ── JSON Export ───────────────────────────────────────────────────────

    def write_health_json(self, result: APIHealthResult) -> None:
        """Write result to JSON file (atomically)."""
        data = result.to_dict()
        data["broker"] = "Rithmic"
        data["mode"] = "file-based (no own connection)"
        _json_write(HEALTH_FILE, data)

    # ── Formatting ───────────────────────────────────────────────────────

    def get_summary_text(self) -> str:
        """Formatted text summary."""
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
            "Rithmic API Trust System",
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
    log(f"Rithmic Health Scanner started (file-based) | Interval: {CHECK_INTERVAL}s")
    log(f"Health JSON: {HEALTH_FILE}")
    log(f"Status source: {STATUS_FILE}")
    log("NO own Rithmic connection – reads scanner status only!")

    scanner = RithmicHealthScanner()

    # Graceful shutdown
    running = True

    def handle_signal(sig, frame):
        nonlocal running
        log(f"Signal {sig} received, shutting down...")
        running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # First check immediately
    try:
        result = scanner.run_all_checks()
        scanner.write_health_json(result)
        passed = sum(1 for c in result.checks if c.passed)
        log(
            f"Initial check: Trust={result.trust_score:.0f} [{result.status}] | {passed}/10 passed"
        )
        if result.trust_score < 80:
            log(f"  Alerts: {result.alerts}")
    except Exception as e:
        log(f"ERROR on initial check: {e}")
        traceback.print_exc()

    # Loop
    while running:
        time.sleep(CHECK_INTERVAL)
        if not running:
            break

        try:
            result = scanner.run_all_checks()
            scanner.write_health_json(result)
            passed = sum(1 for c in result.checks if c.passed)
            log(
                f"Trust={result.trust_score:.0f} [{result.status}] | {passed}/10 passed"
            )
        except Exception as e:
            log(f"ERROR: {e}")
            traceback.print_exc()

    log("Rithmic Health Scanner stopped.")


if __name__ == "__main__":
    main()
