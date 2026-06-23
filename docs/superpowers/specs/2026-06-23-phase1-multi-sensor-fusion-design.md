# Phase 1: Multi-Sensor Groundwater Platform — Design Spec

> **Date:** 2026-06-23  
> **Author:** Rizki Agustiawan, S.T.  
> **Status:** Draft — Pending Review  
> **Scope:** 3 sub-projects (BMKG, Multi-sensor Pipeline, SAR Subsidence)

---

## 1. Overview

### Problem
NTB Groundwater Monitor saat ini memiliki pipeline data terpisah per sumber (GRACE, GLDAS, Sentinel-2). Tidak ada integrasi BMKG curah hujan real-time, tidak ada deteksi penurunan tanah (subsidence), dan tidak ada unified view yang menggabungkan semua sensor dalam satu analisis terpadu.

### Goal
Bangun fondasi multi-sensor untuk platform:
1. **BMKG Rainfall Integration** — data curah hujan real-time dari API BMKG
2. **Multi-sensor Data Fusion Pipeline** — unified ETL yang menggabungkan 6 sumber data
3. **Sentinel-1 SAR Subsidence Detection** — deteksi penurunan tanah dari SAR

### Success Criteria
- BMKG rainfall data tersedia di API dan dashboard
- Tabel `unified_monitoring` berisi data gabungan 6 sensor per grid point per bulan
- SAR subsidence data tersedia untuk area di sekitar 280 sumur ESDM
- Semua endpoint baru terdokumentasi di `/api/docs`
- Tidak ada regression pada fitur existing

---

## 2. Architecture

### Current State
```
GRACE ──→ grace_to_postgis.py ──→ grace_tws ──→ grace.py router
GLDAS ──→ gldas_to_postgis.py ──→ gldas_sms ──→ groundwater.py router
Sentinel-2 CSV ──→ load_ndvi_csv.py ──→ sentinel2_ndvi ──→ ndvi.py router
```
Setiap sumber punya pipeline sendiri, tidak terhubung satu sama lain.

### Target State
```
┌─────────────────────────────────────────────────────────┐
│                  DATA SOURCES                            │
│  GRACE  GLDAS  CHIRPS  BMKG(new)  Sentinel-2  Sentinel-1│
└────┬─────┬──────┬───────┬──────────┬──────────┬─────────┘
     │     │      │       │          │          │
     ▼     ▼      ▼       ▼          ▼          ▼
┌─────────────────────────────────────────────────────────┐
│              UNIFIED ETL PIPELINE                        │
│  scripts/sync_unified.py                                 │
│  → unified_monitoring (tabel gabungan)                   │
│  → per-titik: TWS, SMS, GWS, rainfall, NDVI,            │
│    subsidence, anomalies                                 │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              API LAYER (FastAPI)                          │
│  /api/bmkg/*           — BMKG rainfall                   │
│  /api/sar/*            — SAR subsidence                  │
│  /api/fusion/*         — Unified multi-sensor            │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              FRONTEND (index.html)                        │
│  + BMKG rainfall chart                                   │
│  + SAR subsidence map layer                              │
│  + Multi-sensor detail panel                             │
└─────────────────────────────────────────────────────────┘
```

### Dependency Graph
```
1a. BMKG Rainfall ──────────────┐
                                ├──→ 1b. Multi-sensor Pipeline
1b. Multi-sensor Pipeline ──────┤
                                │
1c. SAR Subsidence ─────────────┘
```
Sub-proyek 1a dan 1c bisa dikerjakan paralel. 1b menggabungkan output keduanya.

---

## 3. Sub-Project 1a: BMKG Rainfall Integration

### 3.1 Data Source
- **API:** `https://data.bmkg.go.id` (JSON format)
- **Coverage:** Stasiun BMKG di NTB (Sumbawa, Lombok, Bima, Dompu)
- **Variables:** Curah hujan (mm), kelembaban (%), suhu (°C), kecepatan angin (m/s)
- **Temporal:** Daily/hourly, real-time
- **Authentication:** API key via environment variable `BMKG_API_KEY`

