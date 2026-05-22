#!/usr/bin/env python3
import argparse
import asyncio
import os
import csv
from collections import defaultdict
from pathlib import Path

import asyncpg
import numpy as np
import xarray as xr
from tqdm import tqdm

DEFAULT_DB_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@db:5432/ntb_groundwater")
DEFAULT_NC_PATH = os.getenv(
    "GLDAS_NC_PATH",
    "/data/gldas/GLDAS_NOAH025_M.2.1.nc"
)

NTB_BOUNDS = {
    "lat_min": -9.25,
    "lat_max": -7.75,
    "lon_min": 115.75,
    "lon_max": 119.25,
}

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS gldas_sms (
    id          SERIAL PRIMARY KEY,
    lat         FLOAT NOT NULL,
    lon         FLOAT NOT NULL,
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL,
    sms_cm_ewh  FLOAT,
    sms_anomaly FLOAT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(lat, lon, year, month)
);
"""

UPSERT_SQL = """
INSERT INTO gldas_sms (
    lat, lon, year, month, sms_cm_ewh, sms_anomaly
) VALUES (
    $1, $2, $3, $4, $5, $6
)
ON CONFLICT (lat, lon, year, month) DO UPDATE SET
    sms_cm_ewh = EXCLUDED.sms_cm_ewh,
    sms_anomaly = EXCLUDED.sms_anomaly
