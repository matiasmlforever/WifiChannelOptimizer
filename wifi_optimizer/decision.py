"""
wifi_optimizer/decision.py
Phase 2 — Congestion scoring and channel selection logic.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# Non-overlapping 2.4 GHz channels
CHANNELS_24: list[int] = [1, 6, 11]

# Preferred 5 GHz channels — non-DFS for stability (no radar-detection events)
CHANNELS_5_PREFERRED: list[int] = [36, 40, 44, 48, 149, 153, 157, 161]

# All standard 5 GHz channels (DFS included as fallback)
CHANNELS_5_ALL: list[int] = CHANNELS_5_PREFERRED + [
    52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136, 140, 144
]

# Hysteresis: only switch if the new channel is at least this much better (relative).
# Default 40% — only worth interrupting the radio for a dramatic improvement.
# Configurable via HYSTERESIS_THRESHOLD in .env (passed in by main.py).
HYSTERESIS_THRESHOLD: float = 0.40


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _band(channel: int) -> str:
    return "2.4" if channel <= 14 else "5"


def _adjacent_channels(channel: int) -> set[int]:
    """
    Returns the set of channels that overlap with `channel`.

    2.4 GHz: ±4 channels overlap at 20 MHz channel width.
    5 GHz:   channels are spaced 4 apart; ±4 steps covers one adjacent block.
    """
    if _band(channel) == "2.4":
        return {c for c in range(max(1, channel - 4), min(14, channel + 4) + 1)}
    return {c for c in range(max(36, channel - 4), channel + 4 + 1, 4)}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_congestion_scores(
    networks: list[dict[str, Any]],
    candidate_channels: list[int],
) -> dict[int, float]:
    """
    Congestion score for each candidate channel =
        sum of dBm power of all networks on that channel AND its adjacent channels.

    More negative score → less congestion → better candidate.
    Using dBm (not count) means a strong nearby network weighs more than several weak ones.
    """
    scores: dict[int, float] = {}
    for ch in candidate_channels:
        zone = _adjacent_channels(ch) | {ch}
        scores[ch] = sum(
            net["signal_dbm"] for net in networks if net["channel"] in zone
        )
    return scores


def best_channel(
    networks: list[dict[str, Any]],
    band: str,
    current_channel: int | None,
    *,
    hysteresis_threshold: float = HYSTERESIS_THRESHOLD,
) -> tuple[int, bool]:
    """
    Returns (optimal_channel, should_change).

    should_change is True only when the improvement over the current channel
    exceeds hysteresis_threshold, avoiding unnecessary radio restarts.
    """
    candidates = CHANNELS_24 if band == "2.4" else CHANNELS_5_PREFERRED
    scores = compute_congestion_scores(networks, candidates)
    log.info("Congestion scores %s GHz: %s", band, scores)

    optimal = min(scores, key=lambda ch: scores[ch])

    if current_channel is None:
        return optimal, True

    current_score = scores.get(current_channel)
    if current_score is None:
        return optimal, True

    optimal_score = scores[optimal]
    improvement = (
        (current_score - optimal_score) / abs(current_score)
        if current_score != 0 else 0.0
    )
    should_change = (optimal != current_channel) and (improvement > hysteresis_threshold)

    log.info(
        "%s GHz — current: ch%s (%.1f), optimal: ch%s (%.1f), "
        "improvement: %.1f%% (threshold %.0f%%) → %s",
        band, current_channel, current_score,
        optimal, optimal_score,
        improvement * 100,
        hysteresis_threshold * 100,
        "CHANGE" if should_change else "keep",
    )
    return optimal, should_change


def log_interference_heatmap(networks: list[dict[str, Any]]) -> None:
    """Log a per-channel RF interference heatmap."""
    channel_data: dict[int, list[float]] = {}
    for net in networks:
        channel_data.setdefault(net["channel"], []).append(net["signal_dbm"])

    lines = ["=== Interference heatmap ==="]
    for ch in sorted(channel_data):
        vals = channel_data[ch]
        avg = sum(vals) / len(vals)
        lines.append(f"  Ch{ch:>3}: {'█' * len(vals):<10} {len(vals)} network(s), avg {avg:.1f} dBm")
    log.info("\n%s", "\n".join(lines))