### 3.2 Database Schema
```sql
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
```

### 3.3 ETL Script: `scripts/bmkg_sync.py`
```
Input:  BMKG API (JSON)
Output: bmkg_rainfall table (PostGIS)
Process:
  1. Fetch daftar stasiun NTB dari API BMKG
  2. Untuk setiap stasiun, fetch data curah hujan terbaru
  3. Parse JSON → dict
  4. INSERT ... ON CONFLICT (station_id, date) DO UPDATE
  5. Log jumlah records yang di-update
```

### 3.4 API Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/bmkg/rainfall` | GET | Data hujan terbaru, filter: `?station_id=`, `?start_date=`, `?end_date=` |
| `/api/bmkg/rainfall/timeseries` | GET | Time series per stasiun: `?station_id=X&months=12` |
| `/api/bmkg/summary` | GET | Ringkasan anomali curah hujan NTB vs baseline |
| `/api/bmkg/stations` | GET | Daftar stasiun BMKG di NTB |

### 3.5 Frontend Changes
- **Topbar:** Tambah badge "BMKG"
- **Detail Panel:** Tambah chart curah hujan BMKG (bar chart,jejer dengan NDVI chart)
- **Sidebar Science Section:** Update deskripsi "GWS = TWS - SMS | BMKG rainfall"

### 3.6 Environment Variables
```
BMKG_API_KEY=<key from BMKG>
```

---

## 4. Sub-Project 1b: Multi-sensor Data Fusion Pipeline

### 4.1 Data Sources to Fuse
| Source | Table | Variable | Resolution |
|--------|-------|----------|------------|
| NASA GRACE | `grace_tws` | TWS anomaly (cm EWH) | 0.5°, monthly |
| NASA GLDAS | `gldas_sms` | SMS anomaly (cm EWH) | 0.25°, monthly |
| CHIRPS | `chirps_precip` | Precipitation (mm) | 0.05°, monthly |
| BMKG | `bmkg_rainfall` | Precipitation (mm) | station, daily |
| Sentinel-2 | `sentinel2_ndvi` | NDVI | 10m, irregular |
| Sentinel-1 | `sar_subsidence` | Displacement (mm) | 10m, irregular |

### 4.2 Database Schema
```sql
CREATE TABLE IF NOT EXISTS unified_monitoring (
    id                  SERIAL PRIMARY KEY,
    period_date         DATE NOT NULL,
    lat                 FLOAT NOT NULL,
    lon                 FLOAT NOT NULL,
    -- GRACE TWS
    tws_anomaly         FLOAT,
    tws_uncertainty     FLOAT,
    -- GLDAS SMS
    sms_anomaly         FLOAT,
    -- Derived GWS
    gws_anomaly         FLOAT,
    -- CHIRPS Precipitation
    chirps_precip_mm    FLOAT,
    chirps_anomaly      FLOAT,
    -- BMKG (nearest station)
    bmkg_precip_mm      FLOAT,
    bmkg_station_id     VARCHAR(20),
    bmkg_distance_km    FLOAT,
    -- Sentinel-2 NDVI (nearest pixel)
    ndvi                FLOAT,
    ndvi_location       VARCHAR(100),
    ndvi_distance_km    FLOAT,
    -- Sentinel-1 SAR
    sar_subsidence_mm   FLOAT,
    sar_rate_mm_year    FLOAT,
    -- Derived Indicators
    drought_index       FLOAT,  -- composite: GWS + rainfall + NDVI
    risk_level          VARCHAR(20),  -- normal, waspada, kritis
    -- Metadata
    grid_resolution     VARCHAR(20) DEFAULT '0.5deg',
    data_completeness   FLOAT,  -- 0.0 to 1.0
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(period_date, lat, lon)
);

CREATE INDEX IF NOT EXISTS idx_unified_geom 
    ON unified_monitoring USING GIST(
        ST_SetSRID(ST_MakePoint(lon, lat), 4326)
    );
CREATE INDEX IF NOT EXISTS idx_unified_date ON unified_monitoring(period_date);
CREATE INDEX IF NOT EXISTS idx_unified_risk ON unified_monitoring(risk_level);
```

