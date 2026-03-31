"""
wifi_optimizer/optimizer.py
Core optimization loop — ties scanner, decision engine, quality monitor and router driver together.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .scanner  import scan_wifi_networks
from .decision import best_channel, log_interference_heatmap
from .quality  import measure_quality, quality_degraded
from .analyzer import load_optimal_hours, WINDOWS_PATH
from .routers.base import BaseRouter

log = logging.getLogger(__name__)


def run_optimization_cycle(
    router:  BaseRouter,
    state:   dict,
    *,
    dry_run: bool  = False,
    headed:  bool  = False,
    trial_period_seconds:        int   = 300,
    ping_threshold_ms:           int   = 20,
    jitter_threshold_ms:         int   = 15,
    speed_drop_pct:              float = 0.40,
    hysteresis_threshold:        float = 0.40,
    change_cooldown_seconds:     int   = 3600,
    optimal_windows_path:        Path  = WINDOWS_PATH,
    baseline_good_ping_ms:       int   = 15,
    baseline_good_jitter_ms:     int   = 5,
    # Emergency mode — active outside optimal windows
    emergency_ping_ms:           int   = 80,
    emergency_jitter_ms:         int   = 50,
    emergency_hysteresis:        float = 0.80,
    emergency_cooldown_seconds:  int   = 7200,
) -> None:
    """
    One full optimization cycle. Behaviour depends on whether the current
    hour falls inside or outside the high-congestion optimal windows:

    NORMAL mode (inside optimal window or no window file):
      0.  Window check — confirm we are in a high-congestion hour
      1.  RF scan
      2.  Cooldown check (change_cooldown_seconds)
      2.5 Baseline guard — skip if connection is already good
          (ping ≤ baseline_good_ping_ms AND jitter ≤ baseline_good_jitter_ms)
      3.  Score channels, apply hysteresis_threshold
      4.  Apply + launch revert monitor

    EMERGENCY mode (outside optimal window, e.g. gaming hours):
      Acts only when the signal has degraded severely enough to ruin a session.
      Uses much stricter thresholds to avoid unnecessary interruptions:
      1.  RF scan
      2.  Emergency cooldown check (emergency_cooldown_seconds — longer)
      2.5 Emergency baseline guard — only proceed if ping > emergency_ping_ms
          OR jitter > emergency_jitter_ms (connection is already bad)
      3.  Score channels, apply emergency_hysteresis (much higher threshold)
      4.  Apply + launch revert monitor

    state keys:
        current_24 (int|None)            — active 2.4 GHz channel
        current_5  (int|None)            — active 5 GHz channel
        last_change_ts (float)           — monotonic ts of last normal change
        last_emergency_change_ts (float) — monotonic ts of last emergency change
        lock (threading.Lock)            — guards all state reads/writes
    """
    log.info("─" * 60)
    log.info(
        "Optimization cycle started — %s",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    )

    lock: threading.Lock = state.setdefault("lock", threading.Lock())

    # Step 0 — Determine operating mode
    optimal_hours  = load_optimal_hours(optimal_windows_path)
    emergency_mode = False

    if optimal_hours is not None:
        current_hour = datetime.now().hour
        if current_hour not in optimal_hours:
            emergency_mode = True
            log.info(
                "Outside optimal window (%02d:00) — EMERGENCY mode active. "
                "Will only act on severe signal degradation.",
                current_hour,
            )
        else:
            log.info(
                "Within optimal window (%02d:00 ✅) — NORMAL mode.",
                current_hour,
            )

    # Step 1 — RF scan (no router interaction)
    networks = scan_wifi_networks()
    if not networks:
        log.warning("No networks detected. Skipping cycle.")
        return

    log.info("Networks detected: %d", len(networks))
    log_interference_heatmap(networks)

    # Step 2 — Cooldown check (uses separate tracker in emergency mode)
    with lock:
        if emergency_mode:
            last_change = state.get("last_emergency_change_ts", 0.0)
            cooldown    = emergency_cooldown_seconds
        else:
            last_change = state.get("last_change_ts", 0.0)
            cooldown    = change_cooldown_seconds

    elapsed   = time.monotonic() - last_change
    remaining = cooldown - elapsed
    if remaining > 0 and last_change > 0.0:
        log.info(
            "%s cooldown active — last change %.0f min ago, "
            "next allowed in %.0f min. Scan-only cycle.",
            "Emergency" if emergency_mode else "Change",
            elapsed / 60, remaining / 60,
        )
        return

    # Step 2.5 — Baseline quality guard
    log.info("Measuring baseline quality…")
    baseline   = measure_quality(router.gateway_host)
    ping_now   = baseline.get("ping_gw_ms") or 0.0
    jitter_now = baseline.get("jitter_ms")  or 0.0
    log.info(
        "Wi-Fi quality → gateway ping: %.1f ms  jitter: %.1f ms  speed: %s Mbps",
        ping_now, jitter_now,
        f"{baseline.get('speed_mbps'):.2f}" if baseline.get("speed_mbps") else "N/A",
    )

    if emergency_mode:
        if ping_now <= emergency_ping_ms and jitter_now <= emergency_jitter_ms:
            log.info(
                "Emergency mode — signal acceptable "
                "(ping %.1f ms ≤ %d ms, jitter %.1f ms ≤ %d ms). "
                "Not worth interrupting — skipping.",
                ping_now, emergency_ping_ms,
                jitter_now, emergency_jitter_ms,
            )
            return
        log.warning(
            "Emergency mode — severe degradation detected "
            "(ping %.1f ms, jitter %.1f ms). Evaluating channel change…",
            ping_now, jitter_now,
        )
        active_hysteresis = emergency_hysteresis
    else:
        if ping_now <= baseline_good_ping_ms and jitter_now <= baseline_good_jitter_ms:
            log.info(
                "Baseline already good (ping %.1f ms ≤ %d ms, "
                "jitter %.1f ms ≤ %d ms). Skipping.",
                ping_now, baseline_good_ping_ms,
                jitter_now, baseline_good_jitter_ms,
            )
            return
        active_hysteresis = hysteresis_threshold

    # Step 3 — Scoring and decision
    with lock:
        cur_24 = state["current_24"]
        cur_5  = state["current_5"]

    best_24, change_24 = best_channel(
        networks, "2.4", cur_24,
        hysteresis_threshold=active_hysteresis,
    )
    best_5, change_5 = best_channel(
        networks, "5", cur_5,
        hysteresis_threshold=active_hysteresis,
    )

    apply_24 = best_24 if change_24 else None
    apply_5  = best_5  if change_5  else None

    if not apply_24 and not apply_5:
        log.info("Already on optimal channels (or within hysteresis). No changes.")
        return

    with lock:
        prev_24 = state["current_24"]
        prev_5  = state["current_5"]

    if dry_run:
        log.info(
            "[DRY-RUN] Would apply → 2.4 GHz: %s | 5 GHz: %s",
            f"ch{apply_24}" if apply_24 else "unchanged",
            f"ch{apply_5}"  if apply_5  else "unchanged",
        )
        return

    # Step 5 — Apply
    router.apply_channels(apply_24, apply_5, headed=headed)
    ts_now = time.monotonic()
    with lock:
        if emergency_mode:
            state["last_emergency_change_ts"] = ts_now
        else:
            state["last_change_ts"] = ts_now
        if apply_24:
            state["current_24"] = apply_24
        if apply_5:
            state["current_5"] = apply_5

    log.info(
        "Channels applied → 2.4 GHz: ch%s | 5 GHz: ch%s",
        state["current_24"], state["current_5"],
    )

    # Step 6 — Background quality monitor
    threading.Thread(
        target=_monitor_and_revert,
        kwargs=dict(
            router=router,
            prev_24=prev_24, prev_5=prev_5,
            new_24=apply_24, new_5=apply_5,
            baseline=baseline,
            trial_seconds=trial_period_seconds,
            ping_threshold_ms=ping_threshold_ms,
            jitter_threshold_ms=jitter_threshold_ms,
            speed_drop_pct=speed_drop_pct,
            state=state,
            lock=lock,
            emergency_mode=emergency_mode,
        ),
        daemon=True,
        name="monitor-revert",
    ).start()


def _monitor_and_revert(
    router:  BaseRouter,
    prev_24: int | None, prev_5: int | None,
    new_24:  int | None, new_5:  int | None,
    baseline: dict,
    trial_seconds:       int,
    ping_threshold_ms:   int,
    jitter_threshold_ms: int,
    speed_drop_pct:      float,
    state:               dict,
    lock:                threading.Lock,
    emergency_mode:      bool,
) -> None:
    log.info("Trial period started (%d min). Monitoring quality…", trial_seconds // 60)
    time.sleep(trial_seconds)

    current = measure_quality(router.gateway_host)
    if quality_degraded(
        baseline, current,
        ping_threshold_ms=ping_threshold_ms,
        jitter_threshold_ms=jitter_threshold_ms,
        speed_drop_pct=speed_drop_pct,
    ):
        log.warning(
            "Quality degraded after %d min. Reverting: "
            "2.4 GHz ch%s→ch%s | 5 GHz ch%s→ch%s",
            trial_seconds // 60,
            new_24, prev_24, new_5, prev_5,
        )
        router.apply_channels(prev_24, prev_5)
        # Reset the matching cooldown tracker so a revert doesn't block
        # immediate re-evaluation.  Emergency changes must reset the
        # emergency tracker — not the normal one — to avoid cross-mode
        # interference.
        with lock:
            if emergency_mode:
                state["last_emergency_change_ts"] = 0.0
            else:
                state["last_change_ts"] = 0.0
            if prev_24 is not None:
                state["current_24"] = prev_24
            if prev_5 is not None:
                state["current_5"] = prev_5
        log.info("Revert complete.")
    else:
        log.info(
            "Quality stable after %d min. Channel change confirmed. "
            "ping=%.1f ms | jitter=%.1f ms | speed=%.2f Mbps",
            trial_seconds // 60,
            current.get("ping_gw_ms") or 0,
            current.get("jitter_ms")  or 0,
            current.get("speed_mbps") or 0,
        )
