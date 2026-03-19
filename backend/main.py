"""
NTB Groundwater Monitoring API
Landasan hukum: PP No. 43 Tahun 2008 tentang Air Tanah
Referensi ilmiah: NASA GRACE RL06 Mascon Solutions
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import asyncpg
import os
import json
from datetime import datetime, date
from typing import Optional
import math

app = FastAPI(
    title="NTB Groundwater Monitoring API",
    description="Platform monitoring air tanah Nusa Tenggara Barat berbasis satelit NASA GRACE dan data lapangan. Referensi: PP 43/2008, Perpres 33/2018.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@db:5432/ntb_groundwater")


async def get_db():
    return await asyncpg.connect(DATABASE_URL)


# ============================================================
# ROOT
# ============================================================
@app.get("/")
async def root():
    return {
        "platform": "NTB Groundwater Monitoring",
        "version": "1.0.0",
        "legal_basis": ["PP No. 43 Tahun 2008", "Perpres No. 33 Tahun 2018", "PerMenLHK P.68/2016"],
        "data_sources": ["NASA GRACE RL06 Mascon", "Sentinel-2 MSI", "Data lapangan ESDM NTB"],
        "coverage": "Nusa Tenggara Barat, Indonesia",
        "docs": "/docs"
    }


# ============================================================
# ENDPOINT 1: Semua sumur sebagai GeoJSON
# ============================================================
@app.get("/wells/geojson")
async def get_wells_geojson(
    kabupaten: Optional[str] = Query(None, description="Filter per kabupaten"),
    status: Optional[str] = Query(None, description="Filter: normal, waspada, kritis, sangat_kritis")
):
    """
    Semua sumur pantau NTB dalam format GeoJSON.
    Siap dikonsumsi langsung oleh MapLibre GL JS.
    """
    conn = await get_db()
    try:
        query = """
            SELECT
                id, well_code, name, kecamatan, kabupaten,
                well_type, depth_m, aquifer_type, status,
                water_level_m, measured_at, ph, conductivity_us,
                status_level, geometry
            FROM well_latest_status
            WHERE 1=1
        """
        params = []
        if kabupaten:
            params.append(kabupaten)
            query += f" AND LOWER(kabupaten) LIKE LOWER(${ len(params)})"
            params[-1] = f"%{kabupaten}%"
        if status:
            params.append(status)
            query += f" AND status_level = ${ len(params)}"

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
                    }.get(row["status_level"], "#888780")
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
    finally:
        await conn.close()


# ============================================================
# ENDPOINT 2: Time series satu sumur
# ============================================================
@app.get("/wells/{well_id}/timeseries")
async def get_well_timeseries(
    well_id: int,
    months: int = Query(12, description="Jumlah bulan ke belakang", ge=1, le=60)
):
    """
    Data time series pengukuran untuk satu sumur.
    Digunakan untuk chart di popup dashboard.
    """
    conn = await get_db()
    try:
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
    finally:
        await conn.close()


# ============================================================
# ENDPOINT 3: Data GRACE TWS anomali
# ============================================================
@app.get("/grace/tws")
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
    conn = await get_db()
    try:
        query = """
            SELECT period_date, lat, lon, tws_anomaly, uncertainty,
                   ST_AsGeoJSON(geom)::json AS geometry
            FROM grace_tws
            WHERE 1=1
        """
        params = []

        if start_date:
            params.append(f"{start_date}-01")
            query += f" AND period_date >= ${ len(params)}::date"
        if end_date:
            params.append(f"{end_date}-01")
            query += f" AND period_date <= ${ len(params)}::date"
        if bbox:
            try:
                lon_min, lat_min, lon_max, lat_max = map(float, bbox.split(","))
                params.append(lon_min); params.append(lat_min)
                params.append(lon_max); params.append(lat_max)
                i = len(params)
                query += f" AND ST_Within(geom, ST_MakeEnvelope(${i-3},${i-2},${i-1},${i},4326))"
            except:
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
                "title": "GRACE/GRACE-FO Terrestrial Water Storage Anomaly — NTB",
                "data_source": "NASA GRACE RL06 Mascon Solutions",
                "unit": "cm equivalent water height (EWH)",
                "reference": "Watkins et al. (2015), doi:10.1002/2014JB011547",
                "interpretation": "Nilai negatif = defisit air, nilai positif = surplus air dibanding rata-rata 2004-2009",
                "total_records": len(features)
            },
            "features": features
        }
    finally:
        await conn.close()


# ============================================================
# ENDPOINT 4: Ringkasan status per kabupaten
# ============================================================
@app.get("/summary/kabupaten")
async def get_summary_by_kabupaten():
    """Ringkasan kondisi air tanah per kabupaten untuk kartu dashboard."""
    conn = await get_db()
    try:
        rows = await conn.fetch("""
            SELECT
                kabupaten,
                COUNT(*) AS total_wells,
                COUNT(*) FILTER (WHERE status_level = 'normal') AS normal,
                COUNT(*) FILTER (WHERE status_level = 'waspada') AS waspada,
                COUNT(*) FILTER (WHERE status_level = 'kritis') AS kritis,
                COUNT(*) FILTER (WHERE status_level = 'sangat_kritis') AS sangat_kritis,
                COUNT(*) FILTER (WHERE status_level = 'tidak_ada_data') AS no_data,
                ROUND(AVG(water_level_m)::numeric, 2) AS avg_water_level_m,
                ROUND(AVG(ph)::numeric, 2) AS avg_ph
            FROM well_latest_status
            GROUP BY kabupaten
            ORDER BY kabupaten
        """)

        result = []
        for row in rows:
            total = row["total_wells"]
            kritis_count = (row["kritis"] or 0) + (row["sangat_kritis"] or 0)
            # Level risiko keseluruhan kabupaten
            if total > 0:
                kritis_pct = (kritis_count / total) * 100
                if kritis_pct >= 50:
                    risk = "KRITIS"
                elif kritis_pct >= 25:
                    risk = "WASPADA"
                else:
                    risk = "NORMAL"
            else:
                risk = "TIDAK_ADA_DATA"

            result.append({
                "kabupaten": row["kabupaten"],
                "total_wells": total,
                "status_breakdown": {
                    "normal": row["normal"] or 0,
                    "waspada": row["waspada"] or 0,
                    "kritis": row["kritis"] or 0,
                    "sangat_kritis": row["sangat_kritis"] or 0,
                    "tidak_ada_data": row["no_data"] or 0
                },
                "avg_water_level_m": float(row["avg_water_level_m"]) if row["avg_water_level_m"] else None,
                "avg_ph": float(row["avg_ph"]) if row["avg_ph"] else None,
                "overall_risk": risk
            })

        return {
            "generated_at": datetime.now().isoformat(),
            "total_kabupaten": len(result),
            "legal_basis": "PP No. 43 Tahun 2008",
            "data": result
        }
    finally:
        await conn.close()


# ============================================================
# ENDPOINT 5: Health check
# ============================================================
@app.get("/health")
async def health():
    try:
        conn = await get_db()
        await conn.fetchval("SELECT 1")
        await conn.close()
        return {"status": "ok", "database": "connected", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database error: {str(e)}")


# ============================================================
# ENDPOINT 6: GRACE TWS time series NTB (untuk chart dashboard)
# ============================================================
@app.get("/grace/timeseries")
async def get_grace_timeseries(
    start_year: int = Query(2020, description="Tahun mulai"),
    end_year: int   = Query(2025, description="Tahun akhir")
):
    """
    Time series rata-rata TWS anomali NTB dari NASA GRACE.
    Unit: cm equivalent water height (EWH).
    Referensi: Watkins et al. (2015), doi:10.1002/2014JB011547
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
            WHERE EXTRACT(YEAR FROM period_date)
                  BETWEEN $1 AND $2
            GROUP BY period_date
            ORDER BY period_date
        """, start_year, end_year)

        series = [{
            "period":      row["period_date"].strftime("%Y-%m"),
            "tws_cm":      float(row["avg_tws"]),
            "uncertainty": float(row["avg_uncertainty"]),
            "status":      row["status"],
            "color": {
                "defisit_kritis": "#791F1F",
                "defisit":        "#E24B4A",
                "normal":         "#BA7517",
                "surplus":        "#1D9E75"
            }.get(row["status"], "#888780")
        } for row in rows]

        # Hitung statistik
        vals = [s["tws_cm"] for s in series]
        defisit_months = [s for s in series if "defisit" in s["status"]]

        return {
            "metadata": {
                "title":      "GRACE/GRACE-FO TWS Anomaly — NTB",
                "unit":       "cm equivalent water height (EWH)",
                "baseline":   "2004-2009 mean",
                "source":     "NASA GRACE RL06.3 Mascon",
                "reference":  "Watkins et al. (2015)",
                "coverage":   "Nusa Tenggara Barat (4x8 grid, 0.5deg resolution)",
                "period":     f"{start_year}–{end_year}"
            },
            "statistics": {
                "mean_tws":       round(sum(vals)/len(vals), 2) if vals else None,
                "min_tws":        round(min(vals), 2) if vals else None,
                "max_tws":        round(max(vals), 2) if vals else None,
                "defisit_months": len(defisit_months),
                "total_months":   len(series)
            },
            "series": series
        }
    finally:
        await conn.close()


# ============================================================
# ENDPOINT 7: NDVI Sentinel-2 per lokasi
# ============================================================
@app.get("/ndvi/summary")
async def get_ndvi_summary():
    """
    Ringkasan kondisi vegetasi NTB dari Sentinel-2.
    Referensi: Rouse et al. (1974) NDVI methodology.
    """
    conn = await get_db()
    try:
        rows = await conn.fetch("""
            SELECT
                location, kabupaten, lat, lon,
                ROUND(AVG(ndvi)::numeric, 3) AS avg_ndvi,
                ROUND(MIN(ndvi)::numeric, 3) AS min_ndvi,
                ROUND(MAX(ndvi)::numeric, 3) AS max_ndvi,
                CASE
                    WHEN AVG(ndvi) >= 0.5 THEN 'lebat'
                    WHEN AVG(ndvi) >= 0.3 THEN 'sedang'
                    WHEN AVG(ndvi) >= 0.1 THEN 'jarang'
                    ELSE 'kritis'
                END AS kondisi,
                COUNT(*) AS n_months
            FROM sentinel2_ndvi
            GROUP BY location, kabupaten, lat, lon
            ORDER BY avg_ndvi DESC
        """)

        features = [{
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(r["lon"]), float(r["lat"])]
            },
            "properties": {
                "location":  r["location"],
                "kabupaten": r["kabupaten"],
                "avg_ndvi":  float(r["avg_ndvi"]),
                "min_ndvi":  float(r["min_ndvi"]),
                "max_ndvi":  float(r["max_ndvi"]),
                "kondisi":   r["kondisi"],
                "n_months":  r["n_months"],
                "color": {
                    "lebat":  "#1D9E75",
                    "sedang": "#639922",
                    "jarang": "#BA7517",
                    "kritis": "#E24B4A"
                }.get(r["kondisi"], "#888780")
            }
        } for r in rows]

        return {
            "type": "FeatureCollection",
            "metadata": {
                "title": "Sentinel-2 NDVI — Kondisi Vegetasi NTB",
                "source": "Copernicus Sentinel-2 MSI (COPERNICUS/S2_SR_HARMONIZED)",
                "method": "NDVI = (B8-B4)/(B8+B4), Rouse et al. (1974)",
                "period": "2023-2024",
                "cloud_filter": "< 30% cloud cover",
                "resolution": "10 meter"
            },
            "features": features
        }
    finally:
        await conn.close()


@app.get("/ndvi/timeseries/{location}")
async def get_ndvi_timeseries(location: str):
    """Time series NDVI untuk satu lokasi."""
    conn = await get_db()
    try:
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
    finally:
        await conn.close()


# ============================================================
# ENDPOINT 8: AI Interpretation via Kimi API
# ============================================================
from openai import OpenAI as KimiClient
import os

@app.get("/ai/interpret")
async def ai_interpret_ntb():
    """
    Interpretasi otomatis kondisi air tanah NTB menggunakan AI.
    Menggabungkan data GRACE TWS + NDVI Sentinel-2 + status sumur.
    """
    conn = await get_db()
    try:
        # Ambil data terbaru GRACE
        grace_rows = await conn.fetch("""
            SELECT period_date,
                   ROUND(AVG(tws_anomaly)::numeric, 2) AS avg_tws
            FROM grace_tws
            GROUP BY period_date
            ORDER BY period_date DESC
            LIMIT 6
        """)

        # Ambil rata-rata NDVI per lokasi
        ndvi_rows = await conn.fetch("""
            SELECT location, kabupaten,
                   ROUND(AVG(ndvi)::numeric, 3) AS avg_ndvi,
                   CASE
                       WHEN AVG(ndvi) >= 0.5 THEN 'Vegetasi Lebat'
                       WHEN AVG(ndvi) >= 0.3 THEN 'Vegetasi Sedang'
                       WHEN AVG(ndvi) >= 0.1 THEN 'Vegetasi Jarang'
                       ELSE 'Lahan Kritis'
                   END AS kondisi
            FROM sentinel2_ndvi
            GROUP BY location, kabupaten
            ORDER BY avg_ndvi ASC
            LIMIT 5
        """)

        # Ambil ringkasan sumur
        well_rows = await conn.fetch("""
            SELECT kabupaten,
                   COUNT(*) FILTER (WHERE status_level='kritis' OR status_level='sangat_kritis') AS kritis,
                   COUNT(*) AS total
            FROM well_latest_status
            GROUP BY kabupaten
            ORDER BY kritis DESC
        """)

        # Susun konteks data untuk AI
        grace_summary = "\n".join([
            f"  {r['period_date'].strftime('%Y-%m')}: {r['avg_tws']:+.2f} cm EWH"
            for r in grace_rows
        ])

        ndvi_summary = "\n".join([
            f"  {r['location']} ({r['kabupaten']}): NDVI {r['avg_ndvi']} — {r['kondisi']}"
            for r in ndvi_rows
        ])

        well_summary = "\n".join([
            f"  {r['kabupaten']}: {r['kritis']} dari {r['total']} sumur kritis"
            for r in well_rows
        ])

        prompt = f"""Kamu adalah Senior Environmental Engineer dengan spesialisasi hidrologi dan monitoring lingkungan di Indonesia.