### 4.3 ETL Script: `scripts/sync_unified.py`
```
Input:  Semua tabel sumber (grace_tws, gldas_sms, chirps_precip, 
        bmkg_rainfall, sentinel2_ndvi, sar_subsidence)
Output: unified_monitoring table
Process:
  1. Untuk setiap grid point GRACE (lat, lon, period_date):
     a. Ambil tws_anomaly dari grace_tws
     b. Ambil sms_anomaly dari gldas_sms (spatial + temporal match)
     c. Hitung gws_anomaly = tws_anomaly - COALESCE(sms_anomaly, 0)
     d. Ambil chirps_precip dari chirps_precip (nearest grid)
     e. Hitung chirps_anomaly = chirps - baseline_2004_2009
     f. Cari nearest BMKG station → ambil precip_mm
     g. Cari nearest Sentinel-2 NDVI pixel
     h. Cari nearest SAR subsidence point (jika ada)
     i. Hitung drought_index (weighted: 40% GWS + 30% rainfall + 30% NDVI)
     j. Hitung risk_level dari drought_index
     k. Hitung data_completeness (berapa sensor yang tersedia / total)
  2. INSERT ... ON CONFLICT (period_date, lat, lon) DO UPDATE
  
  Fungsi helper:
  - nearest_bmkg(lat, lon, date) → (station_id, precip, distance)
  - nearest_ndvi(lat, lon) → (location, ndvi, distance)
  - nearest_sar(lat, lon, date) → (displacement, rate)
  - calc_drought_index(gws, rain_anomaly, ndvi) → float
  - calc_risk_level(drought_index) → str
```

### 4.4 API Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/fusion/monitoring` | GET | Data terpadu, filter: `?lat=&lon=&start_date=&end_date=&risk_level=` |
| `/api/fusion/timeseries` | GET | Time series multi-sensor: `?lat=X&lon=Y&months=24` |
| `/api/fusion/summary` | GET | Ringkasan semua sensor NTB |
| `/api/fusion/correlation` | GET | Korelasi antar sensor: `?sensor1=gws&sensor2=ndvi` |

### 4.5 Drought Index Formula
```python
def normalize(value, min_val, max_val):
    """Linear normalization to 0..1 range. Clamps to bounds."""
    if value is None:
        return 0.5  # default to middle if missing
    return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))

drought_index = (
    0.4 * normalize(gws_anomaly, -5, 5) +       # GWS: -5..+5 cm → 0..1
    0.3 * normalize(chirps_anomaly, -200, 200) +  # Rain: -200..+200 mm → 0..1
    0.3 * normalize(ndvi, 0, 0.8)                 # NDVI: 0..0.8 → 0..1
)

risk_level:
  drought_index >= 0.6 → "normal"       # surplus/healthy
  drought_index >= 0.4 → "waspada"      # mild deficit
  drought_index >= 0.2 → "kritis"       # significant deficit
  drought_index < 0.2  → "sangat_kritis" # severe deficit
```

### 4.6 Frontend Changes
- **Detail Panel:** New "Multi-Sensor Overview" section — grid of all sensor values
- **Chart:** Overlay chart: GWS bar + rainfall line + NDVI line in single canvas
- **Summary API:** Update sidebar stats to include data_completeness

---

## 5. Sub-Project 1c: Sentinel-1 SAR Subsidence Detection

### 5.1 Data Source
- **Platform:** Google Earth Engine (GEE)
- **Dataset:** `COPERNICUS/S1_GRD` (Sentinel-1 Ground Range Detected)
- **Mode:** Interferometric Wide Swath (IW), VV+VH polarization
- **Method:** Stack-based displacement estimation
- **Coverage:** NTB region (bbox: 115.5,-9.5,120.0,-7.5)
- **Temporal:** 2014-present (Sentinel-1A), 2016-present (Sentinel-1B)

