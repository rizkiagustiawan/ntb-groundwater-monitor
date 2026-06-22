from fastapi import APIRouter, Query

from app.db import get_pool

router = APIRouter(prefix="/chirps", tags=["chirps"])


@router.get("/timeseries")
async def get_timeseries(
    start_year: int = Query(2020, description="Tahun mulai"),
    end_year: int = Query(2025, description="Tahun akhir"),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH monthly AS (
                SELECT year, month, AVG(precip_mm) AS avg_precip
                FROM chirps_precip
                WHERE year BETWEEN $1 AND $2
                GROUP BY year, month
            ),
            baseline AS (
                SELECT AVG(precip_mm) AS mean_precip FROM chirps_precip
            )
            SELECT m.year, m.month,
                   ROUND(m.avg_precip::numeric, 2) AS avg_precip_mm,
                   ROUND((m.avg_precip - b.mean_precip)::numeric, 2) AS anomaly_mm
            FROM monthly m CROSS JOIN baseline b
            ORDER BY m.year, m.month
            """,
            start_year,
            end_year,
        )
        return {
            "series": [
                {
                    "period": f"{r['year']}-{r['month']:02d}",
                    "avg_precip_mm": float(r["avg_precip_mm"]),
                    "anomaly_mm": float(r["anomaly_mm"]),
                }
                for r in rows
            ]
        }


@router.get("/summary")
async def get_summary():
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total_records,
                   MIN(year) AS min_year, MAX(year) AS max_year,
                   ROUND(AVG(precip_mm)::numeric, 2) AS avg_monthly_precip,
                   ROUND(AVG(precip_mm)::numeric, 2) AS baseline_mean
            FROM chirps_precip
            """
        )
        return {
            "total_records": row["total_records"],
            "date_range": f"{row['min_year']}-{row['max_year']}",
            "avg_monthly_precip_mm": float(row["avg_monthly_precip"]),
            "baseline_mean_mm": float(row["baseline_mean"]),
        }


@router.get("/anomaly")
async def get_anomaly(
    start_year: int = Query(2020, description="Tahun mulai"),
    end_year: int = Query(2025, description="Tahun akhir"),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            WITH monthly AS (
                SELECT year, month, AVG(precip_mm) AS avg_precip
                FROM chirps_precip
                WHERE year BETWEEN $1 AND $2
                GROUP BY year, month
            ),
            baseline AS (
                SELECT AVG(precip_mm) AS mean_precip FROM chirps_precip
            )
            SELECT m.year, m.month,
                   ROUND(m.avg_precip::numeric, 2) AS precip_mm,
                   ROUND((m.avg_precip - b.mean_precip)::numeric, 2) AS anomaly_mm,
                   CASE
                       WHEN (m.avg_precip - b.mean_precip) > 50  THEN 'surplus'
                       WHEN (m.avg_precip - b.mean_precip) > 0   THEN 'normal'
                       WHEN (m.avg_precip - b.mean_precip) > -50 THEN 'deficit'
                       ELSE 'critical'
                   END AS status
            FROM monthly m CROSS JOIN baseline b
            ORDER BY m.year, m.month
            """,
            start_year,
            end_year,
        )
        return {
            "series": [
                {
                    "period": f"{r['year']}-{r['month']:02d}",
                    "precip_mm": float(r["precip_mm"]),
                    "anomaly_mm": float(r["anomaly_mm"]),
                    "status": r["status"],
                }
                for r in rows
            ]
        }
