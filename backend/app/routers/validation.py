from fastapi import APIRouter, HTTPException
from datetime import datetime

from app.db import get_pool

router = APIRouter(prefix="/validation", tags=["validation"])


def pearson_r(x: list, y: list) -> float | None:
    import numpy as np
    x, y = np.array(x, dtype=float), np.array(y, dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    if mask.sum() < 3:
        return None
    return float(np.corrcoef(x[mask], y[mask])[0, 1])


def classify_correlation(r: float | None) -> str:
    if r is None:
        return "none"
    abs_r = abs(r)
    if abs_r > 0.7:
        return "strong"
    if abs_r > 0.4:
        return "moderate"
    if abs_r > 0.2:
        return "weak"
    return "none"


@router.get("/compare")
async def compare_all_wells():
    pool = await get_pool()
    async with pool.acquire() as conn:
        wells = await conn.fetch("""
            SELECT w.id, w.well_code, w.name, w.kabupaten,
                   w.geom
            FROM wells w
            WHERE EXISTS (
                SELECT 1 FROM measurements m
                WHERE m.well_id = w.id
                GROUP BY m.well_id
                HAVING COUNT(DISTINCT DATE_TRUNC('month', m.measured_at)) >= 6
            )
        """)

        results = []
        for well in wells:
            well_id = well["id"]

            nearest_grace = await conn.fetchrow("""
                SELECT lat, lon, id
                FROM grace_tws
                ORDER BY geom <-> $1::geometry
                LIMIT 1
            """, well["geom"])

            if not nearest_grace:
                continue

            rows = await conn.fetch("""
                SELECT
                    DATE_TRUNC('month', m.measured_at) AS period,
                    AVG(m.water_level_m) AS avg_wl
                FROM measurements m
                WHERE m.well_id = $1
                GROUP BY DATE_TRUNC('month', m.measured_at)
                ORDER BY period
            """, well_id)

            grace_rows = await conn.fetch("""
                SELECT period_date, tws_anomaly
                FROM grace_tws
                WHERE lat = $1 AND lon = $2
                ORDER BY period_date
            """, nearest_grace["lat"], nearest_grace["lon"])

            wl_by_month = {r["period"].strftime("%Y-%m"): float(r["avg_wl"]) for r in rows if r["avg_wl"] is not None}
            grace_by_month = {r["period_date"].strftime("%Y-%m"): float(r["tws_anomaly"]) for r in grace_rows if r["tws_anomaly"] is not None}

            common_months = sorted(set(wl_by_month) & set(grace_by_month))
            n_months = len(common_months)

            if n_months < 6:
                continue

            x = [wl_by_month[m] for m in common_months]
            y = [grace_by_month[m] for m in common_months]
            r = pearson_r(x, y)
            r_sq = round(r ** 2, 4) if r is not None else None

            results.append({
                "well_id": well_id,
                "well_code": well["well_code"],
                "name": well["name"],
                "kabupaten": well["kabupaten"],
                "n_months": n_months,
                "correlation": round(r, 4) if r is not None else None,
                "r_squared": r_sq,
                "status": classify_correlation(r),
            })

        results.sort(key=lambda x: abs(x["correlation"] or 0), reverse=True)
        return {"wells": results, "total": len(results), "generated_at": datetime.now().isoformat()}


@router.get("/summary")
async def validation_summary():
    pool = await get_pool()
    async with pool.acquire() as conn:
        wells = await conn.fetch("""
            SELECT w.id, w.geom
            FROM wells w
            WHERE EXISTS (
                SELECT 1 FROM measurements m
                WHERE m.well_id = w.id
                GROUP BY m.well_id
                HAVING COUNT(DISTINCT DATE_TRUNC('month', m.measured_at)) >= 6
            )
        """)

        correlations = []
        counts = {"strong": 0, "moderate": 0, "weak": 0, "none": 0}

        for well in wells:
            nearest_grace = await conn.fetchrow("""
                SELECT lat, lon FROM grace_tws
                ORDER BY geom <-> $1::geometry LIMIT 1
            """, well["geom"])
            if not nearest_grace:
                continue

            rows = await conn.fetch("""
                SELECT DATE_TRUNC('month', m.measured_at) AS period, AVG(m.water_level_m) AS avg_wl
                FROM measurements m WHERE m.well_id = $1
                GROUP BY DATE_TRUNC('month', m.measured_at) ORDER BY period
            """, well["id"])

            grace_rows = await conn.fetch("""
                SELECT period_date, tws_anomaly FROM grace_tws
                WHERE lat = $1 AND lon = $2 ORDER BY period_date
            """, nearest_grace["lat"], nearest_grace["lon"])

            wl_map = {r["period"].strftime("%Y-%m"): float(r["avg_wl"]) for r in rows if r["avg_wl"] is not None}
            g_map = {r["period_date"].strftime("%Y-%m"): float(r["tws_anomaly"]) for r in grace_rows if r["tws_anomaly"] is not None}
            common = sorted(set(wl_map) & set(g_map))
            if len(common) < 6:
                continue

            r = pearson_r([wl_map[m] for m in common], [g_map[m] for m in common])
            if r is not None:
                correlations.append(r)
                counts[classify_correlation(r)] += 1

        n = len(correlations)
        return {
            "n_wells_validated": n,
            "avg_correlation": round(sum(correlations) / n, 4) if n else None,
            "n_strong": counts["strong"],
            "n_moderate": counts["moderate"],
            "n_weak": counts["weak"],
            "n_none": counts["none"],
            "generated_at": datetime.now().isoformat(),
        }


@router.get("/well/{well_id}")
async def well_validation_detail(well_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        well = await conn.fetchrow(
            "SELECT id, well_code, name, kabupaten, geom FROM wells WHERE id = $1", well_id
        )
        if not well:
            raise HTTPException(status_code=404, detail=f"Sumur ID {well_id} tidak ditemukan")

        nearest_grace = await conn.fetchrow("""
            SELECT lat, lon FROM grace_tws
            ORDER BY geom <-> $1::geometry LIMIT 1
        """, well["geom"])

        if not nearest_grace:
            raise HTTPException(status_code=404, detail="Tidak ada data GRACE di sekitar sumur ini")

        rows = await conn.fetch("""
            SELECT DATE_TRUNC('month', m.measured_at) AS period, AVG(m.water_level_m) AS avg_wl
            FROM measurements m WHERE m.well_id = $1
            GROUP BY DATE_TRUNC('month', m.measured_at) ORDER BY period
        """, well_id)

        grace_rows = await conn.fetch("""
            SELECT period_date, tws_anomaly FROM grace_tws
            WHERE lat = $1 AND lon = $2 ORDER BY period_date
        """, nearest_grace["lat"], nearest_grace["lon"])

        wl_map = {r["period"].strftime("%Y-%m"): float(r["avg_wl"]) for r in rows if r["avg_wl"] is not None}
        g_map = {r["period_date"].strftime("%Y-%m"): float(r["tws_anomaly"]) for r in grace_rows if r["tws_anomaly"] is not None}
        all_months = sorted(set(wl_map) | set(g_map))

        series = []
        for m in all_months:
            series.append({
                "month": m,
                "water_level_m": wl_map.get(m),
                "grace_gws_anomaly": g_map.get(m),
            })

        common = sorted(set(wl_map) & set(g_map))
        r = pearson_r([wl_map[m] for m in common], [g_map[m] for m in common]) if len(common) >= 3 else None

        return {
            "well": {
                "id": well["id"],
                "well_code": well["well_code"],
                "name": well["name"],
                "kabupaten": well["kabupaten"],
            },
            "grace_grid": {
                "lat": float(nearest_grace["lat"]),
                "lon": float(nearest_grace["lon"]),
            },
            "correlation": round(r, 4) if r is not None else None,
            "r_squared": round(r ** 2, 4) if r is not None else None,
            "status": classify_correlation(r),
            "n_common_months": len(common),
            "series": series,
            "generated_at": datetime.now().isoformat(),
        }
