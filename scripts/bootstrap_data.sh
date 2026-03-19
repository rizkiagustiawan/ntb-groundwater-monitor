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

echo "==> Load Sentinel-2 NDVI fixture"
$COMPOSE_CMD exec -T api "$REMOTE_API_PY" /scripts/load_ndvi_csv.py --csv "$NDVI_FILE"

echo "==> Load NASA GRACE NetCDF"
$COMPOSE_CMD exec -T api "$REMOTE_API_PY" /scripts/grace_to_postgis.py --nc "$GRACE_FILE"

echo "==> Bootstrap completed"
$COMPOSE_CMD exec -T db psql -U rizki -d ntb_groundwater -c "SELECT COUNT(*) AS sentinel2_ndvi_rows FROM sentinel2_ndvi;"
$COMPOSE_CMD exec -T db psql -U rizki -d ntb_groundwater -c "SELECT COUNT(*) AS grace_tws_rows FROM grace_tws;"
$COMPOSE_CMD exec -T db psql -U rizki -d ntb_groundwater -c "SELECT COUNT(*) AS wells_esdm_rows FROM wells_esdm;"
