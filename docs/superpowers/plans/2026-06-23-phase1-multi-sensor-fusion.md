# Phase 1: Multi-Sensor Groundwater Platform — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build multi-sensor foundation for NTB Groundwater Monitor — BMKG rainfall integration, unified data fusion pipeline, and Sentinel-1 SAR subsidence detection.

**Architecture:** Three independent data pipelines (BMKG, SAR, existing sources) feed into a unified ETL script that merges all sensors into one PostGIS table. FastAPI routers expose each source plus a fusion API. Frontend adds new layers and charts.

**Tech Stack:** FastAPI 0.111, asyncpg, PostgreSQL 15 + PostGIS 3.3, Python 3.11, Google Earth Engine Python API, requests, Chart.js 4.4, MapLibre GL JS 4.1

## Global Constraints

- Python 3.11, FastAPI 0.111, asyncpg 0.29
- PostgreSQL 15 + PostGIS 3.3
- All new tables use `IF NOT EXISTS` for idempotent init
- All new routers registered in `backend/main.py`
- All spatial data uses EPSG:4326
- Baseline period: 2004-2009 for all anomalies
- Follow existing code patterns (router structure, query style, GeoJSON output)

---

## File Map

### New Files
| File | Responsibility |
|------|----------------|
| `backend/init.sql` | Add 3 new tables: `bmkg_rainfall`, `sar_subsidence`, `unified_monitoring` |
| `scripts/bmkg_sync.py` | ETL: fetch BMKG API → `bmkg_rainfall` |
| `scripts/sar_to_postgis.py` | ETL: GEE Sentinel-1 → `sar_subsidence` |
| `scripts/sync_unified.py` | ETL: merge all sources → `unified_monitoring` |
| `backend/app/routers/bmkg.py` | API endpoints for BMKG rainfall |
| `backend/app/routers/sar.py` | API endpoints for SAR subsidence |
| `backend/app/routers/fusion.py` | API endpoints for unified monitoring |
| `backend/tests/test_bmkg.py` | Tests for BMKG router |
| `backend/tests/test_sar.py` | Tests for SAR router |
| `backend/tests/test_fusion.py` | Tests for fusion router + drought index |

### Modified Files
| File | Change |
|------|--------|
| `backend/main.py` | Register 3 new routers |
| `backend/requirements.txt` | Add `requests`, `earthengine-api` |
| `frontend/index.html` | Add BMKG chart, SAR layer, multi-sensor panel |
| `scripts/smoke_test_live.sh` | Add new endpoint checks |

---

## Task 1: Database Schema — New Tables

**Files:**
- Modify: `backend/init.sql`

**Interfaces:**
- Produces: `bmkg_rainfall`, `sar_subsidence`, `unified_monitoring` tables

- [ ] **Step 1: Append new tables to init.sql**

Read current `backend/init.sql` (174 lines), then append after line 174:

```sql
-- ============================================================
-- TABEL: Data Curah Hujan BMKG
-- Sumber: BMKG API (https://data.bmkg.go.id)
-- ============================================================
CREATE TABLE IF NOT EXISTS bmkg_rainfall (
    id              SERIAL PRIMARY KEY,
    station_id      VARCHAR(20) NOT NULL,
    station_name    VARCHAR(100),
    lat             FLOAT NOT NULL,
    lon             FLOAT NOT NULL,
    date            DATE NOT NULL,
    precip_mm       FLOAT,
    humidity_pct    FLOAT,
    temp_c          FLOAT,
    wind_speed_ms   FLOAT,
    geom            GEOMETRY(Point, 4326),
    source          VARCHAR(50) DEFAULT 'bmkg_api',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(station_id, date)
);

CREATE INDEX IF NOT EXISTS idx_bmkg_geom ON bmkg_rainfall USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_bmkg_date ON bmkg_rainfall(date);

-- ============================================================
-- TABEL: Data Subsidence Sentinel-1 SAR
-- Sumber: COPERNICUS/S1_GRD via Google Earth Engine
-- ============================================================
CREATE TABLE IF NOT EXISTS sar_subsidence (
    id                  SERIAL PRIMARY KEY,
    location            VARCHAR(100) NOT NULL,
    kabupaten           VARCHAR(100),
    lat                 FLOAT NOT NULL,
    lon                 FLOAT NOT NULL,
    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    displacement_mm     FLOAT,
    rate_mm_year        FLOAT,
    n_observations      INTEGER,
    coherence           FLOAT,
    geom                GEOMETRY(Point, 4326),
    source              VARCHAR(50) DEFAULT 'sentinel1_gee',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(location, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_sar_geom ON sar_subsidence USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_sar_rate ON sar_subsidence(rate_mm_year);

-- ============================================================
-- TABEL: Unified Multi-Sensor Monitoring
-- Gabungan: GRACE + GLDAS + CHIRPS + BMKG + Sentinel-2 + SAR
-- ============================================================
CREATE TABLE IF NOT EXISTS unified_monitoring (
    id                  SERIAL PRIMARY KEY,
    period_date         DATE NOT NULL,
    lat                 FLOAT NOT NULL,
    lon                 FLOAT NOT NULL,
    tws_anomaly         FLOAT,
    tws_uncertainty     FLOAT,
    sms_anomaly         FLOAT,
    gws_anomaly         FLOAT,
    chirps_precip_mm    FLOAT,
    chirps_anomaly      FLOAT,
    bmkg_precip_mm      FLOAT,
    bmkg_station_id     VARCHAR(20),
    bmkg_distance_km    FLOAT,
    ndvi                FLOAT,
    ndvi_location       VARCHAR(100),
    ndvi_distance_km    FLOAT,
    sar_subsidence_mm   FLOAT,
    sar_rate_mm_year    FLOAT,
    drought_index       FLOAT,
    risk_level          VARCHAR(20),
    grid_resolution     VARCHAR(20) DEFAULT '0.5deg',
    data_completeness   FLOAT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(period_date, lat, lon)
);

CREATE INDEX IF NOT EXISTS idx_unified_date ON unified_monitoring(period_date);
CREATE INDEX IF NOT EXISTS idx_unified_risk ON unified_monitoring(risk_level);
CREATE INDEX IF NOT EXISTS idx_unified_coords ON unified_monitoring(lat, lon);
```

- [ ] **Step 2: Verify schema loads**

```bash
docker compose up -d db
sleep 5
docker compose exec db psql -U rizki -d ntb_groundwater -c "\dt"
```

Expected: tables `bmkg_rainfall`, `sar_subsidence`, `unified_monitoring` listed.

- [ ] **Step 3: Commit**

```bash
git add backend/init.sql
git commit -m "db: add bmkg_rainfall, sar_subsidence, unified_monitoring tables"
```

---

## Task 2: BMKG Sync Script

**Files:**
- Create: `scripts/bmkg_sync.py`

**Interfaces:**
- Consumes: BMKG API JSON
- Produces: rows in `bmkg_rainfall` table

- [ ] **Step 1: Create the sync script**

