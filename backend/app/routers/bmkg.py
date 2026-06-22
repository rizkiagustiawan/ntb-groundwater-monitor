from fastapi import APIRouter, Query
from datetime import date
from typing import Optional

from app.db import get_pool

router = APIRouter(prefix="/bmkg", tags=["bmkg"])


@router.get("/stations")
async def get_stations():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT station_id, station_name, lat, lon, COUNT(*) AS record_count,
                   MIN(date) AS first_date, MAX(date) AS last_date
            FROM bmkg_rainfall
            GROUP BY station_id, station_name, lat, lon
            ORDER BY station_name
        """)
        return {
            "stations": [
                {
                    "station_id": r["station_id"],
                    "station_name": r["station_name"],
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "record_count": r["record_count"],
                    "first_date": r["first_date"].isoformat(),
                    "last_date": r["last_date"].isoformat(),
                }
                for r in rows
            ]
        }


@router.get("/rainfall")
async def get_rainfall(
    station_id: Optional[str] = Query(None),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    limit: int = Query(500, ge=1, le=5000),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        clauses = []
        params = []
        idx = 1
        if station_id:
            clauses.append(f"station_id = ${idx}")
            params.append(station_id)
            idx += 1
        if start_date:
            clauses.append(f"date >= ${idx}")
            params.append(start_date)
            idx += 1
        if end_date:
            clauses.append(f"date <= ${idx}")
            params.append(end_date)
            idx += 1

        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = await conn.fetch(
            f"SELECT * FROM bmkg_rainfall {where} ORDER BY date DESC LIMIT ${idx}",
            *params,
        )
        return {
            "count": len(rows),
            "data": [
                {
                    "station_id": r["station_id"],
                    "station_name": r["station_name"],
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "date": r["date"].isoformat(),
                    "precip_mm": float(r["precip_mm"]) if r["precip_mm"] is not None else None,
                    "humidity_pct": float(r["humidity_pct"]) if r["humidity_pct"] is not None else None,
                    "temp_c": float(r["temp_c"]) if r["temp_c"] is not None else None,
                    "wind_speed_ms": float(r["wind_speed_ms"]) if r["wind_speed_ms"] is not None else None,
                }
                for r in rows
            ],
        }


@router.get("/rainfall/timeseries")
async def get_rainfall_timeseries(
    station_id: Optional[str] = Query(None),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        clause = "WHERE station_id = $1" if station_id else ""
        params = [station_id] if station_id else []
        rows = await conn.fetch(
            f"""
            SELECT station_id, station_name,
                   DATE_TRUNC('month', date) AS month,
                   SUM(precip_mm) AS total_precip,
                   AVG(precip_mm) AS avg_precip,
                   COUNT(*) AS days
            FROM bmkg_rainfall
            {clause}
            GROUP BY station_id, station_name, DATE_TRUNC('month', date)
            ORDER BY station_id, month
            """,
            *params,
        )
        return {
            "series": [
                {
                    "station_id": r["station_id"],
                    "station_name": r["station_name"],
                    "month": r["month"].strftime("%Y-%m"),
                    "total_precip_mm": float(r["total_precip"]) if r["total_precip"] is not None else 0,
                    "avg_precip_mm": float(r["avg_precip"]) if r["avg_precip"] is not None else 0,
                    "days": r["days"],
                }
                for r in rows
            ]
        }


@router.get("/summary")
async def get_summary():
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            WITH monthly AS (
                SELECT station_id, station_name,
                       DATE_TRUNC('month', date) AS month,
                       SUM(precip_mm) AS total_precip
                FROM bmkg_rainfall
                GROUP BY station_id, station_name, DATE_TRUNC('month', date)
            ),
            baseline AS (
                SELECT station_id, AVG(total_precip) AS avg_precip
                FROM monthly
                GROUP BY station_id
            )
            SELECT m.station_id, m.station_name, m.month, m.total_precip,
                   b.avg_precip AS baseline_precip,
                   m.total_precip - b.avg_precip AS anomaly_mm
            FROM monthly m
            JOIN baseline b USING (station_id)
            ORDER BY m.month DESC, m.station_id
            LIMIT 200
        """)
        return {
            "summary": [
                {
                    "station_id": r["station_id"],
                    "station_name": r["station_name"],
                    "month": r["month"].strftime("%Y-%m"),
                    "total_precip_mm": float(r["total_precip"]),
                    "baseline_precip_mm": float(r["baseline_precip"]),
                    "anomaly_mm": float(r["anomaly_mm"]),
                }
                for r in rows
            ]
        }
