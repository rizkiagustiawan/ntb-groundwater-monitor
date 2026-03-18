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
-- TABEL: Cekungan Air Tanah (CAT)
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

-- ============================================================
-- SEED DATA: 32 Sumur Pantau NTB (representatif per kecamatan)
-- Koordinat valid secara geografis - Pulau Sumbawa
-- Data dummy untuk development - ganti dengan data ESDM riil
-- ============================================================
INSERT INTO wells (well_code, name, kecamatan, kabupaten, well_type, depth_m, elevation_m, aquifer_type, geom) VALUES
-- Kabupaten Sumbawa
('SMB-001','Sumur Pantau Sumbawa Kota','Sumbawa','Kab. Sumbawa','monitoring',45.0,12.0,'bebas',ST_SetSRID(ST_MakePoint(117.4174,-8.4932),4326)),
('SMB-002','Sumur Pantau Moyo Hilir','Moyo Hilir','Kab. Sumbawa','monitoring',38.5,18.5,'bebas',ST_SetSRID(ST_MakePoint(117.6012,-8.5521),4326)),
('SMB-003','Sumur Pantau Unter Iwes','Unter Iwes','Kab. Sumbawa','monitoring',52.0,22.0,'tertekan',ST_SetSRID(ST_MakePoint(117.3845,-8.4234),4326)),
('SMB-004','Sumur Pantau Batu Lanteh','Batu Lanteh','Kab. Sumbawa','monitoring',35.0,145.0,'bebas',ST_SetSRID(ST_MakePoint(117.3124,-8.6712),4326)),
('SMB-005','Sumur Pantau Empang','Empang','Kab. Sumbawa','monitoring',42.0,8.0,'bebas',ST_SetSRID(ST_MakePoint(117.8934,-8.8234),4326)),
('SMB-006','Sumur Pantau Tarano','Tarano','Kab. Sumbawa','monitoring',60.0,15.0,'tertekan',ST_SetSRID(ST_MakePoint(118.0123,-8.9012),4326)),
('SMB-007','Sumur Pantau Lunyuk','Lunyuk','Kab. Sumbawa','monitoring',55.0,25.0,'bebas',ST_SetSRID(ST_MakePoint(117.1234,-9.0145),4326)),
('SMB-008','Sumur Pantau Alas','Alas','Kab. Sumbawa','monitoring',40.0,5.0,'bebas',ST_SetSRID(ST_MakePoint(116.8923,-8.6534),4326)),
-- Kabupaten Sumbawa Barat
('KSB-001','Sumur Pantau Taliwang','Taliwang','Kab. Sumbawa Barat','monitoring',38.0,10.0,'bebas',ST_SetSRID(ST_MakePoint(116.8523,-8.7234),4326)),
('KSB-002','Sumur Pantau Sekongkang','Sekongkang','Kab. Sumbawa Barat','monitoring',65.0,120.0,'tertekan',ST_SetSRID(ST_MakePoint(116.7012,-8.9823),4326)),
('KSB-003','Sumur Pantau Maluk','Maluk','Kab. Sumbawa Barat','monitoring',45.0,35.0,'bebas',ST_SetSRID(ST_MakePoint(116.7534,-8.8923),4326)),
-- Kabupaten Dompu
('DMP-001','Sumur Pantau Dompu Kota','Dompu','Kab. Dompu','monitoring',50.0,20.0,'bebas',ST_SetSRID(ST_MakePoint(118.4623,-8.5312),4326)),
('DMP-002','Sumur Pantau Hu u','Hu u','Kab. Dompu','monitoring',35.0,8.0,'bebas',ST_SetSRID(ST_MakePoint(118.2834,-8.8923),4326)),
('DMP-003','Sumur Pantau Kilo','Kilo','Kab. Dompu','monitoring',42.0,15.0,'bebas',ST_SetSRID(ST_MakePoint(118.0234,-8.4512),4326)),
-- Kabupaten Bima
('BMA-001','Sumur Pantau Raba','Raba','Kab. Bima','monitoring',55.0,12.0,'tertekan',ST_SetSRID(ST_MakePoint(118.7234,-8.4823),4326)),
('BMA-002','Sumur Pantau Woha','Woha','Kab. Bima','monitoring',48.0,18.0,'bebas',ST_SetSRID(ST_MakePoint(118.6123,-8.6234),4326)),
('BMA-003','Sumur Pantau Bolo','Bolo','Kab. Bima','monitoring',40.0,10.0,'bebas',ST_SetSRID(ST_MakePoint(118.5234,-8.7012),4326)),
('BMA-004','Sumur Pantau Sape','Sape','Kab. Bima','monitoring',52.0,8.0,'tertekan',ST_SetSRID(ST_MakePoint(119.0123,-8.5834),4326)),
('BMA-005','Sumur Pantau Tambora','Tambora','Kab. Bima','monitoring',30.0,280.0,'bebas',ST_SetSRID(ST_MakePoint(118.0034,-8.2912),4326)),
-- Kota Bima
('BIM-001','Sumur Pantau Rasanae Barat','Rasanae Barat','Kota Bima','monitoring',45.0,6.0,'bebas',ST_SetSRID(ST_MakePoint(118.7134,-8.4612),4326)),
('BIM-002','Sumur Pantau Mpunda','Mpunda','Kota Bima','monitoring',50.0,8.0,'tertekan',ST_SetSRID(ST_MakePoint(118.7334,-8.4412),4326)),
-- Kabupaten Lombok Utara (untuk completeness)
('LUT-001','Sumur Pantau Tanjung','Tanjung','Kab. Lombok Utara','monitoring',35.0,15.0,'bebas',ST_SetSRID(ST_MakePoint(116.1423,-8.3512),4326)),
('LUT-002','Sumur Pantau Gangga','Gangga','Kab. Lombok Utara','monitoring',42.0,25.0,'bebas',ST_SetSRID(ST_MakePoint(116.2134,-8.3923),4326));

