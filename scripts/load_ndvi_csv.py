#!/usr/bin/env python3
import argparse
import asyncio
import csv
import os
from datetime import date
from pathlib import Path

import asyncpg


DEFAULT_DB_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@db:5432/ntb_groundwater")
DEFAULT_CSV_PATH = os.getenv("NDVI_CSV_PATH", "/data/sentinel2/ntb_ndvi_timeseries.csv")


CREATE_SQL = """
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
"""


MIGRATE_SQL = """
ALTER TABLE sentinel2_ndvi ADD COLUMN IF NOT EXISTS kabupaten VARCHAR(100);
ALTER TABLE sentinel2_ndvi ADD COLUMN IF NOT EXISTS lat NUMERIC(9,5);
ALTER TABLE sentinel2_ndvi ADD COLUMN IF NOT EXISTS lon NUMERIC(9,5);
ALTER TABLE sentinel2_ndvi ADD COLUMN IF NOT EXISTS ndwi NUMERIC(8,4);
ALTER TABLE sentinel2_ndvi ADD COLUMN IF NOT EXISTS vegetation_status VARCHAR(30);
ALTER TABLE sentinel2_ndvi ADD COLUMN IF NOT EXISTS data_source VARCHAR(50) DEFAULT 'sentinel2_csv';
ALTER TABLE sentinel2_ndvi ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326);
ALTER TABLE sentinel2_ndvi ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();
"""


UPSERT_SQL = """
INSERT INTO sentinel2_ndvi (
    location, kabupaten, lat, lon, period_date,
    ndvi, ndwi, vegetation_status, data_source, geom
) VALUES (
    $1, $2, $3::numeric, $4::numeric, $5,
    $6::numeric, $7::numeric, $8, $9,
    ST_SetSRID(ST_MakePoint($4::double precision, $3::double precision), 4326)
)
ON CONFLICT (location, period_date) DO UPDATE SET
    kabupaten = EXCLUDED.kabupaten,
    lat = EXCLUDED.lat,
    lon = EXCLUDED.lon,
    ndvi = EXCLUDED.ndvi,
    ndwi = EXCLUDED.ndwi,
    vegetation_status = EXCLUDED.vegetation_status,
    data_source = EXCLUDED.data_source,
    geom = EXCLUDED.geom
"""


def parse_args():
    parser = argparse.ArgumentParser(description="Load Sentinel-2 NDVI CSV into PostGIS.")
    parser.add_argument("--csv", dest="csv_path", default=DEFAULT_CSV_PATH, help="Path to NDVI CSV fixture")
    parser.add_argument("--db-url", dest="db_url", default=DEFAULT_DB_URL, help="PostgreSQL connection URL")
    parser.add_argument("--data-source", dest="data_source", default="sentinel2_csv_fixture", help="data_source value")
    return parser.parse_args()


def parse_float(raw_value):
    if raw_value in ("", None):
        return None
    return float(raw_value)


def load_rows(csv_path: Path, data_source: str):
    rows = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows.append((
                row["location"].strip(),
                row["kabupaten"].strip(),
                float(row["lat"]),
                float(row["lon"]),
                date.fromisoformat(row["period_date"]),
                float(row["ndvi"]),
                parse_float(row.get("ndwi")),
                (row.get("vegetation_status") or "").strip() or None,
                data_source
            ))
    return rows


async def main():
    args = parse_args()
    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    rows = load_rows(csv_path, args.data_source)
    conn = await asyncpg.connect(args.db_url)
    try:
        await conn.execute(CREATE_SQL)
        await conn.execute(MIGRATE_SQL)
        await conn.executemany(UPSERT_SQL, rows)
        total = await conn.fetchval("SELECT COUNT(*) FROM sentinel2_ndvi")
        print(f"Loaded {len(rows)} NDVI rows from {csv_path}")
        print(f"sentinel2_ndvi total rows: {total}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