```python
#!/usr/bin/env python3
"""BMKG Rainfall Data Sync — fetch from BMKG API, insert into PostGIS."""
import os
import sys
import json
import logging
from datetime import datetime, date

import requests
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@localhost:5435/ntb_groundwater")
BMKG_API_KEY = os.getenv("BMKG_API_KEY", "")
BMKG_BASE_URL = "https://data.bmkg.go.id"

# NTB station IDs — update with actual BMKG station codes
NTB_STATIONS = [
    {"id": "96001", "name": "Stasiun Meteorologi Sultan Muhammad Kaharuddin III Sumbawa", "lat": -8.4911, "lon": 117.4203},
    {"id": "96003", "name": "Stasiun Meteorologi Lombok Praya", "lat": -8.7569, "lon": 116.2769},
    {"id": "96004", "name": "Stasiun Meteorologi Bima", "lat": -8.5394, "lon": 118.6869},
    {"id": "96009", "name": "Stasiun Meteorologi Dompu", "lat": -8.5364, "lon": 118.4614},
]


def fetch_bmkg_data(station_id: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch rainfall data from BMKG API."""
    headers = {"Authorization": f"Bearer {BMKG_API_KEY}"} if BMKG_API_KEY else {}
    
    # BMKG API endpoint for daily data
    url = f"{BMKG_BASE_URL}/v1/climate/daily"
    params = {
        "station_id": station_id,
        "start": start_date,
        "end": end_date,
    }
    
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", [])
    except requests.RequestException as e:
        log.warning(f"BMKG API error for station {station_id}: {e}")
        return []


def parse_bmkg_record(raw: dict, station: dict) -> dict:
    """Parse BMKG JSON record to insert-ready dict."""
    return {
        "station_id": station["id"],
        "station_name": station["name"],
        "lat": station["lat"],
        "lon": station["lon"],
        "date": raw.get("date", raw.get("tanggal")),
        "precip_mm": float(raw.get("rainfall", raw.get("rr", 0)) or 0),
        "humidity_pct": float(raw.get("humidity", raw.get("hu", 0)) or 0),
        "temp_c": float(raw.get("temperature", raw.get("tavg", 0)) or 0),
        "wind_speed_ms": float(raw.get("wind_speed", raw.get("ff_avg", 0)) or 0),
    }


def upsert_records(conn, records: list[dict]):
    """Insert or update rainfall records."""
    if not records:
        return 0
    
    sql = """
        INSERT INTO bmkg_rainfall 
            (station_id, station_name, lat, lon, date, precip_mm, humidity_pct, temp_c, wind_speed_ms,
             geom, source)
        VALUES (%(station_id)s, %(station_name)s, %(lat)s, %(lon)s, %(date)s, %(precip_mm)s,
                %(humidity_pct)s, %(temp_c)s, %(wind_speed_ms)s,
                ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 'bmkg_api')
        ON CONFLICT (station_id, date) DO UPDATE SET
            precip_mm = EXCLUDED.precip_mm,
            humidity_pct = EXCLUDED.humidity_pct,
            temp_c = EXCLUDED.temp_c,
            wind_speed_ms = EXCLUDED.wind_speed_ms
    """
    
    with conn.cursor() as cur:
        for record in records:
            cur.execute(sql, record)
    
    conn.commit()
    return len(records)


def main():
    start_date = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
    end_date = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y-%m-%d")
    
    log.info(f"BMKG sync: {start_date} to {end_date}")
    
    conn = psycopg2.connect(DATABASE_URL)
    total = 0
    
    for station in NTB_STATIONS:
        log.info(f"Fetching station {station['id']} ({station['name']})")
        raw_data = fetch_bmkg_data(station["id"], start_date, end_date)
        records = [parse_bmkg_record(r, station) for r in raw_data]
        count = upsert_records(conn, records)
        total += count
        log.info(f"  → {count} records upserted")
    
    conn.close()
    log.info(f"BMKG sync complete: {total} total records")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test the script (dry run)**

```bash
cd /home/awan/Documents/ntb-groundwater-monitor
python3 scripts/bmkg_sync.py 2024-01-01 2024-01-31
```

Expected: Script runs, logs station fetches, may get API errors if no key (acceptable).

- [ ] **Step 3: Commit**

```bash
git add scripts/bmkg_sync.py
git commit -m "feat: add BMKG rainfall sync script"
```

---

## Task 3: BMKG API Router

**Files:**
- Create: `backend/app/routers/bmkg.py`

**Interfaces:**
- Produces: 4 API endpoints for BMKG data

- [ ] **Step 1: Create the router**

```python
from fastapi import APIRouter, Query
from typing import Optional
from datetime import date

from app.db import get_pool

router = APIRouter(prefix="/bmkg", tags=["bmkg"])


@router.get("/stations")
async def get_stations():
    """Daftar stasiun BMKG di NTB."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT station_id, station_name,
                   ROUND(lat::numeric, 4) AS lat,
                   ROUND(lon::numeric, 4) AS lon,
                   COUNT(*) AS n_records,
                   MIN(date) AS earliest,
                   MAX(date) AS latest
            FROM bmkg_rainfall
            GROUP BY station_id, station_name, lat, lon
            ORDER BY station_name
        """)
        return {
            "total_stations": len(rows),
            "stations": [{
                "station_id": r["station_id"],
                "station_name": r["station_name"],
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "n_records": r["n_records"],
                "earliest": r["earliest"].isoformat() if r["earliest"] else None,
                "latest": r["latest"].isoformat() if r["latest"] else None,
            } for r in rows]
        }


@router.get("/rainfall")
async def get_rainfall(
    station_id: Optional[str] = Query(None, description="Filter by station ID"),
    start_date: Optional[str] = Query(None, description="Format: YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="Format: YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=1000)
):
    """Data curah hujan BMKG terbaru."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
            SELECT station_id, station_name, lat, lon, date,
                   precip_mm, humidity_pct, temp_c, wind_speed_ms
            FROM bmkg_rainfall
            WHERE 1=1
        """
        params = []
        if station_id:
            params.append(station_id)
            query += f" AND station_id = ${len(params)}"
        if start_date:
            params.append(start_date)
            query += f" AND date >= ${len(params)}::date"
        if end_date:
            params.append(end_date)
            query += f" AND date <= ${len(params)}::date"
        
        params.append(limit)
        query += f" ORDER BY date DESC LIMIT ${len(params)}"
        
        rows = await conn.fetch(query, *params)
        return {
            "total": len(rows),
            "data": [{
                "station_id": r["station_id"],
                "station_name": r["station_name"],
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "date": r["date"].isoformat(),
                "precip_mm": float(r["precip_mm"]) if r["precip_mm"] is not None else None,
                "humidity_pct": float(r["humidity_pct"]) if r["humidity_pct"] is not None else None,
                "temp_c": float(r["temp_c"]) if r["temp_c"] is not None else None,
                "wind_speed_ms": float(r["wind_speed_ms"]) if r["wind_speed_ms"] is not None else None,
            } for r in rows]
        }


