from fastapi import APIRouter, Query

from app.db import get_pool

router = APIRouter(prefix="/groundwater", tags=["groundwater"])


@router.get("/timeseries")
async def get_gws_timeseries(
    start_year: int = Query(2002, description="Tahun mulai"),
    end_year: int = Query(2025, description="Tahun akhir")
):
    """Time series anomali Groundwater Storage (GWS) NTB."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                t.period_date,
                ROUND(AVG(t.tws_anomaly)::numeric, 2) AS avg_tws,
                ROUND(AVG(s.sms_anomaly)::numeric, 2) AS avg_sms,
                ROUND(AVG(t.tws_anomaly - COALESCE(s.sms_anomaly, 0))::numeric, 2) AS avg_gws,
                ROUND(AVG(t.uncertainty)::numeric, 2) AS avg_tws_unc,
                ROUND((ABS(AVG(s.sms_anomaly)) * 0.25)::numeric, 2) AS avg_sms_unc,
                ROUND(SQRT(
                    COALESCE(AVG(t.uncertainty), 0) ^ 2 +
                    COALESCE(ABS(AVG(s.sms_anomaly)) * 0.25, 0) ^ 2
                )::numeric, 2) AS avg_gws_unc
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
            "gws_anomaly": float(row["avg_gws"]),
            "tws_uncertainty": float(row["avg_tws_unc"]) if row["avg_tws_unc"] is not None else None,
            "sms_uncertainty": float(row["avg_sms_unc"]) if row["avg_sms_unc"] is not None else 0.0,
            "gws_uncertainty": float(row["avg_gws_unc"]) if row["avg_gws_unc"] is not None else None
        } for row in rows]

        return {
            "metadata": {
                "method": "GRACE_minus_GLDAS",
                "baseline": "2004-2009",
                "unit": "cm_ewh",
                "disclaimer": "GWS estimated. See TRANSPARENCY.md",
                "spatial_resolution": "0.5 degree (~55 km)",
                "reference": "Rodell et al. (2009) doi:10.1038/nature08232",
                "uncertainty_method": "Error propagation: σ_GWS = √(σ_TWS² + σ_SMS²). σ_TWS from GRACE, σ_SMS = 25% of SMS value.",
                "sms_uncertainty_fraction": 0.25
            },
            "data": data
        }
