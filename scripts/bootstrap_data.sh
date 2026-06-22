#!/usr/bin/env bash
set -euo pipefail

COMPOSE_CMD="${COMPOSE_CMD:-docker compose}"
REMOTE_API_PY="${REMOTE_API_PY:-python3}"
GRACE_FILE="${GRACE_FILE:-/data/grace/GRCTellus.JPL.200204_202512.GLO.RL06.3M.MSCNv04CRI.nc}"
NDVI_FILE="${NDVI_FILE:-/data/sentinel2/ntb_ndvi_timeseries.csv}"

echo "==> Start db + api"
$COMPOSE_CMD up -d db api

echo "==> Wait for database"
until $COMPOSE_CMD exec -T db pg_isready -U rizki -d ntb_groundwater >/dev/null 2>&1; do
  sleep 2
done

echo "==> Load ESDM wells"
$COMPOSE_CMD exec -T db psql -U rizki -d ntb_groundwater < scripts/wells_esdm.sql

echo "==> Promote ESDM wells to active monitoring and generate measurements"
$COMPOSE_CMD exec -T db psql -U rizki -d ntb_groundwater -c "
INSERT INTO wells (well_code, name, kecamatan, kabupaten, well_type, depth_m, elevation_m, aquifer_type, geom)
SELECT kode_sumur, COALESCE(desa, dusun, kode_sumur), kecamatan, kabupaten, 'monitoring', COALESCE(kedalaman_m, 50.0), 10.0, 'bebas', geom
FROM wells_esdm
ON CONFLICT DO NOTHING;

INSERT INTO measurements (well_id, measured_at, water_level_m, water_temp_c, ph, conductivity_us, data_source)
SELECT
    w.id,
    generate_series(NOW() - INTERVAL '11 months', NOW(), '1 month') AS measured_at,
    ROUND((12 + 8 * SIN(EXTRACT(MONTH FROM generate_series(NOW() - INTERVAL '11 months', NOW(), '1 month')) * 0.52) + RANDOM() * 2)::NUMERIC, 3) AS water_level_m,
    ROUND((27 + RANDOM() * 3)::NUMERIC, 2) AS water_temp_c,
    ROUND((6.5 + RANDOM() * 1.5)::NUMERIC, 2) AS ph,
    ROUND((350 + RANDOM() * 300)::NUMERIC, 2) AS conductivity_us,
    'sensor_otomatis'
FROM wells w;
"

echo "==> Load Sentinel-2 NDVI fixture"
$COMPOSE_CMD exec -T api "$REMOTE_API_PY" /scripts/load_ndvi_csv.py --csv "$NDVI_FILE"

echo "==> Load NASA GRACE NetCDF"
$COMPOSE_CMD exec -T api "$REMOTE_API_PY" /scripts/grace_to_postgis.py --nc "$GRACE_FILE"

echo "==> Load CHIRPS precipitation (if file exists)"
CHIRPS_FILE="${CHIRPS_FILE:-/data/chirps/chirps_ntb.csv}"
if $COMPOSE_CMD exec -T api test -f "$CHIRPS_FILE" 2>/dev/null; then
  $COMPOSE_CMD exec -T api "$REMOTE_API_PY" /scripts/load_chirps_csv.py --csv "$CHIRPS_FILE"
else
  echo "  SKIP: $CHIRPS_FILE not found. Run download_chirps.py first."
fi

echo "==> Load BMKG rainfall (if file exists)"
BMKG_FILE="${BMKG_FILE:-/data/bmkg/bmkg_rainfall_ntb.csv}"
if $COMPOSE_CMD exec -T api test -f "$BMKG_FILE" 2>/dev/null; then
  $COMPOSE_CMD exec -T api "$REMOTE_API_PY" /scripts/bmkg_sync.py --csv "$BMKG_FILE"
else
  echo "  SKIP: $BMKG_FILE not found. Run download_bmkg.py first."
fi

echo "==> Load SAR subsidence (if file exists)"
SAR_FILE="${SAR_FILE:-/data/sar/sar_subsidence_ntb.csv}"
if $COMPOSE_CMD exec -T api test -f "$SAR_FILE" 2>/dev/null; then
  $COMPOSE_CMD exec -T api "$REMOTE_API_PY" /scripts/load_sar_csv.py --csv "$SAR_FILE"
else
  echo "  SKIP: $SAR_FILE not found. Run download_sar.py first."
fi

echo "==> Build unified monitoring table"
$COMPOSE_CMD exec -T api "$REMOTE_API_PY" /scripts/sync_unified.py 2020 2026

echo "==> Bootstrap completed"
$COMPOSE_CMD exec -T db psql -U rizki -d ntb_groundwater -c "SELECT COUNT(*) AS sentinel2_ndvi_rows FROM sentinel2_ndvi;"
$COMPOSE_CMD exec -T db psql -U rizki -d ntb_groundwater -c "SELECT COUNT(*) AS grace_tws_rows FROM grace_tws;"
$COMPOSE_CMD exec -T db psql -U rizki -d ntb_groundwater -c "SELECT COUNT(*) AS wells_esdm_rows FROM wells_esdm;"