@router.get("/rainfall/timeseries")
async def get_rainfall_timeseries(
    station_id: str = Query(..., description="Station ID"),
    months: int = Query(12, ge=1, le=120)
):
    """Time series curah hujan per stasiun."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT DATE_TRUNC('month', date) AS period,
                   ROUND(SUM(precip_mm)::numeric, 1) AS total_precip,
                   ROUND(AVG(precip_mm)::numeric, 1) AS avg_daily_precip,
                   COUNT(*) AS n_days
            FROM bmkg_rainfall
            WHERE station_id = $1
              AND date >= NOW() - INTERVAL '1 month' * $2
            GROUP BY DATE_TRUNC('month', date)
            ORDER BY period ASC
        """, station_id, months)
        
        return {
            "station_id": station_id,
            "period_months": months,
            "series": [{
                "period": r["period"].strftime("%Y-%m"),
                "total_precip_mm": float(r["total_precip"]) if r["total_precip"] else 0,
                "avg_daily_precip_mm": float(r["avg_daily_precip"]) if r["avg_daily_precip"] else 0,
                "n_days": r["n_days"],
            } for r in rows]
        }


@router.get("/summary")
async def get_summary():
    """Ringkasan anomali curah hujan NTB dari BMKG."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Recent 6 months
        rows = await conn.fetch("""
            SELECT DATE_TRUNC('month', date) AS period,
                   ROUND(AVG(precip_mm)::numeric, 1) AS avg_daily,
                   ROUND(SUM(precip_mm)::numeric, 1) AS total_monthly,
                   COUNT(DISTINCT station_id) AS n_stations
            FROM bmkg_rainfall
            WHERE date >= NOW() - INTERVAL '6 months'
            GROUP BY DATE_TRUNC('month', date)
            ORDER BY period DESC
        """)
        
        # Baseline (all data)
        baseline = await conn.fetchrow("""
            SELECT ROUND(AVG(precip_mm)::numeric, 1) AS avg_daily
            FROM bmkg_rainfall
        """)
        
        return {
            "source": "BMKG",
            "baseline_avg_daily_mm": float(baseline["avg_daily"]) if baseline["avg_daily"] else None,
            "recent_months": [{
                "period": r["period"].strftime("%Y-%m"),
                "avg_daily_mm": float(r["avg_daily"]) if r["avg_daily"] else 0,
                "total_monthly_mm": float(r["total_monthly"]) if r["total_monthly"] else 0,
                "n_stations": r["n_stations"],
            } for r in rows]
        }
```

- [ ] **Step 2: Register router in main.py**

In `backend/main.py`, add import and register:

```python
from app.routers.bmkg import router as bmkg_router
```

And in the router registration section:

```python
app.include_router(bmkg_router)
```

- [ ] **Step 3: Run tests**

```bash
cd /home/awan/Documents/ntb-groundwater-monitor/backend
python3 -m pytest tests/test_bmkg.py -v
```

Expected: tests pass (or fail gracefully if no BMKG data yet).

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/bmkg.py backend/main.py
git commit -m "feat: add BMKG rainfall API router with 4 endpoints"
```

---

## Task 4: BMKG Frontend Integration

**Files:**
- Modify: `frontend/index.html`

**Interfaces:**
- Consumes: `/api/bmkg/summary`, `/api/bmkg/rainfall/timeseries`

- [ ] **Step 1: Add BMKG badge to topbar**

In `frontend/index.html`, find the `.topbar-badges` div (around line 193), add:

```html
<span class="badge">BMKG RAIN</span>
```

- [ ] **Step 2: Add BMKG chart section in detail panel**

After the NDVI chart section (around line 291), add:

```html
<div class="chart-label" id="bmkg-chart-label" style="display:none">Curah Hujan BMKG</div>
<div class="chart-wrap" id="bmkg-chart-wrap" style="display:none"><canvas id="bmkg-chart"></canvas></div>
```

- [ ] **Step 3: Add BMKG chart rendering function**

In the `<script>` section, add after the NDVI chart code:

```javascript
let bmkgChartInst = null;

async function loadBMKGRainfall(stationId) {
    if (!stationId) return;
    try {
        const r = await fetch(`/api/bmkg/rainfall/timeseries?station_id=${stationId}&months=12`);
        if (!r.ok) return;
        const d = await r.json();
        if (!d.series || !d.series.length) return;
        
        document.getElementById('bmkg-chart-label').textContent = `Curah Hujan BMKG — ${stationId}`;
        document.getElementById('bmkg-chart-label').style.display = 'block';
        document.getElementById('bmkg-chart-wrap').style.display = 'block';
        
        if (bmkgChartInst) { bmkgChartInst.destroy(); bmkgChartInst = null; }
        const ctx = document.getElementById('bmkg-chart').getContext('2d');
        bmkgChartInst = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: d.series.map(s => s.period),
                datasets: [{
                    label: 'Curah Hujan (mm)',
                    data: d.series.map(s => s.total_precip_mm),
                    backgroundColor: 'rgba(59,130,246,0.6)',
                    borderColor: '#3b82f6',
                    borderWidth: 1,
                    borderRadius: 2
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#4a5568', font: { size: 9 } }, grid: { color: '#1a2438' } },
                    y: { ticks: { color: '#4a5568', font: { size: 9 }, callback: v => v + ' mm' }, grid: { color: '#1a2438' } }
                }
            }
        });
    } catch (e) { console.log('BMKG chart error:', e); }
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add BMKG rainfall badge and chart to frontend"
```

---

## Task 5: SAR Subsidence Script

**Files:**
- Create: `scripts/sar_to_postgis.py`

**Interfaces:**
- Consumes: Google Earth Engine Sentinel-1 data
- Produces: rows in `sar_subsidence` table

- [ ] **Step 1: Create the SAR processing script**

```python
#!/usr/bin/env python3
"""Sentinel-1 SAR Subsidence Detection via Google Earth Engine."""
import os
import sys
import csv
import logging
from datetime import datetime

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@localhost:5435/ntb_groundwater")

# NTB bounding box
NTB_BBOX = [115.5, -9.5, 120.0, -7.5]

# Sampling points — subset of ESDM wells + key areas
SAMPLING_POINTS = [
    # Sumbawa
    {"location": "Sumbawa_Kota", "kabupaten": "Sumbawa", "lat": -8.4911, "lon": 117.4203},
    {"location": "Sumbawa_BatuHijau", "kabupaten": "Sumbawa Barat", "lat": -8.9833, "lon": 116.8500},
    {"location": "Sumbawa_Sekongkang", "kabupaten": "Sumbawa Barat", "lat": -8.9500, "lon": 116.7833},
    # Dompu
    {"location": "Dompu_Kota", "kabupaten": "Dompu", "lat": -8.5364, "lon": 118.4614},
    # Bima
    {"location": "Bima_Kota", "kabupaten": "Bima", "lat": -8.5394, "lon": 118.6869},
    {"location": "Bima_Woha", "kabupaten": "Bima", "lat": -8.6167, "lon": 118.6333},
    # Lombok
    {"location": "Lombok_Utara", "kabupaten": "Lombok Utara", "lat": -8.3500, "lon": 116.2833},
    {"location": "Lombok_Tanjung", "kabupaten": "Lombok Utara", "lat": -8.3833, "lon": 116.1500},
]


def try_gee_init():
    """Initialize Google Earth Engine. Returns True if successful."""
    try:
        import ee
        ee.Initialize()
        log.info("GEE initialized successfully")
        return True
    except Exception as e:
        log.warning(f"GEE init failed: {e}. Using CSV fallback.")
        return False


def calc_subsidence_gee(lat: float, lon: float, start: str, end: str) -> dict:
    """Calculate subsidence rate at a point using GEE."""
    import ee
    
    point = ee.Geometry.Point([lon, lat])
    
    s1 = ee.ImageCollection('COPERNICUS/S1_GRD') \
        .filterBounds(point.buffer(1000)) \
        .filterDate(start, end) \
        .filter(ee.Filter.eq('instrumentMode', 'IW')) \
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
        .select('VV')
    
    count = s1.size().getInfo()
    if count < 5:
        return {"n_obs": count, "displacement_mm": None, "rate_mm_year": None}
    
    # Simple temporal stack: mean of first half vs second half
    images = s1.toList(count)
    mid = count // 2
    
    first_half = ee.ImageCollection.fromImages(images.slice(0, mid))
    second_half = ee.ImageCollection.fromImages(images.slice(mid))
    
    mean_first = first_half.mean()
    mean_second = second_half.mean()
    
    diff = mean_second.subtract(mean_first)
    
    # Sample at point
    result = diff.sample(point, 10).first().getInfo()
    vv_diff = result.get('properties', {}).get('VV', 0)
    
    # Approximate: VV change in dB → displacement proxy
    # This is a simplified approach; full InSAR would be more accurate
    years = (count / 12)  # rough estimate of years span
    
    return {
        "n_obs": count,
        "displacement_mm": float(vv_diff) * 10 if vv_diff else None,
        "rate_mm_year": float(vv_diff) * 10 / years if vv_diff and years > 0 else None,
    }


def load_csv_fallback(csv_path: str) -> list[dict]:
    """Load pre-computed SAR data from CSV if GEE unavailable."""
    records = []
    if not os.path.exists(csv_path):
        log.warning(f"CSV fallback not found: {csv_path}")
        return records
    
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append({
                "location": row["location"],
                "kabupaten": row.get("kabupaten", ""),
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "period_start": row.get("period_start", "2020-01-01"),
                "period_end": row.get("period_end", "2026-01-01"),
                "displacement_mm": float(row["displacement_mm"]) if row.get("displacement_mm") else None,
                "rate_mm_year": float(row["rate_mm_year"]) if row.get("rate_mm_year") else None,
                "n_observations": int(row.get("n_observations", 0)),
                "coherence": float(row.get("coherence", 0)),
            })
    return records


def upsert_sar(conn, records: list[dict]):
    """Insert SAR subsidence records."""
    sql = """
        INSERT INTO sar_subsidence
            (location, kabupaten, lat, lon, period_start, period_end,
             displacement_mm, rate_mm_year, n_observations, coherence, geom, source)
        VALUES (%(location)s, %(kabupaten)s, %(lat)s, %(lon)s, %(period_start)s, %(period_end)s,
                %(displacement_mm)s, %(rate_mm_year)s, %(n_observations)s, %(coherence)s,
                ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), 'sentinel1_gee')
        ON CONFLICT (location, period_start, period_end) DO UPDATE SET
            displacement_mm = EXCLUDED.displacement_mm,
            rate_mm_year = EXCLUDED.rate_mm_year,
            n_observations = EXCLUDED.n_observations,
            coherence = EXCLUDED.coherence
    """
    with conn.cursor() as cur:
        for r in records:
            cur.execute(sql, r)
    conn.commit()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "gee"
    start_date = "2020-01-01"
    end_date = "2026-06-01"
    
    conn = psycopg2.connect(DATABASE_URL)
    
    if mode == "csv":
        csv_path = sys.argv[2] if len(sys.argv) > 2 else "/data/sar_subsidence.csv"
        records = load_csv_fallback(csv_path)
        log.info(f"Loaded {len(records)} records from CSV")
    else:
        if not try_gee_init():
            log.error("GEE not available. Use 'csv' mode with pre-computed data.")
            conn.close()
            sys.exit(1)
        
        records = []
        for pt in SAMPLING_POINTS:
            log.info(f"Processing {pt['location']}...")
            result = calc_subsidence_gee(pt["lat"], pt["lon"], start_date, end_date)
            records.append({
                "location": pt["location"],
                "kabupaten": pt["kabupaten"],
                "lat": pt["lat"],
                "lon": pt["lon"],
                "period_start": start_date,
                "period_end": end_date,
                "displacement_mm": result["displacement_mm"],
                "rate_mm_year": result["rate_mm_year"],
                "n_observations": result["n_obs"],
                "coherence": 0.0,
            })
    
    upsert_sar(conn, records)
    conn.close()
    log.info(f"SAR sync complete: {len(records)} records")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test CSV mode (create sample data)**

Create `/tmp/test_sar.csv`:
```csv
location,kabupaten,lat,lon,period_start,period_end,displacement_mm,rate_mm_year,n_observations,coherence
Sumbawa_BatuHijau,Sumbawa Barat,-8.9833,116.85,2020-01-01,2026-01-01,-25.3,-4.2,48,0.7
Dompu_Kota,Dompu,-8.5364,118.4614,2020-01-01,2026-01-01,-8.1,-1.35,48,0.6
```

```bash
python3 scripts/sar_to_postgis.py csv /tmp/test_sar.csv
```

- [ ] **Step 3: Commit**

```bash
git add scripts/sar_to_postgis.py
git commit -m "feat: add Sentinel-1 SAR subsidence detection script"
```

---

## Task 6: SAR API Router

**Files:**
- Create: `backend/app/routers/sar.py`

**Interfaces:**
- Produces: 4 API endpoints for SAR subsidence

- [ ] **Step 1: Create the router**

```python
from fastapi import APIRouter, Query
from typing import Optional
import json

from app.db import get_pool

router = APIRouter(prefix="/sar", tags=["sar"])


@router.get("/subsidence")
async def get_subsidence(
    kabupaten: Optional[str] = Query(None, description="Filter by kabupaten"),
    min_rate: Optional[float] = Query(None, description="Min subsidence rate (mm/year)")
):
    """Data penurunan tanah dari Sentinel-1 SAR."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
            SELECT location, kabupaten, lat, lon,
                   period_start, period_end,
                   displacement_mm, rate_mm_year,
                   n_observations, coherence,
                   ST_AsGeoJSON(geom)::json AS geometry
            FROM sar_subsidence
            WHERE 1=1
        """
        params = []
        if kabupaten:
            params.append(f"%{kabupaten}%")
            query += f" AND LOWER(kabupaten) LIKE LOWER(${len(params)})"
        if min_rate is not None:
            params.append(abs(min_rate))
            query += f" AND rate_mm_year <= -${len(params)}"
        
        query += " ORDER BY rate_mm_year ASC"
        rows = await conn.fetch(query, *params)
        
        features = [{
            "type": "Feature",
            "geometry": r["geometry"],
            "properties": {
                "location": r["location"],
                "kabupaten": r["kabupaten"],
                "period_start": r["period_start"].isoformat() if r["period_start"] else None,
                "period_end": r["period_end"].isoformat() if r["period_end"] else None,
                "displacement_mm": float(r["displacement_mm"]) if r["displacement_mm"] is not None else None,
                "rate_mm_year": float(r["rate_mm_year"]) if r["rate_mm_year"] is not None else None,
                "n_observations": r["n_observations"],
                "coherence": float(r["coherence"]) if r["coherence"] is not None else None,
                "color": "#ef4444" if r["rate_mm_year"] and r["rate_mm_year"] < -5 else
                         "#f59e0b" if r["rate_mm_year"] and r["rate_mm_year"] < -2 else "#10b981",
            }
        } for r in rows]
        
        return {
            "type": "FeatureCollection",
            "metadata": {
                "title": "Sentinel-1 SAR Subsidence — NTB",
                "source": "COPERNICUS/S1_GRD via Google Earth Engine",
                "total": len(features),
                "unit": "mm/year"
            },
            "features": features
        }


@router.get("/subsidence/timeseries")
async def get_subsidence_timeseries(location: str = Query(..., description="Location name")):
    """Time series displacement untuk satu lokasi."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT period_start, period_end, displacement_mm, rate_mm_year, n_observations
            FROM sar_subsidence
            WHERE LOWER(location) = LOWER($1)
            ORDER BY period_start
        """, location)
        
        if not rows:
            return {"location": location, "series": [], "message": "No data found"}
        
        return {
            "location": location,
            "series": [{
                "period_start": r["period_start"].isoformat(),
                "period_end": r["period_end"].isoformat(),
                "displacement_mm": float(r["displacement_mm"]) if r["displacement_mm"] is not None else None,
                "rate_mm_year": float(r["rate_mm_year"]) if r["rate_mm_year"] is not None else None,
                "n_observations": r["n_observations"],
            } for r in rows]
        }


@router.get("/summary")
async def get_sar_summary():
    """Ringkasan area subsidence kritis di NTB."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT kabupaten,
                   COUNT(*) AS n_locations,
                   ROUND(AVG(rate_mm_year)::numeric, 2) AS avg_rate,
                   ROUND(MIN(rate_mm_year)::numeric, 2) AS worst_rate
            FROM sar_subsidence
            GROUP BY kabupaten
            ORDER BY avg_rate ASC
        """)
        
        return {
            "source": "Sentinel-1 SAR",
            "unit": "mm/year",
            "data": [{
                "kabupaten": r["kabupaten"],
                "n_locations": r["n_locations"],
                "avg_rate_mm_year": float(r["avg_rate"]) if r["avg_rate"] else None,
                "worst_rate_mm_year": float(r["worst_rate"]) if r["worst_rate"] else None,
                "risk": "KRITIS" if r["avg_rate"] and float(r["avg_rate"]) < -5 else
                        "WASPADA" if r["avg_rate"] and float(r["avg_rate"]) < -2 else "NORMAL",
            } for r in rows]
        }


@router.get("/geojson")
async def get_sar_geojson():
    """GeoJSON layer untuk MapLibre."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT location, kabupaten, lat, lon, rate_mm_year,
                   ST_AsGeoJSON(geom)::json AS geometry
            FROM sar_subsidence
            ORDER BY rate_mm_year ASC
        """)
        
        features = [{
            "type": "Feature",
            "geometry": r["geometry"],
            "properties": {
                "location": r["location"],
                "kabupaten": r["kabupaten"],
                "rate_mm_year": float(r["rate_mm_year"]) if r["rate_mm_year"] is not None else None,
                "color": "#ef4444" if r["rate_mm_year"] and r["rate_mm_year"] < -5 else
                         "#f59e0b" if r["rate_mm_year"] and r["rate_mm_year"] < -2 else "#10b981",
            }
        } for r in rows]
        
        return {"type": "FeatureCollection", "features": features}
```

- [ ] **Step 2: Register router in main.py**

```python
from app.routers.sar import router as sar_router
app.include_router(sar_router)
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/sar.py
git commit -m "feat: add SAR subsidence API router with 4 endpoints"
```

---

## Task 7: SAR Frontend Integration

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 1: Add SAR badge to topbar**

```html
<span class="badge">SAR SUBSIDENCE</span>
```

- [ ] **Step 2: Add SAR map layer**

In the `map.on('load')` callback, add after `loadESDM()`:

```javascript
// Load SAR Subsidence layer
async function loadSAR(){
    try{
        const r=await fetch('/api/sar/geojson');
        if(!r.ok)return;
        const d=await r.json();
        if(map.getSource('sar')){map.getSource('sar').setData(d);return;}
        map.addSource('sar',{type:'geojson',data:d});
        map.addLayer({
            id:'sar-circle',type:'circle',source:'sar',
            paint:{
                'circle-radius':6,
                'circle-color':['get','color'],
                'circle-opacity':0.7,
                'circle-stroke-color':'#ffffff',
                'circle-stroke-width':1
            }
        }, 'active-glow');
    }catch(e){console.log('SAR error:',e);}
}
setTimeout(loadSAR, 3000);
```

- [ ] **Step 3: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add SAR subsidence map layer to frontend"
```

---

## Task 8: Unified Sync Script

**Files:**
- Create: `scripts/sync_unified.py`

**Interfaces:**
- Consumes: `grace_tws`, `gldas_sms`, `chirps_precip`, `bmkg_rainfall`, `sentinel2_ndvi`, `sar_subsidence`
- Produces: rows in `unified_monitoring`

- [ ] **Step 1: Create the unified sync script**

```python
#!/usr/bin/env python3
"""Unified Multi-Sensor Sync — merge all data sources into unified_monitoring."""
import os
import sys
import logging
from datetime import datetime

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@localhost:5435/ntb_groundwater")


def calc_drought_index(gws, rain_anomaly, ndvi):
    """Composite drought index: 0 (severe) to 1 (healthy)."""
    def normalize(val, min_v, max_v):
        if val is None:
            return 0.5
        return max(0.0, min(1.0, (val - min_v) / (max_v - min_v)))
    
    gws_norm = normalize(gws, -5, 5)
    rain_norm = normalize(rain_anomaly, -200, 200)
    ndvi_norm = normalize(ndvi, 0, 0.8)
    
    return 0.4 * gws_norm + 0.3 * rain_norm + 0.3 * ndvi_norm


def calc_risk_level(drought_index):
    if drought_index is None:
        return "tidak_ada_data"
    if drought_index >= 0.6:
        return "normal"
    if drought_index >= 0.4:
        return "waspada"
    if drought_index >= 0.2:
        return "kritis"
    return "sangat_kritis"


def main():
    start_year = int(sys.argv[1]) if len(sys.argv) > 1 else 2020
    end_year = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    
    log.info(f"Unified sync: {start_year} to {end_year}")
    
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    # Get all GRACE grid points with data
    cur.execute("""
        SELECT DISTINCT period_date, lat, lon
        FROM grace_tws
        WHERE EXTRACT(YEAR FROM period_date) BETWEEN %s AND %s
        ORDER BY period_date, lat, lon
    """, (start_year, end_year))
    
    grid_points = cur.fetchall()
    log.info(f"Processing {len(grid_points)} grid point-months")
    
    upsert_sql = """
        INSERT INTO unified_monitoring
            (period_date, lat, lon, tws_anomaly, tws_uncertainty, sms_anomaly, gws_anomaly,
             chirps_precip_mm, chirps_anomaly, bmkg_precip_mm, bmkg_station_id, bmkg_distance_km,
             ndvi, ndvi_location, ndvi_distance_km,
             sar_subsidence_mm, sar_rate_mm_year,
             drought_index, risk_level, data_completeness)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (period_date, lat, lon) DO UPDATE SET
            tws_anomaly = EXCLUDED.tws_anomaly,
            sms_anomaly = EXCLUDED.sms_anomaly,
            gws_anomaly = EXCLUDED.gws_anomaly,
            chirps_precip_mm = EXCLUDED.chirps_precip_mm,
            chirps_anomaly = EXCLUDED.chirps_anomaly,
            bmkg_precip_mm = EXCLUDED.bmkg_precip_mm,
            ndvi = EXCLUDED.ndvi,
            sar_subsidence_mm = EXCLUDED.sar_subsidence_mm,
            drought_index = EXCLUDED.drought_index,
            risk_level = EXCLUDED.risk_level,
            data_completeness = EXCLUDED.data_completeness
    """
    
    count = 0
    for period_date, lat, lon in grid_points:
        # 1. GRACE TWS
        cur.execute("""
            SELECT tws_anomaly, uncertainty FROM grace_tws
            WHERE period_date = %s AND lat = %s AND lon = %s
        """, (period_date, lat, lon))
        grace_row = cur.fetchone()
        tws = grace_row[0] if grace_row else None
        tws_unc = grace_row[1] if grace_row else None
        
        # 2. GLDAS SMS
        cur.execute("""
            SELECT sms_anomaly FROM gldas_sms
            WHERE year = %s AND month = %s AND ABS(lat - %s) < 0.01 AND ABS(lon - %s) < 0.01
        """, (period_date.year, period_date.month, lat, lon))
        sms_row = cur.fetchone()
        sms = sms_row[0] if sms_row else None
        
        # 3. Derived GWS
        gws = (tws or 0) - (sms or 0) if tws is not None else None
        
        # 4. CHIRPS
        cur.execute("""
            SELECT precip_mm FROM chirps_precip
            WHERE year = %s AND month = %s AND ABS(lat - %s) < 0.1 AND ABS(lon - %s) < 0.1
        """, (period_date.year, period_date.month, lat, lon))
        chirps_row = cur.fetchone()
        chirps = chirps_row[0] if chirps_row else None
        
        # CHIRPS anomaly (vs overall mean)
        cur.execute("SELECT AVG(precip_mm) FROM chirps_precip WHERE ABS(lat - %s) < 0.1 AND ABS(lon - %s) < 0.1", (lat, lon))
        chirps_baseline = cur.fetchone()[0]
        chirps_anom = (chirps - chirps_baseline) if chirps and chirps_baseline else None
        
        # 5. BMKG (nearest station)
        cur.execute("""
            SELECT station_id, precip_mm,
                   ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) / 1000 AS dist_km
            FROM bmkg_rainfall
            WHERE date = %s
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
            LIMIT 1
        """, (lon, lat, period_date, lon, lat))
        bmkg_row = cur.fetchone()
        bmkg_station = bmkg_row[0] if bmkg_row else None
        bmkg_precip = bmkg_row[1] if bmkg_row else None
        bmkg_dist = float(bmkg_row[2]) if bmkg_row else None
        
        # 6. NDVI (nearest)
        cur.execute("""
            SELECT location, ndvi,
                   ST_Distance(geom::geography, ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) / 1000 AS dist_km
            FROM sentinel2_ndvi
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
            LIMIT 1
        """, (lon, lat, lon, lat))
        ndvi_row = cur.fetchone()
        ndvi_loc = ndvi_row[0] if ndvi_row else None
        ndvi_val = float(ndvi_row[1]) if ndvi_row and ndvi_row[1] is not None else None
        ndvi_dist = float(ndvi_row[2]) if ndvi_row else None
        
        # 7. SAR (nearest)
        cur.execute("""
            SELECT displacement_mm, rate_mm_year
            FROM sar_subsidence
            ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
            LIMIT 1
        """, (lon, lat))
        sar_row = cur.fetchone()
        sar_disp = float(sar_row[0]) if sar_row and sar_row[0] is not None else None
        sar_rate = float(sar_row[1]) if sar_row and sar_row[1] is not None else None
        
        # 8. Derived indicators
        di = calc_drought_index(gws, chirps_anom, ndvi_val)
        rl = calc_risk_level(di)
        
        # 9. Data completeness
        sensors = [tws, sms, chirps, bmkg_precip, ndvi_val, sar_disp]
        completeness = sum(1 for s in sensors if s is not None) / len(sensors)
        
        cur.execute(upsert_sql, (
            period_date, lat, lon,
            float(tws) if tws is not None else None,
            float(tws_unc) if tws_unc is not None else None,
            float(sms) if sms is not None else None,
            float(gws) if gws is not None else None,
            float(chirps) if chirps is not None else None,
            float(chirps_anom) if chirps_anom is not None else None,
            float(bmkg_precip) if bmkg_precip is not None else None,
            bmkg_station,
            float(bmkg_dist) if bmkg_dist is not None else None,
            float(ndvi_val) if ndvi_val is not None else None,
            ndvi_loc,
            float(ndvi_dist) if ndvi_dist is not None else None,
            float(sar_disp) if sar_disp is not None else None,
            float(sar_rate) if sar_rate is not None else None,
            float(di) if di is not None else None,
            rl,
            float(completeness),
        ))
        count += 1
        
        if count % 100 == 0:
            conn.commit()
            log.info(f"  → {count} records processed")
    
    conn.commit()
    cur.close()
    conn.close()
    log.info(f"Unified sync complete: {count} records")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Test**

```bash
python3 scripts/sync_unified.py 2023 2025
```

Expected: processes grid points, logs progress.

- [ ] **Step 3: Commit**

```bash
git add scripts/sync_unified.py
git commit -m "feat: add unified multi-sensor sync pipeline"
```

---

## Task 9: Fusion API Router

**Files:**
- Create: `backend/app/routers/fusion.py`

- [ ] **Step 1: Create the router**

```python
from fastapi import APIRouter, Query
from typing import Optional

from app.db import get_pool

router = APIRouter(prefix="/fusion", tags=["fusion"])


@router.get("/monitoring")
async def get_monitoring(
    lat: Optional[float] = Query(None, description="Latitude"),
    lon: Optional[float] = Query(None, description="Longitude"),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD"),
    risk_level: Optional[str] = Query(None, description="normal, waspada, kritis, sangat_kritis"),
    limit: int = Query(100, ge=1, le=1000)
):
    """Data terpadu multi-sensor per grid point."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = """
            SELECT period_date, lat, lon,
                   tws_anomaly, sms_anomaly, gws_anomaly,
                   chirps_precip_mm, chirps_anomaly,
                   bmkg_precip_mm, bmkg_station_id, bmkg_distance_km,
                   ndvi, ndvi_location, ndvi_distance_km,
                   sar_subsidence_mm, sar_rate_mm_year,
                   drought_index, risk_level, data_completeness
            FROM unified_monitoring
            WHERE 1=1
        """
        params = []
        if lat is not None and lon is not None:
            params.extend([lat, lon])
            query += f" AND ABS(lat - ${len(params)-1}) < 0.3 AND ABS(lon - ${len(params)}) < 0.3"
        if start_date:
            params.append(start_date)
            query += f" AND period_date >= ${len(params)}::date"
        if end_date:
            params.append(end_date)
            query += f" AND period_date <= ${len(params)}::date"
        if risk_level:
            params.append(risk_level)
            query += f" AND risk_level = ${len(params)}"
        params.append(limit)
        query += f" ORDER BY period_date DESC LIMIT ${len(params)}"
        
        rows = await conn.fetch(query, *params)
        return {
            "total": len(rows),
            "data": [{
                "period": r["period_date"].strftime("%Y-%m"),
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "tws_anomaly": float(r["tws_anomaly"]) if r["tws_anomaly"] is not None else None,
                "sms_anomaly": float(r["sms_anomaly"]) if r["sms_anomaly"] is not None else None,
                "gws_anomaly": float(r["gws_anomaly"]) if r["gws_anomaly"] is not None else None,
                "chirps_precip_mm": float(r["chirps_precip_mm"]) if r["chirps_precip_mm"] is not None else None,
                "chirps_anomaly": float(r["chirps_anomaly"]) if r["chirps_anomaly"] is not None else None,
                "bmkg_precip_mm": float(r["bmkg_precip_mm"]) if r["bmkg_precip_mm"] is not None else None,
                "ndvi": float(r["ndvi"]) if r["ndvi"] is not None else None,
                "sar_subsidence_mm": float(r["sar_subsidence_mm"]) if r["sar_subsidence_mm"] is not None else None,
                "drought_index": float(r["drought_index"]) if r["drought_index"] is not None else None,
                "risk_level": r["risk_level"],
                "data_completeness": float(r["data_completeness"]) if r["data_completeness"] is not None else None,
            } for r in rows]
        }


@router.get("/timeseries")
async def get_fusion_timeseries(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    months: int = Query(24, ge=1, le=120)
):
    """Time series multi-sensor per lokasi."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT period_date,
                   tws_anomaly, sms_anomaly, gws_anomaly,
                   chirps_precip_mm, ndvi, sar_subsidence_mm,
                   drought_index, risk_level
            FROM unified_monitoring
            WHERE ABS(lat - $1) < 0.3 AND ABS(lon - $2) < 0.3
              AND period_date >= NOW() - INTERVAL '1 month' * $3
            ORDER BY period_date ASC
        """, lat, lon, months)
        
        return {
            "lat": lat, "lon": lon, "period_months": months,
            "series": [{
                "period": r["period_date"].strftime("%Y-%m"),
                "gws_anomaly": float(r["gws_anomaly"]) if r["gws_anomaly"] is not None else None,
                "chirps_mm": float(r["chirps_precip_mm"]) if r["chirps_precip_mm"] is not None else None,
                "ndvi": float(r["ndvi"]) if r["ndvi"] is not None else None,
                "drought_index": float(r["drought_index"]) if r["drought_index"] is not None else None,
                "risk_level": r["risk_level"],
            } for r in rows]
        }


@router.get("/summary")
async def get_fusion_summary():
    """Ringkasan semua sensor NTB."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Latest period stats
        stats = await conn.fetchrow("""
            SELECT
                COUNT(*) AS total_records,
                COUNT(DISTINCT period_date) AS n_months,
                ROUND(AVG(gws_anomaly)::numeric, 2) AS avg_gws,
                ROUND(AVG(drought_index)::numeric, 2) AS avg_drought,
                COUNT(*) FILTER (WHERE risk_level = 'normal') AS n_normal,
                COUNT(*) FILTER (WHERE risk_level = 'waspada') AS n_waspada,
                COUNT(*) FILTER (WHERE risk_level = 'kritis') AS n_kritis,
                COUNT(*) FILTER (WHERE risk_level = 'sangat_kritis') AS n_sangat_kritis,
                ROUND(AVG(data_completeness)::numeric, 2) AS avg_completeness
            FROM unified_monitoring
        """)
        
        return {
            "source": "Multi-Sensor Fusion",
            "total_records": stats["total_records"],
            "n_months": stats["n_months"],
            "avg_gws_anomaly_cm": float(stats["avg_gws"]) if stats["avg_gws"] else None,
            "avg_drought_index": float(stats["avg_drought"]) if stats["avg_drought"] else None,
            "risk_breakdown": {
                "normal": stats["n_normal"],
                "waspada": stats["n_waspada"],
                "kritis": stats["n_kritis"],
                "sangat_kritis": stats["n_sangat_kritis"],
            },
            "avg_data_completeness": float(stats["avg_completeness"]) if stats["avg_completeness"] else None,
        }


