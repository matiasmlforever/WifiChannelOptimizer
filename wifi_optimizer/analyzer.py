"""
wifi_optimizer/analyzer.py

RF Window Analyzer — reads wifi_monitor.db, identifies the MOST-congested
hours of the day, and writes optimal_windows.json for the optimizer to use.

Rationale:
    The optimizer should act when there IS congestion to escape from.
    During low-congestion hours the connection is already good — changing
    channels there is unnecessary and risks degrading a working setup
    (confirmed by 3 real reverts that all happened with a good baseline).

    During high-congestion hours, neighbouring networks occupy certain
    channels heavily, so switching to a less-populated channel yields a
    real, measurable improvement in ping and jitter.

Flow:
    python main.py --monitor   →  accumulates data in wifi_monitor.db
    python main.py --analyze   →  reads DB, writes optimal_windows.json
    python main.py             →  optimizer respects optimal_windows.json (if present)

optimal_windows.json schema:
    {
        "generated_at": "2026-03-31T12:00:00+00:00",
        "based_on_scans": 4287,
        "tz_offset_hours": -3,
        "top_n": 8,
        "optimal_hours": [2, 3, 4, 7, 1, 0, 6, 10],   ← most congested hours
        "ranking": [
            {"rank": 1, "hour": 2, "combined_score": -128941.5,
             "score_24ghz": -94617.0, "score_5ghz": -34324.5},
            ...
        ]
    }
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .monitor import DB_PATH

log = logging.getLogger(__name__)

WINDOWS_PATH = Path("optimal_windows.json")

# Hours in the top-N will be written to optimal_windows.json.
# The optimizer only acts during these hours when the file is present.
DEFAULT_TOP_N = 8   # out of 24 — covers ~1/3 of the day


def run_analyze(
    db_path:    Path = DB_PATH,
    out_path:   Path = WINDOWS_PATH,
    tz_offset:  int  = -3,
    top_n:      int  = DEFAULT_TOP_N,
) -> None:
    """
    Analyse congestion patterns in `db_path` and write `out_path`.

    Selects the top_n MOST-congested hours — those are the hours where
    interference is highest and switching to a cleaner channel yields a
    real improvement. During quiet hours the connection is already good
    and unnecessary changes risk degrading a working setup.

    Args:
        db_path:   SQLite database produced by --monitor.
        out_path:  Output JSON file consumed by the optimizer.
        tz_offset: UTC offset for local time display (default: -3 for Chile).
        top_n:     Number of most-congested hours to include in the window.
    """
    if not db_path.exists():
        log.error(
            "Database not found: %s — run '--monitor' first to collect data.",
            db_path,
        )
        return

    if not (1 <= top_n <= 24):
        log.error(
            "Invalid top_n=%d — must be between 1 and 24 (hours in a day).",
            top_n,
        )
        return

    if not (-23 <= tz_offset <= 23):
        log.error(
            "Invalid tz_offset=%d — must be between -23 and +23.",
            tz_offset,
        )
        return

    con = sqlite3.connect(db_path)

    total_scans = con.execute("SELECT COUNT(DISTINCT ts) FROM snapshots").fetchone()[0]
    if total_scans == 0:
        log.error("Database is empty — run '--monitor' first to collect data.")
        con.close()
        return

    log.info("Analysing %d scans from %s …", total_scans, db_path)

    ranking = _compute_ranking(con, tz_offset)
    con.close()

    # Top-N most congested = first N entries (most negative scores first)
    optimal_hours = [r["hour"] for r in ranking[:top_n]]

    payload = {
        "generated_at":    datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "based_on_scans":  total_scans,
        "tz_offset_hours": tz_offset,
        "top_n":           top_n,
        "optimal_hours":   optimal_hours,
        "ranking":         ranking,
    }

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Optimal windows written to %s", out_path.resolve())

    _print_summary(ranking, optimal_hours, tz_offset, top_n)


def load_optimal_hours(path: Path = WINDOWS_PATH) -> list[int] | None:
    """
    Return the list of optimal hours from `path`, or None if the file
    does not exist (meaning no window restriction is active).
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data["optimal_hours"]
    except Exception as exc:
        log.warning("Could not read %s: %s — ignoring window restriction.", path, exc)
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_ranking(con: sqlite3.Connection, tz_offset: int) -> list[dict]:
    """
    Compute combined 2.4+5 GHz congestion score per local hour, ranked
    from MOST congested to least congested.

    Most negative score = most congested = best time for the optimizer to act,
    because there is real interference to escape from by switching channels.
    """
    rows = con.execute(f"""
        WITH congestion_by_hour AS (
            SELECT
                (CAST(strftime('%H', ts) AS INTEGER) + {tz_offset} + 24) % 24  AS local_hour,
                band,
                SUM(signal_dbm)                                                 AS band_score
            FROM snapshots
            GROUP BY local_hour, band
        ),
        combined AS (
            SELECT
                local_hour,
                SUM(band_score)                                              AS combined_score,
                SUM(CASE WHEN band = '2.4' THEN band_score ELSE 0 END)      AS score_24,
                SUM(CASE WHEN band = '5'   THEN band_score ELSE 0 END)      AS score_5
            FROM congestion_by_hour
            GROUP BY local_hour
        )
        SELECT
            local_hour,
            ROUND(combined_score, 1)  AS combined_score,
            ROUND(score_24, 1)        AS score_24,
            ROUND(score_5,  1)        AS score_5
        FROM combined
        ORDER BY combined_score ASC    -- most negative first = most congested
    """).fetchall()

    return [
        {
            "rank":           i + 1,
            "hour":           row[0],
            "combined_score": row[1],
            "score_24ghz":    row[2],
            "score_5ghz":     row[3],
        }
        for i, row in enumerate(rows)
    ]


def _print_summary(
    ranking: list[dict],
    optimal_hours: list[int],
    tz_offset: int,
    top_n: int,
) -> None:
    tz_label = f"UTC{tz_offset:+d}"
    print(f"\n{'='*70}")
    print(f"  RF WINDOW ANALYSIS  ({tz_label})")
    print(f"  Top {top_n} MOST-congested hours → optimizer will act during these")
    print(f"  (most interference = most to gain by switching to a cleaner channel)")
    print(f"{'='*70}")
    print(f"  {'Rank':>4} | {'Hour':>5} | {'Combined':>10} | {'2.4 GHz':>10} | {'5 GHz':>10} |")
    print(f"  {'-'*62}")

    scores = [r["combined_score"] for r in ranking]
    min_s, max_s = min(scores), max(scores)

    for r in ranking:
        # norm=1 → most congested (most negative), norm=0 → least congested
        norm   = (r["combined_score"] - max_s) / (min_s - max_s) if min_s != max_s else 0
        bar    = chr(0x2588) * int(norm * 10)
        window = "  ✅ ACT HERE" if r["hour"] in optimal_hours else ""
        print(
            f"  {r['rank']:>4} | {r['hour']:02d}:00 | "
            f"{r['combined_score']:>10.1f} | "
            f"{r['score_24ghz']:>10.1f} | "
            f"{r['score_5ghz']:>10.1f} | "
            f"{bar:<10}{window}"
        )

    print(f"\n  Optimizer will act during (high-congestion windows): "
          f"{', '.join(f'{h:02d}:00' for h in sorted(optimal_hours))}")
    print(f"  Low-congestion hours are SKIPPED — connection is already good there.")
    print(f"  To disable window restriction: delete optimal_windows.json\n")
