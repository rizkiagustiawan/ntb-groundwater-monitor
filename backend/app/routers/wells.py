from fastapi import APIRouter, HTTPException, Query
import json
from datetime import datetime
from typing import Optional

from app.db import get_pool
from app.utils import classify_ndvi, ndvi_color, format_period_label

router = APIRouter(tags=["wells"])


@router.get("/wells/geojson")
async def get_wells_geojson(
    kabupaten: Optional[str] = Query(None, description="Filter per kabupaten"),
    status: Optional[str] = Query(None, description="Filter: normal, waspada, kritis, sangat_kritis")
):
    """
    Semua sumur pantau NTB dalam format GeoJSON.
    Siap dikonsumsi langsung oleh MapLibre GL JS.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
            WITH latest_ndvi AS (
                SELECT DISTINCT ON (location)
                    location, ndvi, period_date, vegetation_status, geom AS ndvi_geom
                FROM sentinel2_ndvi
                ORDER BY location, period_date DESC
            )
            SELECT
                wls.id, wls.well_code, wls.name, wls.kecamatan, wls.kabupaten,
                wls.well_type, wls.depth_m, wls.aquifer_type, wls.status,
                wls.water_level_m, wls.measured_at, wls.ph, wls.conductivity_us,
                wls.status_level, wls.geometry,
                n.location AS ndvi_location,
                ROUND(n.ndvi::numeric, 3) AS ndvi_value,
                n.period_date AS ndvi_period,
                n.vegetation_status AS ndvi_vegetation,
                ROUND((ST_Distance(w.geom::geography, n.ndvi_geom::geography) / 1000)::numeric, 1) AS ndvi_distance_km
            FROM well_latest_status wls
            JOIN wells w ON w.id = wls.id
            LEFT JOIN LATERAL (
                SELECT * FROM latest_ndvi
                ORDER BY ndvi_geom <-> w.geom
                LIMIT 1
            ) n ON TRUE
            WHERE 1=1
        """
        params = []
        if kabupaten:
            params.append(kabupaten)
            query += f" AND LOWER(wls.kabupaten) LIKE LOWER(${len(params)})"
            params[-1] = f"%{kabupaten}%"
        if status:
            params.append(status)
            query += f" AND wls.status_level = ${len(params)}"

        rows = await conn.fetch(query, *params)

        features = []
        for row in rows:
            geom = row["geometry"]
            if isinstance(geom, str):
                geom = json.loads(geom)

            # Hitung persentase muka air (0-100%)
            pct = None
            if row["water_level_m"] and row["depth_m"] and row["depth_m"] > 0:
                pct = round((row["water_level_m"] / row["depth_m"]) * 100, 1)

            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "id": row["id"],
                    "well_code": row["well_code"],
                    "name": row["name"],
                    "kecamatan": row["kecamatan"],
                    "kabupaten": row["kabupaten"],
                    "well_type": row["well_type"],
                    "depth_m": float(row["depth_m"]) if row["depth_m"] else None,
                    "aquifer_type": row["aquifer_type"],
                    "water_level_m": float(row["water_level_m"]) if row["water_level_m"] else None,
                    "water_level_pct": pct,
                    "ph": float(row["ph"]) if row["ph"] else None,
                    "conductivity_us": float(row["conductivity_us"]) if row["conductivity_us"] else None,
                    "measured_at": row["measured_at"].isoformat() if row["measured_at"] else None,
                    "status_level": row["status_level"],
                    # warna untuk MapLibre
                    "color": {
                        "normal": "#1D9E75",
                        "waspada": "#BA7517",
                        "kritis": "#E24B4A",
                        "sangat_kritis": "#791F1F",
                        "tidak_ada_data": "#888780"
                    }.get(row["status_level"], "#888780"),
                    # NDVI vegetasi terdekat (Sentinel-2)
                    "ndvi_value": float(row["ndvi_value"]) if row["ndvi_value"] is not None else None,
                    "ndvi_location": row["ndvi_location"],
                    "ndvi_kondisi": classify_ndvi(float(row["ndvi_value"])) if row["ndvi_value"] is not None else None,
                    "ndvi_color": ndvi_color(classify_ndvi(float(row["ndvi_value"]))) if row["ndvi_value"] is not None else None,
                    "ndvi_period": format_period_label(row["ndvi_period"]) if row["ndvi_period"] else None,
                    "ndvi_distance_km": float(row["ndvi_distance_km"]) if row["ndvi_distance_km"] is not None else None,
                    "ndvi_vegetation": row["ndvi_vegetation"]
                }
            })

        return {
            "type": "FeatureCollection",
            "metadata": {
                "title": "Sumur Pantau Air Tanah NTB",
                "legal_reference": "PP No. 43 Tahun 2008",
                "total_wells": len(features),
                "generated_at": datetime.now().isoformat(),
                "crs": "EPSG:4326"
            },
            "features": features
        }


