from fastapi import APIRouter, Query
import asyncpg
import os
from typing import List, Optional

router = APIRouter(prefix="/groundwater", tags=["groundwater"])

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@db:5432/ntb_groundwater")

async def get_db():
    return await asyncpg.connect(DATABASE_URL)

@router.get("/timeseries")
async def get_gws_timeseries(
    start_year: int = Query(2002, description="Tahun mulai"),
    end_year: int   = Query(2025, description="Tahun akhir")
):
    """
    Time series anomali Groundwater Storage (GWS) NTB.
    Formula: GWS = TWS (GRACE) - SMS (GLDAS).
    Unit: cm equivalent water height (EWH).
    """
    conn = await get_db()
    try:
        rows = await conn.fetch("""
            SELECT
                t.period_date,
                ROUND(AVG(t.tws_anomaly)::numeric, 2) AS avg_tws,
                ROUND(AVG(s.sms_anomaly)::numeric, 2) AS avg_sms,
                ROUND(AVG(t.tws_anomaly - COALESCE(s.sms_anomaly, 0))::numeric, 2) AS avg_gws
            FROM grace_tws t
            LEFT JOIN gldas_sms s ON 
                EXTRACT(YEAR FROM t.period_date) = s.year AND 
                EXTRACT(MONTH FROM t.period_date) = s.month AND
                ABS(t.lat - s.lat) < 0.01 AND 
                ABS(t.lon - s.lon) < 0.01
            WHERE EXTRACT(YEAR FROM t.period_date) BETWEEN $1 AND $2
            GROUP BY t.period_date
            ORDER BY t.period_date
        """, start_year, end_year)

        data = [{
            "year": row["period_date"].year,
            "month": row["period_date"].month,
            "period": row["period_date"].strftime("%Y-%m"),
            "tws_anomaly": float(row["avg_tws"]),
            "sms_anomaly": float(row["avg_sms"]) if row["avg_sms"] is not None else 0.0,
            "gws_anomaly": float(row["avg_gws"])
        } for row in rows]

        return {
            "metadata": {
                "method": "GRACE_minus_GLDAS",
                "baseline": "2004-2009",
                "unit": "cm_ewh",
                "disclaimer": "GWS estimated. See TRANSPARENCY.md",
                "spatial_resolution": "0.5 degree (~55 km)",
                "reference": "Rodell et al. (2009) doi:10.1038/nature08232"
            },
            "data": data
        }
    finally:
        await conn.close()
