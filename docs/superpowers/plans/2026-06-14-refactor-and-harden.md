# NTB Groundwater Monitor — Refactor & Harden Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor monolith `main.py` into modular routers, add connection pooling, fix security/code-quality issues, and add test coverage.

**Architecture:** Extract 8 endpoints from `main.py` into domain-specific routers under `app/routers/`. Introduce a shared `db.py` module with asyncpg connection pool. Add pytest-based test suite with httpx AsyncClient. Fix CORS, credentials, bare excepts, dead code.

**Tech Stack:** FastAPI, asyncpg (pool), pytest, pytest-asyncio, httpx

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/app/db.py` | Single asyncpg pool, `get_pool()`, `get_db()` dependency |
| `backend/app/utils.py` | `classify_ndvi()`, `ndvi_color()`, `format_period_label()` |
| `backend/app/queries.py` | `get_ndvi_period_range()`, `get_latest_ndvi_rows()` |
| `backend/app/routers/wells.py` | `/wells/geojson`, `/wells/{id}/timeseries`, `/wells/esdm/geojson` |
| `backend/app/routers/ndvi.py` | `/ndvi/summary`, `/ndvi/timeseries/{location}` |
| `backend/app/routers/ai.py` | `/ai/interpret` |
| `backend/app/routers/report.py` | `/report/pdf` |
| `backend/app/routers/health.py` | `/health` |
| `backend/app/routers/summary.py` | `/summary/kabupaten` |
| `backend/main.py` | App factory, CORS, include routers (slim ~50 lines) |
| `backend/tests/conftest.py` | Pytest fixtures: test DB, async client |
| `backend/tests/test_wells.py` | Tests for wells endpoints |
| `backend/tests/test_health.py` | Tests for health endpoint |
| `backend/tests/test_utils.py` | Tests for utility functions |

---

### Task 1: Create shared DB pool module

**Files:**
- Create: `backend/app/db.py`

- [ ] **Step 1: Create `backend/app/db.py`**

```python
import os
import asyncpg
from typing import Optional

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rizki:ntb_env_2024@db:5432/ntb_groundwater"
)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or _pool._closed:
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    return _pool


async def get_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


async def close_pool():
    global _pool
    if _pool and not _pool._closed:
        await _pool.close()
        _pool = None
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/db.py').read()); print('OK')"`
Expected: `OK`

---

### Task 2: Create shared utility module

**Files:**
- Create: `backend/app/utils.py`

- [ ] **Step 1: Create `backend/app/utils.py`**

```python
from datetime import date
from typing import Optional


def classify_ndvi(ndvi_value: float, title_case: bool = False) -> str:
    if ndvi_value >= 0.5:
        return "Vegetasi Lebat" if title_case else "lebat"
    if ndvi_value >= 0.3:
        return "Vegetasi Sedang" if title_case else "sedang"
    if ndvi_value >= 0.1:
        return "Vegetasi Jarang" if title_case else "jarang"
    return "Lahan Kritis" if title_case else "kritis"


def ndvi_color(kondisi: str) -> str:
    return {
        "lebat": "#1D9E75",
        "sedang": "#639922",
        "jarang": "#BA7517",
        "kritis": "#E24B4A"
    }.get(kondisi, "#888780")


def format_period_label(period_value: Optional[date]) -> Optional[str]:
    if not period_value:
        return None
    return period_value.strftime("%Y-%m")
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/utils.py').read()); print('OK')"`
Expected: `OK`

---

### Task 3: Create NDVI helper query module

**Files:**
- Create: `backend/app/queries.py`

- [ ] **Step 1: Create `backend/app/queries.py`**

