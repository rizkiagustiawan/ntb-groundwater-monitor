from fastapi import APIRouter, Query
import asyncpg
import os
from typing import List, Optional

router = APIRouter(prefix="/grace", tags=["grace"])

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@db:5432/ntb_groundwater")

async def get_db():
    return await asyncpg.connect(DATABASE_URL)

@router.get("/timeseries")
async def get_grace_timeseries(
    start_year: int = Query(2020, description="Tahun mulai"),
    end_year: int   = Query(2025, description="Tahun akhir")
):
    """
    Time series rata-rata TWS anomali NTB dari NASA GRACE.
    Unit: cm equivalent water height (EWH).
    """
    conn = await get_db()
    try:
        rows = await conn.fetch("""
            SELECT
                period_date,
                ROUND(AVG(tws_anomaly)::numeric, 2) AS avg_tws,
                ROUND(AVG(uncertainty)::numeric, 2)  AS avg_uncertainty,
                CASE
                    WHEN AVG(tws_anomaly) < -2 THEN 'defisit_kritis'
                    WHEN AVG(tws_anomaly) < 0  THEN 'defisit'
                    WHEN AVG(tws_anomaly) < 2  THEN 'normal'
                    ELSE 'surplus'
                END AS status
            FROM grace_tws
            WHERE EXTRACT(YEAR FROM period_date) BETWEEN $1 AND $2
            GROUP BY period_date
            ORDER BY period_date
        """, start_year, end_year)

        series = [{
            "period":      row["period_date"].strftime("%Y-%m"),
            "tws_anomaly": float(row["avg_tws"]),
            "uncertainty": float(row["avg_uncertainty"]),
            "status":      row["status"]
        } for row in rows]

        return {
            "metadata": {
                "title":      "GRACE/GRACE-FO TWS Anomaly - NTB",
                "unit":       "cm equivalent water height (EWH)",
                "baseline":   "2004-2009 mean",
                "source":     "NASA GRACE RL06.3 Mascon",
                "scientific_note": "TWS includes soil moisture. Use /groundwater/timeseries for GWS estimate."
            },
            "series": series
        }
    finally:
        await conn.close()