"""

def parse_args():
    parser = argparse.ArgumentParser(description="Load NTB GLDAS Soil Moisture into PostGIS.")
    parser.add_argument("--nc", dest="nc_path", default=DEFAULT_NC_PATH, help="Path to GLDAS NetCDF file")
    parser.add_argument("--input", dest="csv_path", help="Path to GEE Export CSV file")
    parser.add_argument("--db-url", dest="db_url", default=DEFAULT_DB_URL, help="PostgreSQL connection URL")
    parser.add_argument("--dry-run", action="store_true", help="Print records instead of inserting to DB")
    parser.add_argument("--print-vars", action="store_true", help="Print variable names from NetCDF and exit")
    parser.add_argument("--limit", type=int, help="Limit number of records to process")
    parser.add_argument("--year-start", type=int, default=2002, help="Filter start year")
    parser.add_argument("--year-end", type=int, default=2025, help="Filter end year")
    return parser.parse_args()

def normalize_longitudes(lon_values):
    lon_values = np.asarray(lon_values, dtype=float)
    if np.nanmax(lon_values) > 180:
        lon_values = np.where(lon_values > 180, lon_values - 360, lon_values)
    return lon_values

def build_records(nc_path: Path, args, print_vars_only: bool = False):
    ds = xr.open_dataset(nc_path, decode_times=True)
    try:
        if print_vars_only:
            print(f"Variables in {nc_path.name}:")
            for v in ds.data_vars:
                print(f"  - {v}")
            return None

        # GLDAS Variables (kg/m2)
        v0_10 = "SoilMoi0_10cm_inst"
        v10_40 = "SoilMoi10_40cm_inst"
        v40_100 = "SoilMoi40_100cm_inst"
        v100_200 = "SoilMoi100_200cm_inst"
        
        for v in [v0_10, v10_40, v40_100, v100_200]:
            if v not in ds.data_vars:
                raise KeyError(f"Variable {v} not found in GLDAS file. Found: {list(ds.data_vars.keys())}")

        total_sms_kg = (
            ds[v0_10] * 0.10 +
            ds[v10_40] * 0.30 +
            ds[v40_100] * 0.60 +
            ds[v100_200] * 1.00
        )
        total_sms_cm = total_sms_kg / 10.0

        lat_values = ds.lat.values
        lon_values = normalize_longitudes(ds.lon.values)
        time_values = ds.time.values

        lat_idx = np.where((lat_values >= NTB_BOUNDS["lat_min"]) & (lat_values <= NTB_BOUNDS["lat_max"]))[0]
        lon_idx = np.where((lon_values >= NTB_BOUNDS["lon_min"]) & (lon_values <= NTB_BOUNDS["lon_max"]))[0]
        
        if len(lat_idx) == 0 or len(lon_idx) == 0:
            raise ValueError("No GLDAS grid points found inside NTB bounds.")

        # Baseline 2004-2009 mean
        baseline_years = [2004, 2005, 2006, 2007, 2008, 2009]
        baseline_ds = total_sms_cm.sel(time=total_sms_cm.time.dt.year.isin(baseline_years))
        if len(baseline_ds.time) == 0:
            print("Warning: Baseline years 2004-2009 not found. Using full record for mean.")
            baseline_mean = total_sms_cm.mean(dim='time')
        else:
            baseline_mean = baseline_ds.mean(dim='time')

        records = []
        time_loop = range(len(time_values))
        if not print_vars_only:
             print(f"Processing GLDAS NetCDF records...")
             time_loop = tqdm(time_loop, desc="Months")

        for t_idx in time_loop:
            year = int(ds.time.dt.year[t_idx])
            month = int(ds.time.dt.month[t_idx])
            
            if year < args.year_start or year > args.year_end:
                continue

            for la_idx in lat_idx:
                for lo_idx in lon_idx:
                    sms_cm = float(total_sms_cm[t_idx, la_idx, lo_idx].values)
                    mean_cm = float(baseline_mean[la_idx, lo_idx].values)
                    
                    if np.isnan(sms_cm):
                        continue
                    
                    anomaly = sms_cm - mean_cm
                    
                    records.append((
                        float(lat_values[la_idx]),
                        float(lon_values[lo_idx]),
                        year,
                        month,
                        round(sms_cm, 4),
                        round(anomaly, 4)
                    ))
                    
                    if args.limit and len(records) >= args.limit:
                        return records
        return records
    finally:
        ds.close()

def build_records_from_csv(csv_path: Path, args):
    """
    Read GEE export CSV and compute 2004-2009 baseline anomaly.
    Columns: point_id, lat, lon, year, month, period, sms_cm_ewh
    """
    raw_data = []
    baseline_sums = defaultdict(float)
    baseline_counts = defaultdict(int)

    print(f"Reading CSV from {csv_path}...")
    with open(csv_path, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                lat = float(row['lat'])
                lon = float(row['lon'])
                year = int(row['year'])
                month = int(row['month'])
                sms_val = float(row['sms_cm_ewh'])
                
                if year < args.year_start or year > args.year_end:
                    continue

                raw_data.append({
                    'lat': lat, 'lon': lon, 'year': year, 'month': month, 'sms': sms_val
                })

                if 2004 <= year <= 2009:
                    coord = (lat, lon)
                    baseline_sums[coord] += sms_val
                    baseline_counts[coord] += 1
            except (ValueError, KeyError):
                continue

    baseline_means = {
        coord: (baseline_sums[coord] / baseline_counts[coord])
        for coord in baseline_sums if baseline_counts[coord] > 0
    }

    print(f"Processing CSV records and computing anomalies...")
    records = []
    for item in tqdm(raw_data, desc="Records"):
        coord = (item['lat'], item['lon'])
        mean_val = baseline_means.get(coord)
        if mean_val is None:
            # Fallback to all data mean for this point if baseline years missing
            point_vals = [r['sms'] for r in raw_data if (r['lat'], r['lon']) == coord]
            mean_val = sum(point_vals) / len(point_vals) if point_vals else 0

        anomaly = item['sms'] - mean_val
        records.append((
            item['lat'],
            item['lon'],
            item['year'],
            item['month'],
            round(item['sms'], 4),
            round(anomaly, 4)
        ))
        
        if args.limit and len(records) >= args.limit:
            break
            
    return records

async def main():
    args = parse_args()
    
    if args.csv_path:
        csv_path = Path(args.csv_path)
        if not csv_path.exists():
            print(f"Error: CSV file not found at {csv_path}")
            return
        records = build_records_from_csv(csv_path, args)
    else:
        nc_path = Path(args.nc_path)
        if not nc_path.exists():
            print(f"Skipping GLDAS load: NetCDF not found at {nc_path}")
            return

        if args.print_vars:
            build_records(nc_path, args, print_vars_only=True)
            return

        records = build_records(nc_path, args)
    
    if args.dry_run:
        print(f"DRY RUN: Generated {len(records)} records. Showing first 3:")
        for r in records[:3]:
            print(f"  Lat:{r[0]}, Lon:{r[1]}, Year:{r[2]}, Month:{r[3]}, SMS:{r[4]}, Anom:{r[5]}")
        return

    conn = await asyncpg.connect(args.db_url)
    try:
        await conn.execute(CREATE_SQL)
        await conn.executemany(UPSERT_SQL, records)
        print(f"Loaded {len(records)} GLDAS records.")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