```python
from typing import Optional
import asyncpg


async def get_ndvi_period_range(conn: asyncpg.Connection):
    return await conn.fetchrow("""
        SELECT
            MIN(period_date) AS min_period,
            MAX(period_date) AS max_period
        FROM sentinel2_ndvi
    """)


async def get_latest_ndvi_rows(
    conn: asyncpg.Connection,
    ascending: bool = False,
    limit: Optional[int] = None
):
    order = "ASC" if ascending else "DESC"
    limit_clause = f"LIMIT {int(limit)}" if limit else ""
    return await conn.fetch(f"""
        WITH ranked AS (
            SELECT
                location, kabupaten, lat, lon, period_date,
                ndvi, ndwi, vegetation_status,
                ROW_NUMBER() OVER (PARTITION BY location ORDER BY period_date DESC) AS rn,
                ROUND(MIN(ndvi) OVER (PARTITION BY location)::numeric, 3) AS min_ndvi,
                ROUND(MAX(ndvi) OVER (PARTITION BY location)::numeric, 3) AS max_ndvi,
                COUNT(*) OVER (PARTITION BY location) AS n_months
            FROM sentinel2_ndvi
        )
        SELECT
            location, kabupaten, lat, lon,
            ROUND(ndvi::numeric, 3) AS latest_ndvi,
            min_ndvi, max_ndvi, n_months,
            period_date AS latest_period,
            vegetation_status
        FROM ranked
        WHERE rn = 1
        ORDER BY latest_ndvi {order}, location
        {limit_clause}
    """)
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/queries.py').read()); print('OK')"`
Expected: `OK`

---

### Task 4: Extract wells router

**Files:**
- Create: `backend/app/routers/wells.py`

- [ ] **Step 1: Create `backend/app/routers/wells.py`**

Move endpoints `/wells/geojson`, `/wells/{well_id}/timeseries`, `/wells/esdm/geojson` from `main.py` into this file. Use `get_pool()` from `app.db` instead of per-request `get_db()`. Import helpers from `app.utils`.

```python
import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.db import get_pool
from app.utils import classify_ndvi, ndvi_color, format_period_label

router = APIRouter(tags=["wells"])


@router.get("/wells/geojson")
async def get_wells_geojson(
    kabupaten: Optional[str] = Query(None, description="Filter per kabupaten"),
    status: Optional[str] = Query(None, description="Filter: normal, waspada, kritis, sangat_kritis")
):
    """Semua sumur pantau NTB dalam format GeoJSON."""
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
            params.append(f"%{kabupaten}%")
            query += f" AND LOWER(wls.kabupaten) LIKE LOWER(${len(params)})"
        if status:
            params.append(status)
            query += f" AND wls.status_level = ${len(params)}"

        rows = await conn.fetch(query, *params)

        features = []
        for row in rows:
            geom = row["geometry"]
            if isinstance(geom, str):
                geom = json.loads(geom)

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
                    "color": {
                        "normal": "#1D9E75", "waspada": "#BA7517",
                        "kritis": "#E24B4A", "sangat_kritis": "#791F1F",
                        "tidak_ada_data": "#888780"
                    }.get(row["status_level"], "#888780"),
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
    """Data time series pengukuran untuk satu sumur."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        well = await conn.fetchrow("SELECT * FROM wells WHERE id = $1", well_id)
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

        levels = [s["water_level_m"] for s in series if s["water_level_m"]]
        stats = {}
        if levels:
            stats = {
                "min": round(min(levels), 3),
                "max": round(max(levels), 3),
                "mean": round(sum(levels) / len(levels), 3),
                "trend": "menurun" if len(levels) >= 2 and levels[-1] > levels[0] else "stabil_atau_naik"
            }

        return {
            "well": {
                "id": well["id"], "well_code": well["well_code"],
                "name": well["name"], "kabupaten": well["kabupaten"],
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
    """280 sumur air tanah real dari ESDM NTB / Badan Geologi."""
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
                "kode_sumur": row["kode_sumur"], "fungsi": row["fungsi"],
                "kecamatan": row["kecamatan"], "kabupaten": row["kabupaten"],
                "desa": row["desa"], "dibangun_oleh": row["dibangun_oleh"],
                "kedalaman_m": float(row["kedalaman_m"]) if row["kedalaman_m"] else None,
                "tahun": int(row["tahun_pembangunan"]) if row["tahun_pembangunan"] else None,
                "color": "#00d4ff",
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/routers/wells.py').read()); print('OK')"`
Expected: `OK`

---

### Task 5: Extract NDVI router

**Files:**
- Create: `backend/app/routers/ndvi.py`

- [ ] **Step 1: Create `backend/app/routers/ndvi.py`**

Move `/ndvi/summary` and `/ndvi/timeseries/{location}` from `main.py`.