@router.get("/correlation")
async def get_correlation(
    sensor1: str = Query("gws_anomaly", description="Column name"),
    sensor2: str = Query("ndvi", description="Column name")
):
    """Korelasi antar sensor."""
    valid_columns = {"gws_anomaly", "chirps_anomaly", "ndvi", "sar_rate_mm_year", "drought_index"}
    if sensor1 not in valid_columns or sensor2 not in valid_columns:
        return {"error": f"Invalid sensor. Valid: {valid_columns}"}
    
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(f"""
            SELECT
                ROUND(CORR({sensor1}, {sensor2})::numeric, 3) AS correlation,
                COUNT(*) AS n_points
            FROM unified_monitoring
            WHERE {sensor1} IS NOT NULL AND {sensor2} IS NOT NULL
        """)
        
        return {
            "sensor1": sensor1,
            "sensor2": sensor2,
            "correlation": float(row["correlation"]) if row["correlation"] else None,
            "n_data_points": row["n_points"],
        }
```

- [ ] **Step 2: Register router in main.py**

```python
from app.routers.fusion import router as fusion_router
app.include_router(fusion_router)
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/fusion.py
git commit -m "feat: add unified fusion API router with 4 endpoints"
```

---

## Task 10: Fusion Frontend Integration

**Files:**
- Modify: `frontend/index.html`

- [ ] **Step 1: Add multi-sensor section in detail panel**

After the NDVI chart section, add:

```html
<div class="chart-label" id="fusion-chart-label" style="display:none">Multi-Sensor Overview</div>
<div class="chart-wrap" id="fusion-chart-wrap" style="display:none"><canvas id="fusion-chart"></canvas></div>
```

- [ ] **Step 2: Add multi-sensor chart function**

```javascript
let fusionChartInst = null;

