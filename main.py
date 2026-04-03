"""
main.py — entry point for WiFi Channel Optimizer.

All business logic lives in the wifi_optimizer/ package.
This file is responsible only for:
  - Loading configuration from .env
  - Instantiating the correct router driver
  - Running the daemon, single-shot, or monitor loop
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from wifi_optimizer.optimizer import run_optimization_cycle
from wifi_optimizer.monitor import run_monitor
from wifi_optimizer.analyzer import run_analyze
from wifi_optimizer.routers.huawei_hg8145x6 import HuaweiHG8145X6

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("wifi_optimizer.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config from .env
# ---------------------------------------------------------------------------
ROUTER_URL  = os.getenv("ROUTER_URL",  "http://192.168.100.1")
ROUTER_USER = os.getenv("ROUTER_USER", "admin")
ROUTER_PASS = os.getenv("ROUTER_PASS", "admin")

# Mini profile selector for emergency behavior outside optimal windows.
# Explicit EMERGENCY_* env vars always override profile defaults.
_PROFILE_DEFAULTS = {
    "balanced": {
        "ping_ms": 40,
        "jitter_ms": 20,
        "hysteresis": 0.50,
        "cooldown_s": 3600,
    },
    "aggressive": {
        "ping_ms": 30,
        "jitter_ms": 12,
        "hysteresis": 0.35,
        "cooldown_s": 1800,
    },
}


def _resolve_profile() -> tuple[str, dict[str, float | int]]:
    raw = os.getenv("GAMING_PROFILE", "balanced").strip().lower()
    if raw not in _PROFILE_DEFAULTS:
        log.warning("Unknown GAMING_PROFILE '%s'; falling back to 'balanced'.", raw)
        raw = "balanced"
    return raw, _PROFILE_DEFAULTS[raw]

SCAN_INTERVAL_SECONDS        = int(os.getenv("SCAN_INTERVAL_SECONDS",        "300"))
CHANGE_COOLDOWN_SECONDS      = int(os.getenv("CHANGE_COOLDOWN_SECONDS",      "3600"))
HYSTERESIS_THRESHOLD         = float(os.getenv("HYSTERESIS_THRESHOLD",       "0.40"))
TRIAL_PERIOD_SECONDS         = int(os.getenv("TRIAL_PERIOD_SECONDS",         "300"))
PING_DEGRADATION_MS          = int(os.getenv("PING_DEGRADATION_MS",          "20"))
JITTER_DEGRADATION_MS        = int(os.getenv("JITTER_DEGRADATION_MS",        "15"))
SPEED_DEGRADATION_PCT        = float(os.getenv("SPEED_DEGRADATION_PCT",      "0.40"))
BASELINE_GOOD_PING_MS        = int(os.getenv("BASELINE_GOOD_PING_MS",        "15"))
BASELINE_GOOD_JITTER_MS      = int(os.getenv("BASELINE_GOOD_JITTER_MS",      "5"))
GAMING_PROFILE, _profile = _resolve_profile()
EMERGENCY_PING_MS            = int(os.getenv("EMERGENCY_PING_MS",            str(_profile["ping_ms"])))
EMERGENCY_JITTER_MS          = int(os.getenv("EMERGENCY_JITTER_MS",          str(_profile["jitter_ms"])))
EMERGENCY_HYSTERESIS         = float(os.getenv("EMERGENCY_HYSTERESIS",       str(_profile["hysteresis"])))
EMERGENCY_COOLDOWN_SECONDS   = int(os.getenv("EMERGENCY_COOLDOWN_SECONDS",   str(_profile["cooldown_s"])))

# ---------------------------------------------------------------------------
# Router driver registry
# To add a new router model:
#   1. Create wifi_optimizer/routers/<your_model>.py  (subclass BaseRouter)
#   2. Add an entry here
#   3. Set ROUTER_DRIVER=<key> in .env
# ---------------------------------------------------------------------------
ROUTER_DRIVERS = {
    "huawei_hg8145x6": HuaweiHG8145X6,
    # "tplink_archer":  TPLinkArcher,   # example future entry
}

_DEFAULT_DRIVER = "huawei_hg8145x6"


def _build_router():
    key = os.getenv("ROUTER_DRIVER", _DEFAULT_DRIVER).lower()
    cls = ROUTER_DRIVERS.get(key)
    if cls is None:
        log.error("Unknown ROUTER_DRIVER '%s'. Available: %s", key, list(ROUTER_DRIVERS))
        sys.exit(1)
    return cls(url=ROUTER_URL, username=ROUTER_USER, password=ROUTER_PASS)


def _get_int_arg(args: list[str], flag: str, *, default: int | None) -> int | None:
    """Return the integer value following `flag` in args, or `default` if `flag` is absent.

    If `flag` is present but missing a value or has a non-integer value,
    log an error and exit with a non-zero status code.
    """
    # Flag not provided at all: use the default as-is.
    if flag not in args:
        return default

    index = args.index(flag)

    # Flag present but no value after it: treat as a CLI error.
    try:
        value_str = args[index + 1]
    except IndexError:
        log.error("Flag %s requires an integer value.", flag)
        sys.exit(1)

    # Flag present with a value that is not an integer: also a CLI error.
    try:
        return int(value_str)
    except ValueError:
        log.error("Invalid value for %s: %r (expected integer).", flag, value_str)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    args    = sys.argv[1:]
    dry_run = "--dry-run" in args
    headed  = "--inspect" in args
    once    = "--once"    in args or headed
    monitor = "--monitor" in args
    analyze = "--analyze" in args

    # ── Monitor mode — completely independent from the optimizer ──────────
    # Checked BEFORE _build_router() so no router connection is attempted.
    if monitor:
        interval = _get_int_arg(args, "--interval", default=30)
        duration = _get_int_arg(args, "--duration", default=None)
        if interval <= 0:
            log.error("--interval must be a positive integer (got %d).", interval)
            sys.exit(1)
        if duration is not None and duration < 0:
            log.error("--duration must be >= 0 (got %d).", duration)
            sys.exit(1)
        run_monitor(interval_seconds=interval, duration_seconds=duration)
        return

    # ── Analyze mode — reads DB, writes optimal_windows.json ──────────────
    if analyze:
        tz_offset = _get_int_arg(args, "--tz-offset", default=-3)
        top_n     = _get_int_arg(args, "--top-n",     default=8)
        run_analyze(tz_offset=tz_offset, top_n=top_n)
        return

    # ── Optimizer mode ────────────────────────────────────────────────────
    if dry_run:
        log.info("DRY-RUN mode: router will NOT be modified.")
    if headed:
        log.info("INSPECT mode: Chromium will open in headed mode.")

    log.info(
        "Gaming profile: %s | Emergency thresholds → ping>%d ms, jitter>%.1f ms, hysteresis %.0f%%, cooldown %d min",
        GAMING_PROFILE,
        EMERGENCY_PING_MS,
        EMERGENCY_JITTER_MS,
        EMERGENCY_HYSTERESIS * 100,
        EMERGENCY_COOLDOWN_SECONDS // 60,
    )

    router = _build_router()
    log.info("Router driver: %s  (%s)", router.__class__.__name__, router.url)

    state: dict = {
        "current_24":               None,
        "current_5":                None,
        "last_change_ts":           0.0,
        "last_emergency_change_ts": 0.0,
    }
    if not dry_run:
        state["current_24"], state["current_5"] = router.read_channels()

    cycle_kwargs = dict(
        router=router,
        state=state,
        dry_run=dry_run,
        headed=headed,
        trial_period_seconds=TRIAL_PERIOD_SECONDS,
        ping_threshold_ms=PING_DEGRADATION_MS,
        jitter_threshold_ms=JITTER_DEGRADATION_MS,
        speed_drop_pct=SPEED_DEGRADATION_PCT,
        hysteresis_threshold=HYSTERESIS_THRESHOLD,
        change_cooldown_seconds=CHANGE_COOLDOWN_SECONDS,
        baseline_good_ping_ms=BASELINE_GOOD_PING_MS,
        baseline_good_jitter_ms=BASELINE_GOOD_JITTER_MS,
        emergency_ping_ms=EMERGENCY_PING_MS,
        emergency_jitter_ms=EMERGENCY_JITTER_MS,
        emergency_hysteresis=EMERGENCY_HYSTERESIS,
        emergency_cooldown_seconds=EMERGENCY_COOLDOWN_SECONDS,
    )

    if once:
        log.info("Single-shot mode.")
        run_optimization_cycle(**cycle_kwargs)
    else:
        log.info(
            "Daemon mode started. Scan interval: %d s. Ctrl+C to stop. "
            "Flags: --once | --dry-run | --inspect | --analyze",
            SCAN_INTERVAL_SECONDS,
        )
        while True:
            try:
                run_optimization_cycle(**cycle_kwargs)
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                break
            except Exception as exc:
                log.error("Cycle error: %s", exc)
            log.info("Next scan in %d s…", SCAN_INTERVAL_SECONDS)
            try:
                time.sleep(SCAN_INTERVAL_SECONDS)
            except KeyboardInterrupt:
                log.info("Stopped by user.")
                break


if __name__ == "__main__":
    main()