```python
from fastapi import APIRouter, HTTPException

from app.db import get_pool
from app.utils import classify_ndvi, ndvi_color, format_period_label
from app.queries import get_latest_ndvi_rows, get_ndvi_period_range

router = APIRouter(tags=["ndvi"])


@router.get("/ndvi/summary")
async def get_ndvi_summary():
    """Ringkasan kondisi vegetasi NTB dari Sentinel-2."""
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
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/routers/ndvi.py').read()); print('OK')"`
Expected: `OK`

---

### Task 6: Extract summary router

**Files:**
- Create: `backend/app/routers/summary.py`

- [ ] **Step 1: Create `backend/app/routers/summary.py`**

Move `/summary/kabupaten` from `main.py`.

```python
from datetime import datetime

from fastapi import APIRouter

from app.db import get_pool

router = APIRouter(tags=["summary"])


@router.get("/summary/kabupaten")
async def get_summary_by_kabupaten():
    """Ringkasan kondisi air tanah per kabupaten untuk kartu dashboard."""
    pool = await get_pool()
    async with pool.acquire() as conn:
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
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/routers/summary.py').read()); print('OK')"`
Expected: `OK`

---

### Task 7: Extract health router

**Files:**
- Create: `backend/app/routers/health.py`

- [ ] **Step 1: Create `backend/app/routers/health.py`**

Move `/health` from `main.py`.

```python
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.db import get_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Health check endpoint."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"status": "ok", "database": "connected", "timestamp": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database error: {str(e)}")
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/routers/health.py').read()); print('OK')"`
Expected: `OK`

---

### Task 8: Extract AI router

**Files:**
- Create: `backend/app/routers/ai.py`

- [ ] **Step 1: Create `backend/app/routers/ai.py`**

Move `/ai/interpret` from `main.py`. Add `KIMI_API_KEY` validation at start.