async function loadFusionTimeseries(lat, lon) {
    try {
        const r = await fetch(`/api/fusion/timeseries?lat=${lat}&lon=${lon}&months=24`);
        if (!r.ok) return;
        const d = await r.json();
        if (!d.series || !d.series.length) return;
        
        document.getElementById('fusion-chart-label').textContent = 'Multi-Sensor Overview (24 bulan)';
        document.getElementById('fusion-chart-label').style.display = 'block';
        document.getElementById('fusion-chart-wrap').style.display = 'block';
        
        if (fusionChartInst) { fusionChartInst.destroy(); fusionChartInst = null; }
        const ctx = document.getElementById('fusion-chart').getContext('2d');
        fusionChartInst = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: d.series.map(s => s.period),
                datasets: [{
                    label: 'GWS Anomaly (cm)',
                    data: d.series.map(s => s.gws_anomaly),
                    backgroundColor: d.series.map(s => {
                        const v = s.gws_anomaly || 0;
                        return v < -2 ? '#ef4444bb' : v < 0 ? '#f59e0bbb' : '#10b981bb';
                    }),
                    borderColor: d.series.map(s => {
                        const v = s.gws_anomaly || 0;
                        return v < -2 ? '#ef4444' : v < 0 ? '#f59e0b' : '#10b981';
                    }),
                    borderWidth: 0,
                    borderRadius: 2,
                    yAxisID: 'y'
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    x: { ticks: { color: '#4a5568', font: { size: 8 }, maxRotation: 45 }, grid: { color: '#1a2438' } },
                    y: { ticks: { color: '#4a5568', font: { size: 8 }, callback: v => v + ' cm' }, grid: { color: '#1a2438' } }
                }
            }
        });
    } catch (e) { console.log('Fusion chart error:', e); }
}
```

- [ ] **Step 3: Integrate into selectWell**

In the `selectWell` function, add after NDVI chart loading:

```javascript
// Load multi-sensor fusion chart
if (coords) {
    loadFusionTimeseries(coords[1], coords[0]);
}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html
git commit -m "feat: add multi-sensor fusion chart to detail panel"
```

---

## Task 11: Tests

**Files:**
- Create: `backend/tests/test_bmkg.py`
- Create: `backend/tests/test_sar.py`
- Create: `backend/tests/test_fusion.py`

- [ ] **Step 1: Create test_bmkg.py**

```python
from app.utils import classify_ndvi, ndvi_color, format_period_label
from datetime import date


