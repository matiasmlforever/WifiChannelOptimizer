"""
wifi_optimizer/analyzer.py

RF Window Analyzer — reads wifi_monitor.db, identifies the least-congested
hours of the day, and writes optimal_windows.json for the optimizer to use.

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
        "optimal_hours": [15, 12, 17, 19, 13, 16, 14, 20],
        "ranking": [
            {"rank": 1, "hour": 15, "combined_score": -36712.0,
             "score_24ghz": -27071.5, "score_5ghz": -9640.5},
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

    Args:
        db_path:   SQLite database produced by --monitor.
        out_path:  Output JSON file consumed by the optimizer.
        tz_offset: UTC offset for local time display (default: -3 for Chile).
        top_n:     Number of least-congested hours to include in the window.
    """
    if not db_path.exists():
        log.error(
            "Database not found: %s — run '--monitor' first to collect data.",
            db_path,
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
    from least congested (best for optimizer) to most congested.
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
        ORDER BY combined_score DESC   -- less negative first = less congested
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
    print(f"  Top {top_n} least-congested hours → written to optimal_windows.json")
    print(f"{'='*70}")
    print(f"  {'Rank':>4} | {'Hour':>5} | {'Combined':>10} | {'2.4 GHz':>10} | {'5 GHz':>10} |")
    print(f"  {'-'*62}")

    scores = [r["combined_score"] for r in ranking]
    min_s, max_s = min(scores), max(scores)

    for r in ranking:
        norm   = (r["combined_score"] - max_s) / (min_s - max_s) if min_s != max_s else 0
        bar    = chr(0x2588) * int(norm * 10)
        window = "  ✅ OPTIMAL" if r["hour"] in optimal_hours else ""
        print(
            f"  {r['rank']:>4} | {r['hour']:02d}:00 | "
            f"{r['combined_score']:>10.1f} | "
            f"{r['score_24ghz']:>10.1f} | "
            f"{r['score_5ghz']:>10.1f} | "
            f"{bar:<10}{window}"
        )

    print(f"\n  Optimizer will ONLY act during: "
          f"{', '.join(f'{h:02d}:00' for h in sorted(optimal_hours))}")
    print(f"  To disable window restriction: delete optimal_windows.json\n")
