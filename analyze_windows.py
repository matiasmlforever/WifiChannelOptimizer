import sqlite3

con = sqlite3.connect("wifi_monitor.db")

# ── 2.4 GHz congestion per hour (local time Chile = UTC-3) ──────────────
rows_24 = con.execute("""
    SELECT
        (CAST(strftime('%H', ts) AS INTEGER) - 3 + 24) % 24  AS hour_cl,
        COUNT(DISTINCT bssid)                                  AS unique_nets,
        ROUND(AVG(signal_dbm), 1)                              AS avg_dbm,
        ROUND(SUM(signal_dbm), 1)                              AS congestion_score
    FROM snapshots
    WHERE band = '2.4'
    GROUP BY hour_cl
    ORDER BY hour_cl
""").fetchall()

rows_5 = con.execute("""
    SELECT
        (CAST(strftime('%H', ts) AS INTEGER) - 3 + 24) % 24  AS hour_cl,
        COUNT(DISTINCT bssid)                                  AS unique_nets,
        ROUND(AVG(signal_dbm), 1)                              AS avg_dbm,
        ROUND(SUM(signal_dbm), 1)                              AS congestion_score
    FROM snapshots
    WHERE band = '5'
    GROUP BY hour_cl
    ORDER BY hour_cl
""").fetchall()


def print_band(label, rows):
    scores = [r[3] for r in rows]
    min_s, max_s = min(scores), max(scores)
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"{'='*70}")
    print(f"  {'Hora':>5} | {'Redes':>5} | {'Avg dBm':>8} | {'Score':>10} | Congestion")
    print(f"  {'-'*62}")
    for hour, nets, avg, score in rows:
        norm   = (score - max_s) / (min_s - max_s) if min_s != max_s else 0
        bar    = chr(0x2588) * int(norm * 20)
        if norm < 0.25:
            tag = "  ✅ MEJOR VENTANA"
        elif norm > 0.75:
            tag = "  ❌ MAS CONGESTIONADO"
        else:
            tag = ""
        print(f"  {hour:02d}:00 | {nets:5} | {avg:8.1f} | {score:10.1f} | {bar:<20}{tag}")


print_band("2.4 GHz — Congestión por hora (hora Chile)", rows_24)
print_band("5   GHz — Congestión por hora (hora Chile)", rows_5)

# ── Ranking de mejores ventanas combinadas ──────────────────────────────
print(f"\n{'='*70}")
print("  RANKING — Mejores ventanas para ejecutar el optimizador")
print(f"  (score combinado 2.4 GHz + 5 GHz, menos negativo = menos congestion)")
print(f"{'='*70}")

d24 = {r[0]: r[3] for r in rows_24}
d5  = {r[0]: r[3] for r in rows_5}
hours = sorted(set(d24) | set(d5))
combined = [(h, d24.get(h, 0) + d5.get(h, 0)) for h in hours]
combined.sort(key=lambda x: x[1], reverse=True)   # menos negativo primero

print(f"\n  {'Rank':>4} | {'Hora':>5} | {'Score combinado':>16}")
print(f"  {'-'*35}")
for rank, (hour, score) in enumerate(combined, 1):
    tag = "  ← TOP" if rank <= 3 else ""
    print(f"  {rank:>4} | {hour:02d}:00 | {score:16.1f}{tag}")