### 5.2 Processing Pipeline (GEE)
```python
# scripts/sar_to_postgis.py
import ee

ee.Initialize()

# Parameters
region = ee.Geometry.Rectangle([115.5, -9.5, 120.0, -7.5])
start_date = '2020-01-01'
end_date = '2026-06-01'

# Load Sentinel-1 collection
s1 = ee.ImageCollection('COPERNICUS/S1_GRD') \
    .filterBounds(region) \
    .filterDate(start_date, end_date) \
    .filter(ee.Filter.eq('instrumentMode', 'IW')) \
    .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
    .select('VV')

# Stack-based displacement
# Calculate temporal stack mean and trend
def calc_displacement(image):
    date = ee.Date(image.get('system:time_start'))
    years = date.difference(ee.Date(start_date), 'year')
    return image.addBands(
        ee.Image(years).rename('time').float()
    )

stack = s1.map(calc_displacement)
linear_fit = stack.select(['time', 'VV']).reduce(ee.Reducer.linearFit())
# slope = displacement rate per year

# Sample at well locations and export
```

### 5.3 Database Schema
```sql
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
```

### 5.4 API Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sar/subsidence` | GET | Data subsidence, filter: `?kabupaten=&min_rate=` |
| `/api/sar/subsidence/timeseries` | GET | Time series displacement: `?location=X` |
| `/api/sar/summary` | GET | Ringkasan area subsidence kritis |
| `/api/sar/geojson` | GET | GeoJSON layer untuk MapLibre |

### 5.5 Frontend Changes
- **Map Layer:** New layer "SAR Subsidence" — circle markers colored by rate (green/yellow/red)
- **Detail Panel:** "Subsidence" metric section for wells near SAR data
- **Topbar:** Badge "SAR"

---

## 6. Implementation Order

```
1. BMKG Rainfall Integration (scripts/bmkg_sync.py + router + frontend)
   ↓
2. SAR Subsidence Detection (scripts/sar_to_postgis.py + router + frontend)
   ↓
3. Multi-sensor Pipeline (scripts/sync_unified.py + router + frontend)
   ↓
4. Integration Testing (docker compose up + smoke test)
```

Step 1 dan 2 bisa paralel. Step 3 butuh output dari 1 dan 2.

---

## 7. Testing Strategy

### Unit Tests
- `tests/test_bmkg.py` — test BMKG API parsing, insert logic
- `tests/test_fusion.py` — test drought_index calculation, risk_level logic
- `tests/test_sar.py` — test SAR data parsing

### Integration Tests
- Test full pipeline: GRACE + GLDAS → unified_monitoring
- Test BMKG + nearest_station spatial join
- Test API endpoints return correct GeoJSON structure

### Smoke Test
- Extend `scripts/smoke_test_live.sh` dengan endpoint baru:
  - `/api/bmkg/summary`
  - `/api/sar/summary`
  - `/api/fusion/summary`

---

## 8. Environment Variables (New)

```env
# BMKG API
BMKG_API_KEY=<from data.bmkg.go.id>

# Google Earth Engine
GEE_SERVICE_ACCOUNT=<email>
GEE_PRIVATE_KEY=<path or inline>

# Existing
DATABASE_URL=postgresql://rizki:ntb_env_2024@db:5432/ntb_groundwater
KIMI_API_KEY=<existing>
```

---

## 9. Dependencies (requirements.txt additions)

```
# BMKG
requests>=2.31.0

# Google Earth Engine
earthengine-api>=0.1.370

# Already existing
fastapi, asyncpg, numpy, xarray, netCDF4
```

---

## 10. Risk & Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| BMKG API format berubah | Data pipeline break | Abstract parser, add tests |
| BMKG API rate limit | Sync gagal | Cache, exponential backoff |
| GEE quota exceeded | SAR data tidak tersedia | Export batch, cache results |
| GRACE temporal gap (2017-2018) | Missing data di unified | Interpolasi atau flag as missing |
| Spatial mismatch antar resolusi | Nearest-neighbor tidak akurat | Weighted interpolation untuk grid halus |

---

## 11. Out of Scope (Phase 1)

- ML downscaling GRACE → Phase 2a
- Anthropogenic signal detection → Phase 2b
- Drought early warning system → Phase 3
- Mobile responsive UI → independent
- TROPOMI air quality → independent
