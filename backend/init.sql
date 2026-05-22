-- NTB Groundwater Monitoring Database
-- Landasan hukum: PP No. 43 Tahun 2008 tentang Air Tanah
-- Perpres No. 33 Tahun 2018 (Cekungan Air Tanah)

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_topology;

-- ============================================================
-- TABEL UTAMA: Sumur Pantau
-- Referensi: SNI 6989.58:2008 - Metode pengambilan contoh air tanah
-- ============================================================
CREATE TABLE IF NOT EXISTS wells (
    id            SERIAL PRIMARY KEY,
    well_code     VARCHAR(20) UNIQUE NOT NULL,
    name          VARCHAR(100) NOT NULL,
    kecamatan     VARCHAR(100) NOT NULL,
    kabupaten     VARCHAR(100) NOT NULL,
    well_type     VARCHAR(30) DEFAULT 'monitoring',  -- monitoring, production, observation
    depth_m       NUMERIC(8,2),
    elevation_m   NUMERIC(8,2),
    aquifer_type  VARCHAR(30),  -- bebas, tertekan, semi_tertekan
    status        VARCHAR(20) DEFAULT 'aktif',
    geom          GEOMETRY(Point, 4326) NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_wells_geom ON wells USING GIST(geom);
CREATE INDEX idx_wells_kabupaten ON wells(kabupaten);

-- ============================================================
-- TABEL: Pengukuran Muka Air Tanah
-- Standar: PP 43/2008 Pasal 15 - kewajiban monitoring
-- ============================================================
CREATE TABLE IF NOT EXISTS measurements (
    id              SERIAL PRIMARY KEY,
    well_id         INTEGER REFERENCES wells(id) ON DELETE CASCADE,
    measured_at     TIMESTAMPTZ NOT NULL,
    water_level_m   NUMERIC(8,3),  -- kedalaman muka air dari permukaan (m)
    water_temp_c    NUMERIC(5,2),
    ph              NUMERIC(4,2),
    conductivity_us NUMERIC(8,2),  -- µS/cm - konduktivitas listrik
    notes           TEXT,
    data_source     VARCHAR(50) DEFAULT 'manual',  -- manual, sensor_otomatis, satelit
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_measurements_well_time ON measurements(well_id, measured_at DESC);

-- ============================================================
-- TABEL: Data GRACE TWS (Terrestrial Water Storage)
-- Sumber: NASA GRACE/GRACE-FO RL06 Mascon
-- Unit: cm equivalent water height (EWH)
-- ============================================================
CREATE TABLE IF NOT EXISTS grace_tws (
    id          SERIAL PRIMARY KEY,
    period_date DATE NOT NULL,          -- bulan data (hari pertama bulan)
    lat         NUMERIC(8,5) NOT NULL,
    lon         NUMERIC(8,5) NOT NULL,
    tws_anomaly NUMERIC(10,4),          -- anomali TWS dalam cm EWH
    uncertainty NUMERIC(8,4),           -- ketidakpastian pengukuran
    geom        GEOMETRY(Point, 4326),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_grace_geom    ON grace_tws USING GIST(geom);
CREATE INDEX idx_grace_period  ON grace_tws(period_date);
CREATE UNIQUE INDEX idx_grace_unique ON grace_tws(period_date, lat, lon);

-- ============================================================
-- TABEL: Data GLDAS Noah 2.1 Soil Moisture (SMS)
-- Sumber: NASA GLDAS Noah 2.1 Root Zone Soil Moisture
-- Unit: cm EWH anomaly relative to 2004-2009
-- ============================================================
CREATE TABLE IF NOT EXISTS gldas_sms (
    id          SERIAL PRIMARY KEY,
    lat         FLOAT NOT NULL,
    lon         FLOAT NOT NULL,
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    sms_cm_ewh  FLOAT,          -- total soil moisture in cm EWH
    sms_anomaly FLOAT,          -- deviation from 2004-2009 baseline
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(lat, lon, year, month)
);

CREATE INDEX IF NOT EXISTS idx_gldas_coords ON gldas_sms(lat, lon);
CREATE INDEX IF NOT EXISTS idx_gldas_date ON gldas_sms(year, month);

-- ============================================================
-- TABEL: Data CHIRPS Precipitation
-- Sumber: UCSB-CHG/CHIRPS
-- Unit: mm
-- ============================================================
CREATE TABLE IF NOT EXISTS chirps_precip (
    id         SERIAL PRIMARY KEY,
    lat        FLOAT NOT NULL,
    lon        FLOAT NOT NULL,
    year       INTEGER NOT NULL,
    month      INTEGER NOT NULL,
    precip_mm  FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(lat, lon, year, month)
);

CREATE INDEX IF NOT EXISTS idx_chirps_coords ON chirps_precip(lat, lon);
CREATE INDEX IF NOT EXISTS idx_chirps_date ON chirps_precip(year, month);

-- ============================================================
-- TABEL: Sentinel-2 NDVI/NDWI per lokasi
-- Sumber: Google Earth Engine, COPERNICUS/S2_SR_HARMONIZED
-- ============================================================
CREATE TABLE IF NOT EXISTS sentinel2_ndvi (
    id                SERIAL PRIMARY KEY,
    location          VARCHAR(100) NOT NULL,
    kabupaten         VARCHAR(100) NOT NULL,
    lat               NUMERIC(9,5) NOT NULL,
    lon               NUMERIC(9,5) NOT NULL,
    period_date       DATE NOT NULL,
    ndvi              NUMERIC(8,4) NOT NULL,
    ndwi              NUMERIC(8,4),
    vegetation_status VARCHAR(30),
    data_source       VARCHAR(50) DEFAULT 'sentinel2_csv',
    geom              GEOMETRY(Point, 4326),
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sentinel2_ndvi_geom ON sentinel2_ndvi USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_sentinel2_ndvi_period ON sentinel2_ndvi(period_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sentinel2_ndvi_unique ON sentinel2_ndvi(location, period_date);

-- ============================================================
-- Cekungan Air Tanah (CAT)
-- Referensi: Perpres No. 33 Tahun 2018 - Daftar CAT Indonesia
-- ============================================================
CREATE TABLE IF NOT EXISTS cat_zones (
    id          SERIAL PRIMARY KEY,
    cat_name    VARCHAR(100) NOT NULL,
    cat_code    VARCHAR(20),
    province    VARCHAR(50) DEFAULT 'Nusa Tenggara Barat',
    area_km2    NUMERIC(10,2),
    status      VARCHAR(30) DEFAULT 'lintas_kabupaten',
    geom        GEOMETRY(MultiPolygon, 4326),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_cat_geom ON cat_zones USING GIST(geom);

-- CAT Sumbawa (simplified boundary)
INSERT INTO cat_zones (cat_name, cat_code, area_km2, geom) VALUES
('CAT Sumbawa', 'CAT-NTB-01', 1850.0,
 ST_SetSRID(ST_GeomFromText('MULTIPOLYGON(((116.7 -8.3, 118.1 -8.3, 118.1 -9.1, 116.7 -9.1, 116.7 -8.3)))'),4326));

-- View: status terkini per sumur (untuk API)
CREATE OR REPLACE VIEW well_latest_status AS
SELECT
    w.id, w.well_code, w.name, w.kecamatan, w.kabupaten,
    w.well_type, w.depth_m, w.aquifer_type, w.status,
    m.water_level_m, m.measured_at, m.ph, m.conductivity_us,
    -- klasifikasi status berdasarkan persentase kedalaman
    CASE
        WHEN m.water_level_m IS NULL THEN 'tidak_ada_data'
        WHEN m.water_level_m < COALESCE(w.depth_m, 50.0) * 0.3 THEN 'normal'
        WHEN m.water_level_m < COALESCE(w.depth_m, 50.0) * 0.6 THEN 'waspada'
        WHEN m.water_level_m < COALESCE(w.depth_m, 50.0) * 0.8 THEN 'kritis'
        ELSE 'sangat_kritis'
    END AS status_level,
    ST_AsGeoJSON(w.geom)::json AS geometry
FROM wells w
LEFT JOIN LATERAL (
    SELECT * FROM measurements
    WHERE well_id = w.id
    ORDER BY measured_at DESC LIMIT 1
) m ON TRUE;