Berikut adalah data monitoring air tanah Nusa Tenggara Barat (NTB) terkini:

DATA NASA GRACE — Anomali Terrestrial Water Storage (6 bulan terakhir):
{grace_summary}
(Nilai negatif = defisit air tanah dibanding baseline 2004-2009)

DATA SENTINEL-2 NDVI — Kondisi Vegetasi (5 lokasi paling kritis):
{ndvi_summary}
(NDVI < 0.2 = vegetasi sangat jarang/lahan kritis)

STATUS SUMUR PANTAU:
{well_summary}

Berikan interpretasi komprehensif dalam Bahasa Indonesia (maksimal 200 kata) yang mencakup:
1. Kondisi air tanah NTB saat ini berdasarkan data GRACE
2. Hubungan antara kondisi vegetasi dan ketersediaan air tanah
3. Kabupaten/area yang paling memerlukan perhatian segera
4. Rekomendasi tindakan prioritas untuk Dinas ESDM NTB

Gunakan bahasa yang dapat dipahami oleh pejabat pemerintah daerah, bukan hanya ilmuwan.
Referensikan PP No. 43 Tahun 2008 tentang Pengelolaan Air Tanah jika relevan."""

        # Call Kimi API
        kimi = KimiClient(
            api_key=os.getenv('KIMI_API_KEY'),
            base_url="https://api.moonshot.ai/v1"
        )

        response = kimi.chat.completions.create(
            model="moonshot-v1-8k",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )

        interpretation = response.choices[0].message.content

        return {
            "generated_at": datetime.now().isoformat(),
            "data_sources": [
                "NASA GRACE RL06.3 Mascon",
                "Copernicus Sentinel-2 MSI",
                "Data Sumur Pantau NTB"
            ],
            "legal_reference": "PP No. 43 Tahun 2008",
            "ai_model": "moonshot-v1-8k",
            "interpretation": interpretation,
            "raw_data": {
                "grace_6months": [
                    {"period": r['period_date'].strftime('%Y-%m'),
                     "tws_cm": float(r['avg_tws'])}
                    for r in grace_rows
                ],
                "ndvi_critical": [
                    {"location": r['location'],
                     "ndvi": float(r['avg_ndvi']),
                     "kondisi": r['kondisi']}
                    for r in ndvi_rows
                ]
            }
        }
    finally:
        await conn.close()



# ============================================================
# ENDPOINT 9: Export Laporan PDF (reportlab)
# ============================================================
from fastapi.responses import Response
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import io

@app.get("/report/pdf")
async def generate_pdf_report():
    """Generate laporan PDF monitoring air tanah NTB."""
    conn = await get_db()
    try:
        # Ambil data
        grace_rows = await conn.fetch("""
            SELECT period_date, ROUND(AVG(tws_anomaly)::numeric, 2) AS avg_tws
            FROM grace_tws GROUP BY period_date
            ORDER BY period_date DESC LIMIT 6
        """)
        ndvi_rows = await conn.fetch("""
            SELECT location, kabupaten,
                   ROUND(AVG(ndvi)::numeric, 3) AS avg_ndvi,
                   CASE WHEN AVG(ndvi) >= 0.5 THEN 'Vegetasi Lebat'
                        WHEN AVG(ndvi) >= 0.3 THEN 'Vegetasi Sedang'
                        WHEN AVG(ndvi) >= 0.1 THEN 'Vegetasi Jarang'
                        ELSE 'Lahan Kritis' END AS kondisi
            FROM sentinel2_ndvi GROUP BY location, kabupaten ORDER BY avg_ndvi ASC
        """)
        kab_rows = await conn.fetch("""
            SELECT kabupaten, COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status_level='normal') AS normal,
                   COUNT(*) FILTER (WHERE status_level='waspada') AS waspada,
                   COUNT(*) FILTER (WHERE status_level IN ('kritis','sangat_kritis')) AS kritis
            FROM well_latest_status GROUP BY kabupaten ORDER BY kabupaten
        """)

        # AI interpretation
        kimi = KimiClient(api_key=os.getenv('KIMI_API_KEY'), base_url="https://api.moonshot.ai/v1")
        ai_resp = kimi.chat.completions.create(
            model="moonshot-v1-8k",
            messages=[{"role":"user","content":
                f"Buat ringkasan eksekutif kondisi air tanah NTB dalam 2 paragraf singkat. "
                f"Data: TWS terkini {float(grace_rows[0]['avg_tws'])} cm EWH, "
                f"{sum(r['kritis'] or 0 for r in kab_rows)} sumur kritis. "
                f"Bahasa formal untuk laporan pemerintah. Referensi PP 43/2008."}],
            temperature=0.3
        )
        ai_text = ai_resp.choices[0].message.content

        # Build PDF
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)

        BLUE  = HexColor('#0f4c81')
        GREEN = HexColor('#1D9E75')
        RED   = HexColor('#E24B4A')
        AMBER = HexColor('#BA7517')
        LGRAY = HexColor('#f0f4f8')

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('title', fontSize=16, textColor=white,
                                     fontName='Helvetica-Bold', alignment=TA_CENTER)
        sub_style   = ParagraphStyle('sub', fontSize=9, textColor=white,
                                     fontName='Helvetica', alignment=TA_CENTER)
        h2_style    = ParagraphStyle('h2', fontSize=12, textColor=BLUE,
                                     fontName='Helvetica-Bold', spaceAfter=6)
        body_style  = ParagraphStyle('body', fontSize=9, fontName='Helvetica',
                                     leading=14, spaceAfter=4)
        small_style = ParagraphStyle('small', fontSize=8, textColor=HexColor('#666666'),
                                     fontName='Helvetica', leading=12)

        story = []
        now_str = datetime.now().strftime('%d %B %Y %H:%M WIB')

        # Header block
        header_data = [[
            Paragraph('LAPORAN MONITORING AIR TANAH', title_style),
        ],[
            Paragraph('Nusa Tenggara Barat · NTB Groundwater Monitor', sub_style),
        ],[
            Paragraph(f'PP No. 43/2008 · NASA GRACE RL06.3 · Sentinel-2 MSI · {now_str}', sub_style),
        ]]
        header_tbl = Table(header_data, colWidths=[17*cm])
        header_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), BLUE),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('TOPPADDING', (0,0), (-1,-1), 8),
            ('BOTTOMPADDING', (0,0), (-1,-1), 8),
            ('ROWBACKGROUNDS', (0,0), (-1,-1), [BLUE]),
        ]))
        story.append(header_tbl)
        story.append(Spacer(1, 0.4*cm))

        # Stats row
        total_w   = sum(r['total'] for r in kab_rows)
        total_k   = sum(r['kritis'] or 0 for r in kab_rows)
        latest_tws = float(grace_rows[0]['avg_tws'])
        ndvi_k    = sum(1 for r in ndvi_rows if float(r['avg_ndvi']) < 0.1)

        stats_data = [
            [Paragraph(f'<b>{total_w}</b><br/>Total Sumur', body_style),
             Paragraph(f'<b><font color="#E24B4A">{total_k}</font></b><br/>Sumur Kritis', body_style),
             Paragraph(f'<b><font color="{"#1D9E75" if latest_tws > 0 else "#E24B4A"}">{latest_tws:+.2f} cm</font></b><br/>TWS GRACE Terkini', body_style),
             Paragraph(f'<b><font color="#E24B4A">{ndvi_k}</font></b><br/>Area NDVI Kritis', body_style)]
        ]
        stats_tbl = Table(stats_data, colWidths=[4.25*cm]*4)
        stats_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), LGRAY),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 10),
            ('BOTTOMPADDING', (0,0), (-1,-1), 10),
            ('GRID', (0,0), (-1,-1), 0.5, HexColor('#cccccc')),
            ('ROUNDEDCORNERS', [4]),
        ]))
        story.append(stats_tbl)
        story.append(Spacer(1, 0.4*cm))

        # AI Interpretation
        story.append(Paragraph('Interpretasi AI — Analisis Kondisi Terkini', h2_style))
        for para in ai_text.split('\n\n'):
            if para.strip():
                story.append(Paragraph(para.strip(), body_style))
        story.append(Spacer(1, 0.3*cm))

        # GRACE table
        story.append(Paragraph('Data NASA GRACE — Anomali Air Tanah 6 Bulan Terakhir', h2_style))
        grace_table_data = [['Periode', 'Anomali TWS (cm EWH)', 'Status']]
        for r in grace_rows:
            tws = float(r['avg_tws'])
            status = 'Surplus' if tws > 2 else 'Normal' if tws > 0 else 'Defisit'
            color = '#1D9E75' if tws > 0 else '#E24B4A'
            grace_table_data.append([
                r['period_date'].strftime('%B %Y'),
                Paragraph(f'<font color="{color}"><b>{tws:+.2f}</b></font>', body_style),
                status
            ])
        gt = Table(grace_table_data, colWidths=[6*cm, 6*cm, 5*cm])
        gt.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BLUE),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, LGRAY]),
            ('GRID', (0,0), (-1,-1), 0.3, HexColor('#dddddd')),
            ('ALIGN', (1,0), (1,-1), 'CENTER'),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        story.append(gt)
        story.append(Spacer(1, 0.3*cm))

        # Kabupaten table
        story.append(Paragraph('Status Sumur Pantau per Kabupaten', h2_style))
        kab_table_data = [['Kabupaten', 'Total', 'Normal', 'Waspada', 'Kritis', 'Risiko']]
        for r in kab_rows:
            k = r['kritis'] or 0
            risk = 'KRITIS' if k >= 2 else 'WASPADA' if k >= 1 else 'NORMAL'
            rc   = '#E24B4A' if k >= 2 else '#BA7517' if k >= 1 else '#1D9E75'
            kab_table_data.append([
                r['kabupaten'], r['total'], r['normal'] or 0,
                r['waspada'] or 0, k,
                Paragraph(f'<font color="{rc}"><b>{risk}</b></font>', body_style)
            ])
        kt = Table(kab_table_data, colWidths=[5.5*cm,2*cm,2.5*cm,2.5*cm,2*cm,2.5*cm])
        kt.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BLUE),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, LGRAY]),
            ('GRID', (0,0), (-1,-1), 0.3, HexColor('#dddddd')),
            ('ALIGN', (1,0), (-1,-1), 'CENTER'),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        story.append(kt)
        story.append(Spacer(1, 0.3*cm))

        # NDVI table
        story.append(Paragraph('Kondisi Vegetasi — Sentinel-2 NDVI', h2_style))
        ndvi_table_data = [['Lokasi', 'Kabupaten', 'Rata-rata NDVI', 'Kondisi']]
        for r in ndvi_rows:
            ndvi = float(r['avg_ndvi'])
            nc = '#1D9E75' if ndvi >= 0.5 else '#BA7517' if ndvi >= 0.2 else '#E24B4A'
            ndvi_table_data.append([
                r['location'], r['kabupaten'],
                Paragraph(f'<font color="{nc}"><b>{ndvi:.3f}</b></font>', body_style),
                r['kondisi']
            ])
        nt = Table(ndvi_table_data, colWidths=[4.5*cm,5*cm,3.5*cm,4*cm])
        nt.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), BLUE),
            ('TEXTCOLOR', (0,0), (-1,0), white),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
            ('FONTSIZE', (0,0), (-1,-1), 9),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [white, LGRAY]),
            ('GRID', (0,0), (-1,-1), 0.3, HexColor('#dddddd')),
            ('ALIGN', (2,0), (2,-1), 'CENTER'),
            ('TOPPADDING', (0,0), (-1,-1), 5),
            ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ]))
        story.append(nt)
        story.append(Spacer(1, 0.4*cm))

        # Legal footer
        legal = ('Dasar Hukum: PP No. 43 Tahun 2008 · Perpres No. 33 Tahun 2018 · '
                 'PerMenLHK P.68/2016 · SNI 6989.58:2008 | '
                 'Metodologi: NDVI = (B8-B4)/(B8+B4) Rouse et al. (1974) · '
                 'TWS = NASA GRACE RL06.3 Mascon Watkins et al. (2015) | '
                 'Disclaimer: Laporan ini dihasilkan otomatis. Keputusan kebijakan harus dikonfirmasi pengukuran lapangan.')
        story.append(Paragraph(legal, small_style))

        doc.build(story)
        pdf_bytes = buf.getvalue()

        filename = f"laporan-air-tanah-ntb-{datetime.now().strftime('%Y%m%d')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    finally:
        await conn.close()


# ============================================================
# ENDPOINT 10: Data Sumur Real ESDM NTB
# ============================================================
@app.get("/wells/esdm/geojson")
async def get_wells_esdm(
    kabupaten: Optional[str] = Query(None),
    fungsi: Optional[str] = Query(None)
):
    """
    280 sumur air tanah real dari ESDM NTB / Badan Geologi.
    Data terverifikasi dengan koordinat GPS lapangan.
    """
    conn = await get_db()
    try:
        query = """
            SELECT kode_sumur, fungsi, lat, lon,
                   dusun, desa, kecamatan, kabupaten,
                   dibangun_oleh, kedalaman_m, tahun_pembangunan,
                   ST_AsGeoJSON(geom)::json AS geometry
            FROM wells_esdm WHERE 1=1
        """
        params = []
        if kabupaten:
            params.append(f"%{kabupaten}%")
            query += f" AND LOWER(kabupaten) LIKE LOWER(${len(params)})"
        if fungsi:
            params.append(f"%{fungsi}%")
            query += f" AND LOWER(fungsi) LIKE LOWER(${len(params)})"

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
                "color": "#00d4ff"
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
    finally:
        await conn.close()