```python
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from openai import OpenAI as KimiClient

from app.db import get_pool
from app.utils import classify_ndvi, format_period_label
from app.queries import get_latest_ndvi_rows

router = APIRouter(tags=["ai"])


@router.get("/ai/interpret")
async def ai_interpret_ntb():
    """Interpretasi otomatis kondisi air tanah NTB menggunakan AI."""
    kimi_key = os.getenv("KIMI_API_KEY")
    if not kimi_key:
        raise HTTPException(status_code=503, detail="KIMI_API_KEY not configured")

    pool = await get_pool()
    async with pool.acquire() as conn:
        gws_rows = await conn.fetch("""
            SELECT t.period_date,
                   ROUND(AVG(t.tws_anomaly - COALESCE(s.sms_anomaly, 0))::numeric, 2) AS avg_gws
            FROM grace_tws t
            LEFT JOIN gldas_sms s ON
                EXTRACT(YEAR FROM t.period_date) = s.year AND
                EXTRACT(MONTH FROM t.period_date) = s.month AND
                ABS(t.lat - s.lat) < 0.01 AND
                ABS(t.lon - s.lon) < 0.01
            GROUP BY t.period_date
            ORDER BY t.period_date DESC
            LIMIT 6
        """)

        rain_rows = await conn.fetch("""
            SELECT year, month, ROUND(AVG(precip_mm)::numeric, 2) as avg_precip
            FROM chirps_precip
            GROUP BY year, month
            ORDER BY year DESC, month DESC
            LIMIT 6
        """)

        ndvi_rows = await get_latest_ndvi_rows(conn, ascending=True, limit=5)

        well_rows = await conn.fetch("""
            SELECT kabupaten,
                   COUNT(*) FILTER (WHERE status_level='kritis' OR status_level='sangat_kritis') AS kritis,
                   COUNT(*) AS total
            FROM well_latest_status
            GROUP BY kabupaten
            ORDER BY kritis DESC
        """)

        gws_summary = "\n".join([
            f"  {r['period_date'].strftime('%Y-%m')}: {r['avg_gws']:+.2f} cm EWH"
            for r in gws_rows
        ])

        rain_summary = "\n".join([
            f"  {r['year']}-{r['month']:02d}: {float(r['avg_precip']):.1f} mm"
            for r in rain_rows
        ])

        ndvi_summary = "\n".join([
            f"  {r['location']} ({r['kabupaten']}): NDVI terbaru {float(r['latest_ndvi']):.3f} pada {format_period_label(r['latest_period'])} - {classify_ndvi(float(r['latest_ndvi']), title_case=True)}"
            for r in ndvi_rows
        ])

        well_summary = "\n".join([
            f"  {r['kabupaten']}: {r['kritis']} dari {r['total']} sumur kritis"
            for r in well_rows
        ])

        prompt = f"""Kamu adalah Senior Environmental Engineer dengan spesialisasi hidrologi dan monitoring lingkungan di Indonesia.

Berikut adalah data monitoring air tanah Nusa Tenggara Barat (NTB) terkini:

DATA NASA GRACE + GLDAS - Anomali Groundwater Storage (GWS) regional (6 bulan terakhir):
{gws_summary}
(GWS = TWS - Soil Moisture. Nilai negatif = defisit simpanan air tanah regional.)

DATA CURAH HUJAN CHIRPS (6 bulan terakhir):
{rain_summary}
(Gunakan untuk melihat apakah defisit air tanah sejalan dengan kurangnya hujan atau indikasi pemompaan berlebih.)

DATA SENTINEL-2 NDVI - Snapshot vegetasi terbaru (5 lokasi paling kritis):
{ndvi_summary}

STATUS SUMUR PANTAU:
{well_summary}

Berikan interpretasi komprehensif dalam Bahasa Indonesia (maksimal 200 kata) yang mencakup:
1. Kondisi simpanan air tanah (GWS) regional NTB saat ini sehubungan dengan curah hujan
2. Hubungan indikatif antara kondisi vegetasi dan potensi tekanan sumber daya air tanah
3. Kabupaten/area yang paling memerlukan perhatian segera
4. Analisis potensi pengaruh antropogenik (jika hujan normal tapi GWS turun tajam)
5. Rekomendasi tindakan prioritas untuk Dinas ESDM NTB.

Referensikan PP No. 43 Tahun 2008."""

        kimi = KimiClient(api_key=kimi_key, base_url="https://api.moonshot.ai/v1")
        response = kimi.chat.completions.create(
            model="moonshot-v1-8k",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )
        interpretation = response.choices[0].message.content

        return {
            "generated_at": datetime.now().isoformat(),
            "data_sources": [
                "NASA GRACE RL06.3 Mascon", "NASA GLDAS Noah 2.1",
                "UCSB-CHG CHIRPS", "Copernicus Sentinel-2 MSI",
                "Data Sumur Pantau NTB"
            ],
            "legal_reference": "PP No. 43 Tahun 2008",
            "ai_model": "moonshot-v1-8k",
            "interpretation": interpretation,
            "raw_data": {
                "gws_6months": [
                    {"period": r['period_date'].strftime('%Y-%m'), "gws_cm": float(r['avg_gws'])}
                    for r in gws_rows
                ],
                "ndvi_critical": [
                    {"location": r['location'], "ndvi": float(r['latest_ndvi']),
                     "kondisi": classify_ndvi(float(r['latest_ndvi']), title_case=True),
                     "latest_period": format_period_label(r['latest_period'])}
                    for r in ndvi_rows
                ]
            }
        }
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/routers/ai.py').read()); print('OK')"`
Expected: `OK`

---

### Task 9: Extract report router

**Files:**
- Create: `backend/app/routers/report.py`

- [ ] **Step 1: Create `backend/app/routers/report.py`**

Move `/report/pdf` from `main.py`. Add `KIMI_API_KEY` validation.

The full code is in `main.py` lines 714-935. Key changes:
- Import `get_pool` from `app.db` instead of local `get_db()`
- Import `classify_ndvi` from `app.utils`
- Import `get_latest_ndvi_rows` from `app.queries`
- Add `kimi_key = os.getenv("KIMI_API_KEY")` check at start
- Replace `conn = await get_db()` / `finally: await conn.close()` with `pool = await get_pool()` / `async with pool.acquire() as conn:`

