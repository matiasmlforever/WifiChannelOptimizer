"""
wifi_optimizer/optimizer.py
Core optimization loop — ties scanner, decision engine, quality monitor and router driver together.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
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
    trial_period_seconds:  int   = 300,
    ping_threshold_ms:     int   = 20,
    jitter_threshold_ms:   int   = 15,
    speed_drop_pct:        float = 0.40,
    hysteresis_threshold:  float = 0.40,
    change_cooldown_seconds: int = 3600,
    optimal_windows_path: Path  = WINDOWS_PATH,
) -> None:
    """
    One full optimization cycle:
      0. Check optimal window — if optimal_windows.json exists and the current
         hour is outside the allowed windows, skip the cycle entirely
      1. Scan the RF environment (always — cheap, uses only netsh)
      2. Check change cooldown — skip router interaction if last change
         was less than change_cooldown_seconds ago
      3. Compute congestion scores and select the best channel per band
      4. Skip if the improvement is within the hysteresis window
      5. Measure baseline quality (gateway RTT, jitter, download speed)
      6. Apply the new channels via the router driver
      7. Launch a background thread to monitor quality and revert if degraded

    state keys:
        current_24 (int|None)   — active 2.4 GHz channel
        current_5  (int|None)   — active 5 GHz channel
        last_change_ts (float)  — monotonic timestamp of last router change
    """
    log.info("─" * 60)
    log.info("Optimization cycle started — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # Step 0 — Optimal window check (optional — only when file exists)
    optimal_hours = load_optimal_hours(optimal_windows_path)
    if optimal_hours is not None:
        current_hour = datetime.now().hour
        if current_hour not in optimal_hours:
            next_window = next(
                (h for h in sorted(optimal_hours) if h > current_hour),
                sorted(optimal_hours)[0],   # wraps to next day
            )
            log.info(
                "Outside optimal window (current hour: %02d:00, "
                "optimal hours: %s). Next window: %02d:00. Skipping.",
                current_hour,
                ", ".join(f"{h:02d}:00" for h in sorted(optimal_hours)),
                next_window,
            )
            return
        log.info(
            "Within optimal window (%02d:00 ✅). Proceeding with cycle.",
            current_hour,
        )

    # Step 1 — RF scan (no router interaction)
    networks = scan_wifi_networks()
    if not networks:
        log.warning("No networks detected. Skipping cycle.")
        return

    log.info("Networks detected: %d", len(networks))
    log_interference_heatmap(networks)

    # Step 2 — Cooldown check (before touching the router)
    last_change = state.get("last_change_ts", 0.0)
    elapsed     = time.monotonic() - last_change
    remaining   = change_cooldown_seconds - elapsed
    if remaining > 0 and last_change > 0.0:
        log.info(
            "Change cooldown active — last change was %.0f min ago, "
            "next allowed in %.0f min. Scan-only cycle.",
            elapsed / 60, remaining / 60,
        )
        return

    # Step 3 — Scoring and decision
    best_24, change_24 = best_channel(
        networks, "2.4", state["current_24"],
        hysteresis_threshold=hysteresis_threshold,
    )
    best_5, change_5 = best_channel(
        networks, "5", state["current_5"],
        hysteresis_threshold=hysteresis_threshold,
    )

    apply_24 = best_24 if change_24 else None
    apply_5  = best_5  if change_5  else None

    if not apply_24 and not apply_5:
        log.info("Already on optimal channels (or within hysteresis). No changes.")
        return

    # Step 4 — Baseline quality
    log.info("Measuring baseline quality before channel change…")
    baseline = measure_quality(router.gateway_host)

    prev_24, prev_5 = state["current_24"], state["current_5"]

    if dry_run:
        log.info(
            "[DRY-RUN] Would apply → 2.4 GHz: %s | 5 GHz: %s",
            f"ch{apply_24}" if apply_24 else "unchanged",
            f"ch{apply_5}"  if apply_5  else "unchanged",
        )
        return

    # Step 5 — Apply
    router.apply_channels(apply_24, apply_5, headed=headed)
    state["last_change_ts"] = time.monotonic()

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
            new_24=apply_24,  new_5=apply_5,
            baseline=baseline,
            trial_seconds=trial_period_seconds,
            ping_threshold_ms=ping_threshold_ms,
            jitter_threshold_ms=jitter_threshold_ms,
            speed_drop_pct=speed_drop_pct,
            state=state,
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
        # Reset cooldown so a revert doesn't block immediate re-evaluation
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
