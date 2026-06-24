from fastapi import APIRouter, Query
from typing import Optional

from app.db import get_pool

router = APIRouter(prefix="/fusion", tags=["fusion"])

VALID_CORRELATION_COLS = {
    "gws_anomaly", "chirps_anomaly", "ndvi", "sar_rate_mm_year", "drought_index",
}


def _f(val):
    return float(val) if val is not None else None


@router.get("/monitoring")
async def get_monitoring(
    lat: float = Query(...),
    lon: float = Query(...),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    risk_level: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=2000),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        clauses = [
            "lat BETWEEN $1 - 0.3 AND $1 + 0.3",
            "lon BETWEEN $2 - 0.3 AND $2 + 0.3",
        ]
        params: list = [lat, lon]
        idx = 3
        if start_date:
            clauses.append(f"period_date >= ${idx}")
            params.append(start_date)
            idx += 1
        if end_date:
            clauses.append(f"period_date <= ${idx}")
            params.append(end_date)
            idx += 1
        if risk_level:
            clauses.append(f"risk_level = ${idx}")
            params.append(risk_level)
            idx += 1

        where = " AND ".join(clauses)
        params.append(limit)
        rows = await conn.fetch(
            f"SELECT * FROM unified_monitoring WHERE {where} ORDER BY period_date DESC LIMIT ${idx}",
            *params,
        )

        return {
            "count": len(rows),
            "filters": {
                "lat": lat, "lon": lon, "start_date": start_date,
                "end_date": end_date, "risk_level": risk_level, "limit": limit,
            },
            "data": [
                {
                    "period_date": r["period_date"].isoformat() if r["period_date"] else None,
                    "lat": _f(r["lat"]),
                    "lon": _f(r["lon"]),
                    "tws_anomaly": _f(r["tws_anomaly"]),
                    "sms_anomaly": _f(r["sms_anomaly"]),
                    "gws_anomaly": _f(r["gws_anomaly"]),
                    "chirps_precip_mm": _f(r["chirps_precip_mm"]),
                    "chirps_anomaly": _f(r["chirps_anomaly"]),
                    "bmkg_precip_mm": _f(r["bmkg_precip_mm"]),
                    "bmkg_station_id": r["bmkg_station_id"],
                    "bmkg_distance_km": _f(r["bmkg_distance_km"]),
                    "ndvi": _f(r["ndvi"]),
                    "ndvi_location": r["ndvi_location"],
                    "ndvi_distance_km": _f(r["ndvi_distance_km"]),
                    "sar_subsidence_mm": _f(r["sar_subsidence_mm"]),
                    "sar_rate_mm_year": _f(r["sar_rate_mm_year"]),
                    "drought_index": _f(r["drought_index"]),
                    "risk_level": r["risk_level"],
                    "data_completeness": _f(r["data_completeness"]),
                }
                for r in rows
            ],
        }


@router.get("/timeseries")
async def get_timeseries(
    lat: float = Query(...),
    lon: float = Query(...),
    months: int = Query(24, ge=1, le=120),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM unified_monitoring
            WHERE lat BETWEEN $1 - 0.01 AND $1 + 0.01
              AND lon BETWEEN $2 - 0.01 AND $2 + 0.01
              AND period_date >= CURRENT_DATE - make_interval(months => $3)
            ORDER BY period_date
            """,
            lat, lon, months,
        )

        return {
            "lat": lat,
            "lon": lon,
            "months": months,
            "count": len(rows),
            "series": [
                {
                    "period_date": r["period_date"].isoformat() if r["period_date"] else None,
                    "gws_anomaly": _f(r["gws_anomaly"]),
                    "chirps_anomaly": _f(r["chirps_anomaly"]),
                    "ndvi": _f(r["ndvi"]),
                    "sar_rate_mm_year": _f(r["sar_rate_mm_year"]),
                    "drought_index": _f(r["drought_index"]),
                    "risk_level": r["risk_level"],
                    "data_completeness": _f(r["data_completeness"]),
                }
                for r in rows
            ],
        }


@router.get("/summary")
async def get_summary():
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total_records,
                AVG(gws_anomaly) AS avg_gws,
                COUNT(*) FILTER (WHERE risk_level = 'normal') AS normal,
                COUNT(*) FILTER (WHERE risk_level = 'waspada') AS waspada,
                COUNT(*) FILTER (WHERE risk_level = 'kritis') AS kritis,
                COUNT(*) FILTER (WHERE risk_level = 'sangat_kritis') AS sangat_kritis,
                AVG(data_completeness) AS avg_completeness
            FROM unified_monitoring
            """
        )

        return {
            "total_records": row["total_records"],
            "avg_gws_anomaly": _f(row["avg_gws"]),
            "risk_breakdown": {
                "normal": row["normal"],
                "waspada": row["waspada"],
                "kritis": row["kritis"],
                "sangat_kritis": row["sangat_kritis"],
            },
            "avg_data_completeness": _f(row["avg_completeness"]),
        }


@router.get("/correlation")
async def get_correlation(
    sensor1: str = Query(...),
    sensor2: str = Query(...),
):
    if sensor1 not in VALID_CORRELATION_COLS:
        return {"error": f"Invalid sensor1: {sensor1}. Valid: {sorted(VALID_CORRELATION_COLS)}"}
    if sensor2 not in VALID_CORRELATION_COLS:
        return {"error": f"Invalid sensor2: {sensor2}. Valid: {sorted(VALID_CORRELATION_COLS)}"}

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT CORR({sensor1}, {sensor2}) AS correlation, COUNT(*) AS n FROM unified_monitoring WHERE {sensor1} IS NOT NULL AND {sensor2} IS NOT NULL"
        )

        return {
            "sensor1": sensor1,
            "sensor2": sensor2,
            "correlation": _f(row["correlation"]),
            "n": row["n"],
        }