```python
import io
import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from openai import OpenAI as KimiClient
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor, white
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER

from app.db import get_pool
from app.utils import classify_ndvi
from app.queries import get_latest_ndvi_rows

router = APIRouter(tags=["report"])


@router.get("/report/pdf")
async def generate_pdf_report():
    """Generate laporan PDF monitoring air tanah NTB."""
    kimi_key = os.getenv("KIMI_API_KEY")
    if not kimi_key:
        raise HTTPException(status_code=503, detail="KIMI_API_KEY not configured")

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Same DB queries as current main.py lines 720-741
        gws_rows = await conn.fetch("""
            SELECT t.period_date,
                   ROUND(AVG(t.tws_anomaly)::numeric, 2) AS avg_tws,
                   ROUND(AVG(t.tws_anomaly - COALESCE(s.sms_anomaly, 0))::numeric, 2) AS avg_gws
            FROM grace_tws t
            LEFT JOIN gldas_sms s ON
                EXTRACT(YEAR FROM t.period_date) = s.year AND
                EXTRACT(MONTH FROM t.period_date) = s.month AND
                ABS(t.lat - s.lat) < 0.01 AND
                ABS(t.lon - s.lon) < 0.01
            GROUP BY t.period_date
            ORDER BY t.period_date DESC
            LIMIT 6
        """)
        ndvi_rows = await get_latest_ndvi_rows(conn, ascending=True)
        kab_rows = await conn.fetch("""
            SELECT kabupaten, COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE status_level='normal') AS normal,
                   COUNT(*) FILTER (WHERE status_level='waspada') AS waspada,
                   COUNT(*) FILTER (WHERE status_level IN ('kritis','sangat_kritis')) AS kritis
            FROM well_latest_status GROUP BY kabupaten ORDER BY kabupaten
        """)

        # AI call + PDF build — same logic as main.py lines 744-935
        kimi = KimiClient(api_key=kimi_key, base_url="https://api.moonshot.ai/v1")
        ai_resp = kimi.chat.completions.create(
            model="moonshot-v1-8k",
            messages=[{"role": "user", "content":
                f"Buat ringkasan eksekutif kondisi sumber daya air NTB dalam 2 paragraf singkat. "
                f"Data: Anomali simpanan air tanah (GWS) regional terkini {float(gws_rows[0]['avg_gws'])} cm EWH, "
                f"Anomali TWS (Total Water Storage) {float(gws_rows[0]['avg_tws'])} cm EWH. "
                f"{sum(r['kritis'] or 0 for r in kab_rows)} sumur kritis. "
                f"GWS dihitung dengan formula Rodell et al. (2009): GWS = TWS - Soil Moisture. "
                f"Tegaskan bahwa GWS satelit adalah indikator regional, bukan pembacaan langsung muka air sumur. "
                f"Bahasa formal untuk laporan pemerintah. Referensi PP 43/2008."}],
            temperature=0.3
        )
        ai_text = ai_resp.choices[0].message.content

        # Build PDF — replicate exact same ReportLab code from main.py lines 760-933
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=2*cm, rightMargin=2*cm,
                                topMargin=2*cm, bottomMargin=2*cm)

        BLUE = HexColor('#0f4c81')
        LGRAY = HexColor('#f0f4f8')

        styles = getSampleStyleSheet()
        title_style = ParagraphStyle('title', fontSize=16, textColor=white,
                                     fontName='Helvetica-Bold', alignment=TA_CENTER)
        sub_style = ParagraphStyle('sub', fontSize=9, textColor=white,
                                   fontName='Helvetica', alignment=TA_CENTER)
        h2_style = ParagraphStyle('h2', fontSize=12, textColor=BLUE,
                                  fontName='Helvetica-Bold', spaceAfter=6)
        body_style = ParagraphStyle('body', fontSize=9, fontName='Helvetica',
                                    leading=14, spaceAfter=4)
        small_style = ParagraphStyle('small', fontSize=8, textColor=HexColor('#666666'),
                                     fontName='Helvetica', leading=12)

        story = []
        now_str = datetime.now().strftime('%d %B %Y %H:%M WIB')

        # Header, stats, AI interpretation, GWS table, kabupaten table, NDVI table, footer
        # — copy exact code from main.py lines 787-923

        header_data = [[Paragraph('LAPORAN MONITORING AIR TANAH', title_style)],
                       [Paragraph('Nusa Tenggara Barat · NTB Groundwater Monitor', sub_style)],
                       [Paragraph(f'PP No. 43/2008 · NASA GRACE/GLDAS · Sentinel-2 MSI · {now_str}', sub_style)]]
        header_tbl = Table(header_data, colWidths=[17*cm])
        header_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), BLUE),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(header_tbl)
        story.append(Spacer(1, 0.4*cm))

        total_w = sum(r['total'] for r in kab_rows)
        total_k = sum(r['kritis'] or 0 for r in kab_rows)
        latest_gws = float(gws_rows[0]['avg_gws'])
        ndvi_k = sum(1 for r in ndvi_rows if float(r['latest_ndvi']) < 0.1)

        stats_data = [
            [Paragraph(f'<b>{total_w}</b><br/>Total Sumur', body_style),
             Paragraph(f'<b><font color="#E24B4A">{total_k}</font></b><br/>Sumur Kritis', body_style),
             Paragraph(f'<b><font color="{"#1D9E75" if latest_gws > 0 else "#E24B4A"}">{latest_gws:+.2f} cm</font></b><br/>GWS (Air Tanah)', body_style),
             Paragraph(f'<b><font color="#E24B4A">{ndvi_k}</font></b><br/>Area NDVI Kritis', body_style)]
        ]
        stats_tbl = Table(stats_data, colWidths=[4.25*cm]*4)
        stats_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), LGRAY),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cccccc')),
        ]))
        story.append(stats_tbl)
        story.append(Spacer(1, 0.4*cm))

        story.append(Paragraph('Interpretasi AI — Analisis Kondisi Terkini', h2_style))
        for para in ai_text.split('\n\n'):
            if para.strip():
                story.append(Paragraph(para.strip(), body_style))
        story.append(Spacer(1, 0.3*cm))

        story.append(Paragraph('Anomali Groundwater Storage (GWS) - 6 Bulan Terakhir', h2_style))
        story.append(Paragraph('Formula: GWS = TWS (GRACE) - SMS (Soil Moisture GLDAS). Baseline: 2004-2009 mean.', small_style))
        gws_table_data = [['Periode', 'Anomali GWS (cm EWH)', 'Status']]
        for r in gws_rows:
            gws = float(r['avg_gws'])
            status = 'Surplus' if gws > 2 else 'Normal' if gws > 0 else 'Defisit'
            color = '#1D9E75' if gws > 0 else '#E24B4A'
            gws_table_data.append([
                r['period_date'].strftime('%B %Y'),
                Paragraph(f'<font color="{color}"><b>{gws:+.2f}</b></font>', body_style),
                status
            ])
        gt = Table(gws_table_data, colWidths=[6*cm, 6*cm, 5*cm])
        gt.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BLUE), ('TEXTCOLOR', (0, 0), (-1, 0), white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, LGRAY]),
            ('GRID', (0, 0), (-1, -1), 0.3, HexColor('#dddddd')),
            ('ALIGN', (1, 0), (1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(gt)
        story.append(Spacer(1, 0.3*cm))

        story.append(Paragraph('Status Sumur Pantau per Kabupaten', h2_style))
        kab_table_data = [['Kabupaten', 'Total', 'Normal', 'Waspada', 'Kritis', 'Risiko']]
        for r in kab_rows:
            k = r['kritis'] or 0
            risk = 'KRITIS' if k >= 2 else 'WASPADA' if k >= 1 else 'NORMAL'
            rc = '#E24B4A' if k >= 2 else '#BA7517' if k >= 1 else '#1D9E75'
            kab_table_data.append([
                r['kabupaten'], r['total'], r['normal'] or 0,
                r['waspada'] or 0, k,
                Paragraph(f'<font color="{rc}"><b>{risk}</b></font>', body_style)
            ])
        kt = Table(kab_table_data, colWidths=[5.5*cm, 2*cm, 2.5*cm, 2.5*cm, 2*cm, 2.5*cm])
        kt.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BLUE), ('TEXTCOLOR', (0, 0), (-1, 0), white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, LGRAY]),
            ('GRID', (0, 0), (-1, -1), 0.3, HexColor('#dddddd')),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(kt)
        story.append(Spacer(1, 0.3*cm))

        story.append(Paragraph('Kondisi Vegetasi - Snapshot NDVI Sentinel-2 Terbaru', h2_style))
        ndvi_table_data = [['Lokasi', 'Kabupaten', 'NDVI Terbaru', 'Kondisi']]
        for r in ndvi_rows:
            ndvi = float(r['latest_ndvi'])
            kondisi = classify_ndvi(ndvi, title_case=True)
            nc = '#1D9E75' if ndvi >= 0.5 else '#BA7517' if ndvi >= 0.2 else '#E24B4A'
            ndvi_table_data.append([
                r['location'], r['kabupaten'],
                Paragraph(f'<font color="{nc}"><b>{ndvi:.3f}</b></font>', body_style),
                kondisi
            ])
        nt = Table(ndvi_table_data, colWidths=[4.5*cm, 5*cm, 3.5*cm, 4*cm])
        nt.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), BLUE), ('TEXTCOLOR', (0, 0), (-1, 0), white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'), ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, LGRAY]),
            ('GRID', (0, 0), (-1, -1), 0.3, HexColor('#dddddd')),
            ('ALIGN', (2, 0), (2, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        story.append(nt)
        story.append(Spacer(1, 0.4*cm))

        legal = ('Dasar Hukum: PP No. 43 Tahun 2008 · Perpres No. 33 Tahun 2018 | '
                 'Metodologi: GWS = TWS (GRACE) - SMS (GLDAS Noah 2.1). Rodell et al. (2009) doi:10.1038/nature08232 | '
                 'Disclaimer: Laporan ini berupa estimasi regional (resolusi ~55 km). Keputusan kebijakan harus dikonfirmasi pengukuran lapangan.')
        story.append(Paragraph(legal, small_style))

        doc.build(story)
        pdf_bytes = buf.getvalue()

        filename = f"laporan-air-tanah-ntb-{datetime.now().strftime('%Y%m%d')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/app/routers/report.py').read()); print('OK')"`
