"""
wifi_optimizer/monitor.py

RF Environment Monitor — independent from the optimizer.

Records periodic snapshots of nearby Wi-Fi networks to a SQLite database
so you can analyse how neighbouring networks behave over time
(channel congestion patterns, signal strength trends, peak hours, etc.)

Usage (via main.py --monitor):
    python main.py --monitor                  # record every 30 s indefinitely
    python main.py --monitor --interval 60    # record every 60 s
    python main.py --monitor --duration 3600  # record for 1 hour then stop

Output:
    wifi_monitor.db  (SQLite, git-ignored)

Schema — table: snapshots
    id          INTEGER PRIMARY KEY
    ts          TEXT     ISO-8601 timestamp (UTC)
    ssid        TEXT
    bssid       TEXT
    channel     INTEGER
    band        TEXT     '2.4' or '5'
    signal_pct  INTEGER  0-100 (netsh raw value)
    signal_dbm  REAL     derived via (pct/2)-100

Querying examples (Python):
    import sqlite3, pandas as pd
    con = sqlite3.connect("wifi_monitor.db")
    df  = pd.read_sql("SELECT * FROM snapshots", con, parse_dates=["ts"])
    df.groupby(["channel","band"])["signal_dbm"].mean()
"""
from __future__ import annotations

import logging
import signal
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .scanner import scan_wifi_networks

log = logging.getLogger(__name__)

DB_PATH = Path("wifi_monitor.db")

# DDL — created once on first run
_DDL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT    NOT NULL,
    ssid       TEXT    NOT NULL,
    bssid      TEXT    NOT NULL,
    channel    INTEGER NOT NULL,
    band       TEXT    NOT NULL,
    signal_pct INTEGER NOT NULL,
    signal_dbm REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ts      ON snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_channel ON snapshots(channel);
CREATE INDEX IF NOT EXISTS idx_bssid   ON snapshots(bssid);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_monitor(
    interval_seconds: int = 30,
    duration_seconds: int | None = None,
    db_path: Path = DB_PATH,
) -> None:
    """
    Record RF snapshots every `interval_seconds`.

    Args:
        interval_seconds: How often to scan and persist (default: 30 s).
        duration_seconds: Stop after this many seconds. None = run forever.
                          0 is accepted and means "stop immediately after setup".
        db_path:          Path to the SQLite database file.
    """
    # Validate inputs before touching the DB or starting the loop
    if interval_seconds < 1:
        log.error(
            "Invalid interval_seconds=%d — must be >= 1. Aborting monitor.",
            interval_seconds,
        )
        return
    if duration_seconds is not None and duration_seconds < 0:
        log.error(
            "Invalid duration_seconds=%d — must be >= 0 (or None for unlimited). "
            "Aborting monitor.",
            duration_seconds,
        )
        return

    _init_db(db_path)

    # Use 'is not None' so duration_seconds=0 is treated as an immediate stop,
    # not as "unlimited" (which a falsy check would do).
    deadline = (time.monotonic() + duration_seconds) if duration_seconds is not None else None
    total_snapshots = 0
    scan_count = 0

    log.info("─" * 60)
    log.info(
        "RF Monitor started — interval: %d s | duration: %s | db: %s",
        interval_seconds,
        f"{duration_seconds} s" if duration_seconds else "unlimited",
        db_path,
    )
    log.info("Ctrl+C to stop.")
    log.info("─" * 60)

    # Allow clean Ctrl+C without a traceback
    _stop = [False]
    def _sigint(sig, frame):  # noqa: ANN001
        _stop[0] = True
    signal.signal(signal.SIGINT, _sigint)

    while not _stop[0]:
        if deadline is not None and time.monotonic() >= deadline:
            log.info("Duration reached. Stopping monitor.")
            break

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            networks = scan_wifi_networks()
        except Exception as exc:
            log.warning("Scan failed: %s", exc)
            networks = []

        scan_count += 1
        if networks:
            _persist(db_path, ts, networks)
            total_snapshots += len(networks)
            _print_snapshot_summary(ts, networks, scan_count, total_snapshots)
        else:
            log.warning("[Scan #%d | %s] No networks detected.", scan_count, ts)

        # Sleep in short chunks so Ctrl+C is responsive
        sleep_end = time.monotonic() + interval_seconds
        while not _stop[0] and time.monotonic() < sleep_end:
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(0.5)

    log.info(
        "Monitor stopped. Total scans: %d | Total rows written: %d | DB: %s",
        scan_count, total_snapshots, db_path.resolve(),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _init_db(db_path: Path) -> None:
    """Create the database and table if they don't exist yet."""
    with sqlite3.connect(db_path) as con:
        con.executescript(_DDL)
    log.info("Database ready: %s", db_path.resolve())


def _band(channel: int) -> str:
    return "2.4" if channel <= 14 else "5"


def _persist(db_path: Path, ts: str, networks: list[dict[str, Any]]) -> None:
    rows = [
        (
            ts,
            net["ssid"],
            net["bssid"],
            net["channel"],
            _band(net["channel"]),
            net["signal_percent"],
            net["signal_dbm"],
        )
        for net in networks
    ]
    with sqlite3.connect(db_path) as con:
        con.executemany(
            "INSERT INTO snapshots (ts, ssid, bssid, channel, band, signal_pct, signal_dbm) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _print_snapshot_summary(
    ts: str,
    networks: list[dict[str, Any]],
    scan_count: int,
    total_snapshots: int,
) -> None:
    """Print a compact one-line-per-network summary for the current scan."""
    band_counts: dict[str, int] = {"2.4": 0, "5": 0}
    for net in networks:
        band_counts[_band(net["channel"])] += 1

    log.info(
        "[Scan #%d | %s] %d network(s) — 2.4 GHz: %d | 5 GHz: %d | total rows: %d",
        scan_count, ts,
        len(networks),
        band_counts["2.4"], band_counts["5"],
        total_snapshots,
    )
    for net in sorted(networks, key=lambda n: n["channel"]):
        log.info(
            "  Ch%3d (%s GHz)  %-32s  %s  %.1f dBm",
            net["channel"],
            _band(net["channel"]),
            net["ssid"][:32] or "(hidden)",
            net["bssid"],
            net["signal_dbm"],
        )
