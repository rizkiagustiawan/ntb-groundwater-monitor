import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.db import get_pool

router = APIRouter(prefix="/grace", tags=["grace"])


@router.get("/tws")
async def get_grace_tws(
    start_date: Optional[str] = Query(None, description="Format: YYYY-MM"),
    end_date: Optional[str] = Query(None, description="Format: YYYY-MM"),
    bbox: Optional[str] = Query(None, description="lon_min,lat_min,lon_max,lat_max")
):
    """
    Data anomali Terrestrial Water Storage dari NASA GRACE/GRACE-FO.
    Unit: cm equivalent water height (EWH).
    Referensi: GRACE RL06 Mascon Solutions (Watkins et al., 2015)
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
            SELECT period_date, lat, lon, tws_anomaly, uncertainty,
                   ST_AsGeoJSON(geom)::json AS geometry
            FROM grace_tws
            WHERE 1=1
        """
        params = []

        if start_date:
            params.append(f"{start_date}-01")
            query += f" AND period_date >= ${len(params)}::date"
        if end_date:
            params.append(f"{end_date}-01")
            query += f" AND period_date <= ${len(params)}::date"
        if bbox:
            try:
                lon_min, lat_min, lon_max, lat_max = map(float, bbox.split(","))
                params.append(lon_min); params.append(lat_min)
                params.append(lon_max); params.append(lat_max)
                i = len(params)
                query += f" AND ST_Within(geom, ST_MakeEnvelope(${i-3},${i-2},${i-1},${i},4326))"
            except ValueError:
                raise HTTPException(status_code=400, detail="Format bbox: lon_min,lat_min,lon_max,lat_max")

        query += " ORDER BY period_date, lat, lon"
        rows = await conn.fetch(query, *params)

        features = [{
            "type": "Feature",
            "geometry": json.loads(row["geometry"]) if isinstance(row["geometry"], str) else row["geometry"],
            "properties": {
                "period": row["period_date"].strftime("%Y-%m"),
                "tws_anomaly_cm": float(row["tws_anomaly"]) if row["tws_anomaly"] else None,
                "uncertainty_cm": float(row["uncertainty"]) if row["uncertainty"] else None,
                "lat": float(row["lat"]),
                "lon": float(row["lon"])
            }
        } for row in rows]

        return {
            "type": "FeatureCollection",
            "metadata": {
                "title": "GRACE/GRACE-FO Terrestrial Water Storage Anomaly - NTB",
                "data_source": "NASA GRACE RL06 Mascon Solutions",
                "unit": "cm equivalent water height (EWH)",
                "reference": "Watkins et al. (2015), doi:10.1002/2014JB011547",
                "interpretation": "Nilai negatif = defisit simpanan air daratan regional; nilai positif = surplus terhadap baseline 2004-2009.",
                "usage_note": "GRACE TWS adalah indikator regional perubahan simpanan air daratan, bukan pembacaan langsung muka air sumur.",
                "total_records": len(features)
            },
            "features": features
        }


@router.get("/timeseries")
async def get_grace_timeseries(
    start_year: int = Query(2020, description="Tahun mulai"),
    end_year: int = Query(2025, description="Tahun akhir")
):
    """Time series rata-rata TWS anomali NTB dari NASA GRACE."""
    pool = await get_pool()
    async with pool.acquire() as conn:
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