Expected: `OK`

---

### Task 10: Update existing routers to use pool

**Files:**
- Modify: `backend/app/routers/groundwater.py`
- Modify: `backend/app/routers/grace.py`
- Modify: `backend/app/routers/climate.py`

- [ ] **Step 1: Update `groundwater.py`**

Replace entire file. Remove local `DATABASE_URL` and `get_db()`. Import `get_pool` from `app.db`.

```python
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
```

- [ ] **Step 2: Update `grace.py`**

Replace entire file. Same pattern — remove local `get_db()`, use `get_pool`.

```python
from fastapi import APIRouter, Query

from app.db import get_pool

router = APIRouter(prefix="/grace", tags=["grace"])


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
```

- [ ] **Step 3: Update `climate.py`**

Read `backend/app/routers/climate.py` first, then apply same pattern: remove local `DATABASE_URL`/`get_db()`, import `get_pool`.

- [ ] **Step 4: Verify all three routers syntax**

Run:
```bash
python -c "import ast; ast.parse(open('backend/app/routers/groundwater.py').read()); print('groundwater OK')"
python -c "import ast; ast.parse(open('backend/app/routers/grace.py').read()); print('grace OK')"
python -c "import ast; ast.parse(open('backend/app/routers/climate.py').read()); print('climate OK')"
```