-- ============================================================
-- SEED: Data Pengukuran Dummy (12 bulan terakhir per sumur sampel)
-- ============================================================
INSERT INTO measurements (well_id, measured_at, water_level_m, water_temp_c, ph, conductivity_us, data_source)
SELECT
    w.id,
    generate_series(NOW() - INTERVAL '11 months', NOW(), '1 month') AS measured_at,
    -- simulasi muka air dengan variasi musiman (kering Juli-Oktober)
    ROUND((12 + 8 * SIN(EXTRACT(MONTH FROM generate_series(NOW() - INTERVAL '11 months', NOW(), '1 month')) * 0.52) + RANDOM() * 2)::NUMERIC, 3) AS water_level_m,
    ROUND((27 + RANDOM() * 3)::NUMERIC, 2) AS water_temp_c,
    ROUND((6.5 + RANDOM() * 1.5)::NUMERIC, 2) AS ph,
    ROUND((350 + RANDOM() * 300)::NUMERIC, 2) AS conductivity_us,
    'sensor_otomatis'
FROM wells w WHERE w.kabupaten IN ('Kab. Sumbawa','Kab. Dompu','Kab. Bima');

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
        WHEN m.water_level_m < w.depth_m * 0.3 THEN 'normal'
        WHEN m.water_level_m < w.depth_m * 0.6 THEN 'waspada'
        WHEN m.water_level_m < w.depth_m * 0.8 THEN 'kritis'
        ELSE 'sangat_kritis'
    END AS status_level,
    ST_AsGeoJSON(w.geom)::json AS geometry
FROM wells w
LEFT JOIN LATERAL (
    SELECT * FROM measurements
    WHERE well_id = w.id
    ORDER BY measured_at DESC LIMIT 1
) m ON TRUE;
