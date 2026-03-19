#!/usr/bin/env python3
import argparse
import asyncio
import os
from datetime import date
from pathlib import Path

import asyncpg
import numpy as np
import xarray as xr


DEFAULT_DB_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@db:5432/ntb_groundwater")
DEFAULT_NC_PATH = os.getenv(
    "GRACE_NC_PATH",
    "/data/grace/GRCTellus.JPL.200204_202512.GLO.RL06.3M.MSCNv04CRI.nc"
)

NTB_BOUNDS = {
    "lat_min": -9.25,
    "lat_max": -7.75,
    "lon_min": 115.75,
    "lon_max": 119.25,
}


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS grace_tws (
    id          SERIAL PRIMARY KEY,
    period_date DATE NOT NULL,
    lat         NUMERIC(8,5) NOT NULL,
    lon         NUMERIC(8,5) NOT NULL,
    tws_anomaly NUMERIC(10,4),
    uncertainty NUMERIC(8,4),
    geom        GEOMETRY(Point, 4326),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_grace_geom ON grace_tws USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_grace_period ON grace_tws(period_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_grace_unique ON grace_tws(period_date, lat, lon);
"""


MIGRATE_SQL = """
ALTER TABLE grace_tws ADD COLUMN IF NOT EXISTS uncertainty NUMERIC(8,4);
ALTER TABLE grace_tws ADD COLUMN IF NOT EXISTS geom GEOMETRY(Point, 4326);
ALTER TABLE grace_tws ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();
"""


UPSERT_SQL = """
INSERT INTO grace_tws (
    period_date, lat, lon, tws_anomaly, uncertainty, geom
) VALUES (
    $1, $2::numeric, $3::numeric, $4::numeric, $5::numeric,
    ST_SetSRID(ST_MakePoint($3::double precision, $2::double precision), 4326)
)
ON CONFLICT (period_date, lat, lon) DO UPDATE SET
    tws_anomaly = EXCLUDED.tws_anomaly,
    uncertainty = EXCLUDED.uncertainty,
    geom = EXCLUDED.geom
"""


def parse_args():
    parser = argparse.ArgumentParser(description="Load NTB GRACE NetCDF data into PostGIS.")
    parser.add_argument("--nc", dest="nc_path", default=DEFAULT_NC_PATH, help="Path to GRACE NetCDF file")
    parser.add_argument("--db-url", dest="db_url", default=DEFAULT_DB_URL, help="PostgreSQL connection URL")
    return parser.parse_args()


def pick_name(candidates, available, kind):
    for candidate in candidates:
        if candidate in available:
            return candidate
    raise KeyError(f"Unable to find {kind}. Available: {sorted(available)}")


def normalize_longitudes(lon_values):
    lon_values = np.asarray(lon_values, dtype=float)
    if np.nanmax(lon_values) > 180:
        lon_values = np.where(lon_values > 180, lon_values - 360, lon_values)
    return lon_values


def pick_var_name(dataset, preferred, fallback_contains):
    for candidate in preferred:
        if candidate in dataset.data_vars:
            return candidate
    for name in dataset.data_vars:
        if fallback_contains in name.lower():
            return name
    raise KeyError(f"Unable to find variable matching {preferred!r} / {fallback_contains!r}")


def build_records(nc_path: Path):
    ds = xr.open_dataset(nc_path, decode_times=True)
    try:
        coord_names = set(ds.coords) | set(ds.variables)
        lat_name = pick_name(["lat", "latitude"], coord_names, "latitude coordinate")
        lon_name = pick_name(["lon", "longitude"], coord_names, "longitude coordinate")
        time_name = pick_name(["time", "date"], coord_names, "time coordinate")
        tws_name = pick_var_name(ds, ["lwe_thickness", "tws_anomaly"], "thickness")
        uncertainty_name = None
        for candidate in ["uncertainty", "lwe_uncertainty", "uncertainty_estimate"]:
            if candidate in ds.data_vars:
                uncertainty_name = candidate
                break

        lat_values = np.asarray(ds[lat_name].values, dtype=float)
        lon_values = normalize_longitudes(ds[lon_name].values)
        time_values = np.asarray(ds[time_name].values)

        lat_idx = np.where((lat_values >= NTB_BOUNDS["lat_min"]) & (lat_values <= NTB_BOUNDS["lat_max"]))[0]
        lon_idx = np.where((lon_values >= NTB_BOUNDS["lon_min"]) & (lon_values <= NTB_BOUNDS["lon_max"]))[0]
        if len(lat_idx) == 0 or len(lon_idx) == 0:
            raise ValueError("No GRACE grid points found inside NTB bounds.")

        tws = ds[tws_name].transpose(time_name, lat_name, lon_name).values
        uncertainty = (
            ds[uncertainty_name].transpose(time_name, lat_name, lon_name).values
            if uncertainty_name else None
        )

        records = []
        for t_idx, raw_time in enumerate(time_values):
            period = date.fromisoformat(np.datetime_as_string(raw_time, unit="D"))
            for la_idx in lat_idx:
                for lo_idx in lon_idx:
                    tws_value = tws[t_idx, la_idx, lo_idx]
                    if np.isnan(tws_value):
                        continue
                    unc_value = None
                    if uncertainty is not None:
                        unc_candidate = uncertainty[t_idx, la_idx, lo_idx]
                        if not np.isnan(unc_candidate):
                            unc_value = float(unc_candidate)
                    records.append((
                        period,
                        round(float(lat_values[la_idx]), 5),
                        round(float(lon_values[lo_idx]), 5),
                        round(float(tws_value), 4),
                        round(unc_value, 4) if unc_value is not None else None
                    ))
        return records
    finally:
        ds.close()


async def main():
    args = parse_args()
    nc_path = Path(args.nc_path)
    if not nc_path.exists():
        raise FileNotFoundError(f"NetCDF not found: {nc_path}")

    records = build_records(nc_path)
    conn = await asyncpg.connect(args.db_url)
    try:
        await conn.execute(CREATE_SQL)
        await conn.execute(MIGRATE_SQL)
        await conn.executemany(UPSERT_SQL, records)
        total = await conn.fetchval("SELECT COUNT(*) FROM grace_tws")
        print(f"Loaded {len(records)} GRACE rows from {nc_path}")
        print(f"grace_tws total rows: {total}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