---

### Task 11: Slim down main.py

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Replace `backend/main.py` with slim version**

The new `main.py` should be ~50 lines: app factory, CORS config, include routers, root endpoint, startup/shutdown events.

```python
"""
NTB Groundwater Monitoring API
Landasan hukum: PP No. 43 Tahun 2008 tentang Air Tanah
Referensi ilmiah: NASA GRACE RL06 Mascon Solutions
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from app.db import get_pool, close_pool
from app.routers import groundwater, grace, climate
from app.routers.wells import router as wells_router
from app.routers.ndvi import router as ndvi_router
from app.routers.summary import router as summary_router
from app.routers.health import router as health_router
from app.routers.ai import router as ai_router
from app.routers.report import router as report_router

ALLOWED_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3003,http://13.236.148.26:3003,https://gw.rizkiagustiawan.tech"
).split(",")

app = FastAPI(
    title="NTB Groundwater Monitoring API",
    description="Platform monitoring air tanah Nusa Tenggara Barat berbasis satelit NASA GRACE dan data lapangan. Referensi: PP 43/2008, Perpres 33/2018.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(groundwater.router)
app.include_router(grace.router)
app.include_router(climate.router)
app.include_router(wells_router)
app.include_router(ndvi_router)
app.include_router(summary_router)
app.include_router(health_router)
app.include_router(ai_router)
app.include_router(report_router)


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


@app.on_event("startup")
async def startup():
    await get_pool()


@app.on_event("shutdown")
async def shutdown():
    await close_pool()
```