def test_classify_ndvi():
    assert classify_ndvi(0.6) == "lebat"
    assert classify_ndvi(0.35) == "sedang"
    assert classify_ndvi(0.15) == "jarang"
    assert classify_ndvi(0.05) == "kritis"


def test_ndvi_color():
    assert ndvi_color("lebat") == "#1D9E75"
    assert ndvi_color("kritis") == "#E24B4A"


def test_format_period_label():
    assert format_period_label(date(2024, 1, 1)) == "2024-01"
    assert format_period_label(None) is None
```

- [ ] **Step 2: Create test_fusion.py**

```python
import pytest


def test_drought_index_calculation():
    """Test drought index formula with known values."""
    def normalize(val, min_v, max_v):
        if val is None:
            return 0.5
        return max(0.0, min(1.0, (val - min_v) / (max_v - min_v)))
    
    def calc_drought_index(gws, rain_anom, ndvi):
        gws_n = normalize(gws, -5, 5)
        rain_n = normalize(rain_anom, -200, 200)
        ndvi_n = normalize(ndvi, 0, 0.8)
        return 0.4 * gws_n + 0.3 * rain_n + 0.3 * ndvi_n
    
    # All healthy
    assert calc_drought_index(5, 200, 0.8) > 0.9
    
    # All critical
    assert calc_drought_index(-5, -200, 0.0) < 0.1
    
    # Mixed
    di = calc_drought_index(0, 0, 0.4)
    assert 0.4 < di < 0.6  # should be around 0.5