@router.get("/wells/{well_id}/timeseries")
async def get_well_timeseries(
    well_id: int,
    months: int = Query(12, description="Jumlah bulan ke belakang", ge=1, le=60)
):
    """
    Data time series pengukuran untuk satu sumur.
    Digunakan untuk chart di popup dashboard.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        well = await conn.fetchrow(
            "SELECT * FROM wells WHERE id = $1", well_id
        )
        if not well:
            raise HTTPException(status_code=404, detail=f"Sumur ID {well_id} tidak ditemukan")

        measurements = await conn.fetch("""
            SELECT
                DATE_TRUNC('month', measured_at) AS period,
                ROUND(AVG(water_level_m)::numeric, 3) AS avg_water_level,
                ROUND(AVG(water_temp_c)::numeric, 2) AS avg_temp,
                ROUND(AVG(ph)::numeric, 2) AS avg_ph,
                ROUND(AVG(conductivity_us)::numeric, 1) AS avg_conductivity,
                COUNT(*) AS n_measurements
            FROM measurements
            WHERE well_id = $1
              AND measured_at >= NOW() - INTERVAL '1 month' * $2
            GROUP BY DATE_TRUNC('month', measured_at)
            ORDER BY period ASC
        """, well_id, months)

        series = [{
            "period": row["period"].strftime("%Y-%m"),
            "water_level_m": float(row["avg_water_level"]) if row["avg_water_level"] else None,
            "water_temp_c": float(row["avg_temp"]) if row["avg_temp"] else None,
            "ph": float(row["avg_ph"]) if row["avg_ph"] else None,
            "conductivity_us": float(row["avg_conductivity"]) if row["avg_conductivity"] else None,
            "n_measurements": row["n_measurements"]
        } for row in measurements]

        # Statistik
        levels = [s["water_level_m"] for s in series if s["water_level_m"]]
        stats = {}
        if levels:
            stats = {
                "min": round(min(levels), 3),
                "max": round(max(levels), 3),
                "mean": round(sum(levels)/len(levels), 3),
                "trend": "menurun" if len(levels) >= 2 and levels[-1] > levels[0] else "stabil_atau_naik"
            }

        return {
            "well": {
                "id": well["id"],
                "well_code": well["well_code"],
                "name": well["name"],
                "kabupaten": well["kabupaten"],
                "depth_m": float(well["depth_m"]) if well["depth_m"] else None,
                "aquifer_type": well["aquifer_type"]
            },
            "period_months": months,
            "statistics": stats,
            "series": series,
            "legal_note": "Data monitoring sesuai PP No. 43 Tahun 2008 Pasal 15"
        }


@router.get("/wells/esdm/geojson")
async def get_wells_esdm(
    kabupaten: Optional[str] = Query(None),
    fungsi: Optional[str] = Query(None)
):
    """
    280 sumur air tanah real dari ESDM NTB / Badan Geologi.
    Data terverifikasi dengan koordinat GPS lapangan.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
            WITH latest_ndvi AS (
                SELECT DISTINCT ON (location)
                    location, ndvi, period_date, vegetation_status, geom AS ndvi_geom
                FROM sentinel2_ndvi
                ORDER BY location, period_date DESC
            )
            SELECT we.kode_sumur, we.fungsi, we.lat, we.lon,
                   we.dusun, we.desa, we.kecamatan, we.kabupaten,
                   we.dibangun_oleh, we.kedalaman_m, we.tahun_pembangunan,
                   ST_AsGeoJSON(we.geom)::json AS geometry,
                   n.location AS ndvi_location,
                   ROUND(n.ndvi::numeric, 3) AS ndvi_value,
                   n.period_date AS ndvi_period,
                   n.vegetation_status AS ndvi_vegetation,
                   ROUND((ST_Distance(we.geom::geography, n.ndvi_geom::geography) / 1000)::numeric, 1) AS ndvi_distance_km
            FROM wells_esdm we
            LEFT JOIN LATERAL (
                SELECT * FROM latest_ndvi
                ORDER BY ndvi_geom <-> we.geom
                LIMIT 1
            ) n ON TRUE
            WHERE 1=1
        """
        params = []
        if kabupaten:
            params.append(f"%{kabupaten}%")
            query += f" AND LOWER(we.kabupaten) LIKE LOWER(${len(params)})"
        if fungsi:
            params.append(f"%{fungsi}%")
            query += f" AND LOWER(we.fungsi) LIKE LOWER(${len(params)})"

        rows = await conn.fetch(query, *params)

        features = [{
            "type": "Feature",
            "geometry": json.loads(row["geometry"]) if isinstance(row["geometry"], str) else row["geometry"],
            "properties": {
                "kode_sumur": row["kode_sumur"],
                "fungsi": row["fungsi"],
                "kecamatan": row["kecamatan"],
                "kabupaten": row["kabupaten"],
                "desa": row["desa"],
                "dibangun_oleh": row["dibangun_oleh"],
                "kedalaman_m": float(row["kedalaman_m"]) if row["kedalaman_m"] else None,
                "tahun": int(row["tahun_pembangunan"]) if row["tahun_pembangunan"] else None,
                "color": "#00d4ff",
                # NDVI vegetasi terdekat (Sentinel-2)
                "ndvi_value": float(row["ndvi_value"]) if row["ndvi_value"] is not None else None,
                "ndvi_location": row["ndvi_location"],
                "ndvi_kondisi": classify_ndvi(float(row["ndvi_value"])) if row["ndvi_value"] is not None else None,
                "ndvi_color": ndvi_color(classify_ndvi(float(row["ndvi_value"]))) if row["ndvi_value"] is not None else None,
                "ndvi_period": format_period_label(row["ndvi_period"]) if row["ndvi_period"] else None,
                "ndvi_distance_km": float(row["ndvi_distance_km"]) if row["ndvi_distance_km"] is not None else None,
                "ndvi_vegetation": row["ndvi_vegetation"]
            }
        } for row in rows]

        return {
            "type": "FeatureCollection",
            "metadata": {
                "title": "Sumur Air Tanah NTB — Data Real ESDM",
                "source": "Dinas ESDM NTB / Badan Geologi",
                "total": len(features),
                "legal": "PP No. 43 Tahun 2008"
            },
            "features": features
        }