- [ ] **Step 2: Verify syntax**

Run: `python -c "import ast; ast.parse(open('backend/main.py').read()); print('OK')"`
Expected: `OK`

---

### Task 12: Add unit tests for utilities

**Files:**
- Create: `backend/tests/__init__.py`
- Create: `backend/tests/test_utils.py`

- [ ] **Step 1: Create `backend/tests/__init__.py`**

```python
```

- [ ] **Step 2: Create `backend/tests/test_utils.py`**

```python
from datetime import date

from app.utils import classify_ndvi, ndvi_color, format_period_label


class TestClassifyNdvi:
    def test_lebat(self):
        assert classify_ndvi(0.6) == "lebat"

    def test_sedang(self):
        assert classify_ndvi(0.4) == "sedang"

    def test_jarang(self):
        assert classify_ndvi(0.2) == "jarang"

    def test_kritis(self):
        assert classify_ndvi(0.05) == "kritis"

    def test_boundary_05(self):
        assert classify_ndvi(0.5) == "lebat"

    def test_boundary_03(self):
        assert classify_ndvi(0.3) == "sedang"

    def test_boundary_01(self):
        assert classify_ndvi(0.1) == "jarang"

    def test_title_case(self):
        assert classify_ndvi(0.6, title_case=True) == "Vegetasi Lebat"
        assert classify_ndvi(0.05, title_case=True) == "Lahan Kritis"


class TestNdviColor:
    def test_lebat_color(self):
        assert ndvi_color("lebat") == "#1D9E75"

    def test_unknown_color(self):
        assert ndvi_color("unknown") == "#888780"


class TestFormatPeriodLabel:
    def test_valid_date(self):
        assert format_period_label(date(2024, 6, 1)) == "2024-06"

    def test_none(self):
        assert format_period_label(None) is None
```

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/test_utils.py -v`
Expected: All PASS

---

### Task 13: Add tests for health endpoint

**Files:**
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health.py`

- [ ] **Step 1: Create `backend/tests/conftest.py`**

```python
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from main import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

- [ ] **Step 2: Create `backend/tests/test_health.py`**

```python
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint(client: AsyncClient):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "timestamp" in data


@pytest.mark.asyncio
async def test_root_endpoint(client: AsyncClient):
    resp = await client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["platform"] == "NTB Groundwater Monitoring"
    assert "legal_basis" in data
```

- [ ] **Step 3: Run tests**

Run: `cd backend && python -m pytest tests/test_health.py -v`
Expected: PASS (requires DB running via docker-compose)

---

### Task 14: Add test dependencies and clean up

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Update `requirements.txt`**

Remove unused deps (`httpx` from API deps — move to test-only). Add test deps.

New content:
```
fastapi==0.111.0
uvicorn[standard]==0.30.0
asyncpg==0.29.0
python-dotenv==1.0.1
openai==2.29.0
reportlab==4.2.0
numpy==2.1.1
```

Create `backend/requirements-test.txt`:
```
-r requirements.txt
pytest==8.3.4
pytest-asyncio==0.25.0
httpx==0.27.0
```

- [ ] **Step 2: Remove dead files**

```bash
git rm backend/templates/laporan.html 2>/dev/null || rm -f backend/templates/laporan.html
rm -f frontend/index.html.bak frontend/index.html.bak2
rm -f frontend/nginx.conf  # stale, proxy.conf is active
```

- [ ] **Step 3: Remove duplicate `import os` in any remaining file**

Check `main.py` — the new slim version has only one `import os`.

---

### Task 15: Verify full refactor

- [ ] **Step 1: Syntax check all Python files**

Run:
```bash
find backend -name "*.py" -not -path "*__pycache__*" -exec python -c "import ast; ast.parse(open('{}').read()); print('{} OK')" \;
```

Expected: All files print `OK`

- [ ] **Step 2: Run all tests**

Run: `cd backend && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Verify endpoint count**

Run: `cd backend && python -c "from main import app; print(len([r for r in app.routes if hasattr(r, 'methods')]))"`
Expected: 14 endpoints (same as before refactor)

- [ ] **Step 4: Docker build test**

Run: `docker compose build api`
Expected: Build succeeds

---