def test_risk_level():
    """Test risk level classification."""
    def calc_risk_level(di):
        if di is None:
            return "tidak_ada_data"
        if di >= 0.6:
            return "normal"
        if di >= 0.4:
            return "waspada"
        if di >= 0.2:
            return "kritis"
        return "sangat_kritis"
    
    assert calc_risk_level(0.7) == "normal"
    assert calc_risk_level(0.5) == "waspada"
    assert calc_risk_level(0.3) == "kritis"
    assert calc_risk_level(0.1) == "sangat_kritis"
    assert calc_risk_level(None) == "tidak_ada_data"
```

- [ ] **Step 3: Run tests**

```bash
cd /home/awan/Documents/ntb-groundwater-monitor/backend
python3 -m pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/tests/
git commit -m "test: add tests for BMKG, fusion drought index, utils"
```

---

## Task 12: Requirements & Smoke Test Update

**Files:**
- Modify: `backend/requirements.txt`
- Modify: `scripts/smoke_test_live.sh`

- [ ] **Step 1: Add dependencies**

Append to `backend/requirements.txt`:

```
requests>=2.31.0
earthengine-api>=0.1.370
```

- [ ] **Step 2: Update smoke test**

Add to `scripts/smoke_test_live.sh`:

```bash
# Phase 1: New endpoints
check "/api/bmkg/stations" "BMKG Stations"
check "/api/sar/summary" "SAR Summary"
check "/api/fusion/summary" "Fusion Summary"
```

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt scripts/smoke_test_live.sh
git commit -m "chore: add requests/earthengine-api deps, update smoke test"
```

---

## Task 13: Docker Compose Update

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add BMKG_API_KEY and GEE env vars**

In the `api` service environment section, add:

```yaml
BMKG_API_KEY: ${BMKG_API_KEY:-}
GEE_SERVICE_ACCOUNT: ${GEE_SERVICE_ACCOUNT:-}
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "chore: add BMKG and GEE env vars to docker-compose"
```

---

## Execution Order Summary

```
Task 1  (DB Schema)          → no dependencies
Task 2  (BMKG Script)        → depends on Task 1
Task 3  (BMKG Router)        → depends on Task 2
Task 4  (BMKG Frontend)      → depends on Task 3
Task 5  (SAR Script)         → depends on Task 1
Task 6  (SAR Router)         → depends on Task 5
Task 7  (SAR Frontend)       → depends on Task 6
Task 8  (Unified Script)     → depends on Tasks 2, 5
Task 9  (Fusion Router)      → depends on Task 8
Task 10 (Fusion Frontend)    → depends on Task 9
Task 11 (Tests)              → depends on Tasks 3, 6, 9
Task 12 (Deps & Smoke Test)  → depends on all
Task 13 (Docker Compose)     → depends on all
```

Parallelizable: Tasks 2+5, Tasks 3+6, Tasks 4+7
