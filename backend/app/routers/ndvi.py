from fastapi import APIRouter, HTTPException
from datetime import datetime

from app.db import get_pool
from app.utils import classify_ndvi, ndvi_color, format_period_label
from app.queries import get_latest_ndvi_rows, get_ndvi_period_range

router = APIRouter(tags=["ndvi"])


@router.get("/ndvi/summary")
async def get_ndvi_summary():
    """
    Ringkasan kondisi vegetasi NTB dari Sentinel-2.
    Referensi: Rouse et al. (1974) NDVI methodology.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await get_latest_ndvi_rows(conn)
        period_range = await get_ndvi_period_range(conn)

        features = []
        for r in rows:
            lat = float(r["lat"])
            lon = float(r["lon"])
            kondisi = classify_ndvi(float(r["latest_ndvi"]))
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon, lat]
                },
                "properties": {
                    "location": r["location"],
                    "kabupaten": r["kabupaten"],
                    "avg_ndvi": float(r["latest_ndvi"]),
                    "min_ndvi": float(r["min_ndvi"]),
                    "max_ndvi": float(r["max_ndvi"]),
                    "kondisi": kondisi,
                    "n_months": r["n_months"],
                    "latest_period": format_period_label(r["latest_period"]),
                    "color": ndvi_color(kondisi)
                }
            })

        return {
            "type": "FeatureCollection",
            "metadata": {
                "title": "Sentinel-2 NDVI - Snapshot Vegetasi Terbaru NTB",
                "source": "Copernicus Sentinel-2 MSI (COPERNICUS/S2_SR_HARMONIZED)",
                "method": "NDVI = (B8-B4)/(B8+B4), Rouse et al. (1974)",
                "period": f"{format_period_label(period_range['min_period'])} s.d. {format_period_label(period_range['max_period'])}" if period_range and period_range["min_period"] and period_range["max_period"] else None,
                "latest_snapshot": format_period_label(period_range["max_period"]) if period_range else None,
                "cloud_filter": "< 30% cloud cover",
                "resolution": "10 meter",
                "summary_basis": "Nilai per lokasi memakai observasi terbaru; min/max adalah rentang historis pada seri waktu yang tersedia."
            },
            "features": features
        }


@router.get("/ndvi/timeseries/{location}")
async def get_ndvi_timeseries(location: str):
    """Time series NDVI untuk satu lokasi."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT period_date, ndvi, ndwi, vegetation_status
            FROM sentinel2_ndvi
            WHERE LOWER(location) = LOWER($1)
            ORDER BY period_date
        """, location)

        if not rows:
            raise HTTPException(status_code=404, detail=f"Lokasi '{location}' tidak ditemukan")

        return {
            "location": location,
            "source": "Sentinel-2 MSI — Google Earth Engine",
            "series": [{
                "period": r["period_date"].strftime("%Y-%m"),
                "ndvi": float(r["ndvi"]),
                "ndwi": float(r["ndwi"]) if r["ndwi"] else None,
                "status": r["vegetation_status"]
            } for r in rows]
        }