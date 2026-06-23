#!/usr/bin/env python3
import argparse
import csv
import os
import sys
from datetime import date, datetime

import psycopg2
import psycopg2.extras


DEFAULT_DB_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@db:5432/ntb_groundwater")

SAMPLING_POINTS = [
    {"location": "Sumbawa_Kota", "kabupaten": "Sumbawa", "lat": -8.4911, "lon": 117.4203},
    {"location": "Sumbawa_BatuHijau", "kabupaten": "Sumbawa Barat", "lat": -8.9833, "lon": 116.8500},
    {"location": "Sumbawa_Sekongkang", "kabupaten": "Sumbawa Barat", "lat": -8.9500, "lon": 116.7833},
    {"location": "Dompu_Kota", "kabupaten": "Dompu", "lat": -8.5364, "lon": 118.4614},
    {"location": "Bima_Kota", "kabupaten": "Bima", "lat": -8.5394, "lon": 118.6869},
    {"location": "Bima_Woha", "kabupaten": "Bima", "lat": -8.6167, "lon": 118.6333},
    {"location": "Lombok_Utara", "kabupaten": "Lombok Utara", "lat": -8.3500, "lon": 116.2833},
    {"location": "Lombok_Tanjung", "kabupaten": "Lombok Utara", "lat": -8.3833, "lon": 116.1500},
]

UPSERT_SQL = """
INSERT INTO sar_subsidence (
    location, kabupaten, lat, lon, period_start, period_end,
    displacement_mm, rate_mm_year, n_observations, coherence, geom, source
) VALUES (
    %(location)s, %(kabupaten)s, %(lat)s, %(lon)s, %(period_start)s, %(period_end)s,
    %(displacement_mm)s, %(rate_mm_year)s, %(n_observations)s, %(coherence)s,
    ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), %(source)s
)
ON CONFLICT (location, period_start, period_end) DO UPDATE SET
    kabupaten = EXCLUDED.kabupaten,
    lat = EXCLUDED.lat,
    lon = EXCLUDED.lon,
    displacement_mm = EXCLUDED.displacement_mm,
    rate_mm_year = EXCLUDED.rate_mm_year,
    n_observations = EXCLUDED.n_observations,
    coherence = EXCLUDED.coherence,
    geom = EXCLUDED.geom,
    source = EXCLUDED.source
"""


def parse_args():
    parser = argparse.ArgumentParser(description="Sentinel-1 SAR subsidence detection into PostGIS.")
    parser.add_argument("mode", choices=["gee", "csv"], help="Data source: 'gee' or 'csv'")
    parser.add_argument("input_path", nargs="?", default=None, help="CSV file path (required for csv mode)")
    parser.add_argument("--db-url", dest="db_url", default=DEFAULT_DB_URL, help="PostgreSQL connection URL")
    return parser.parse_args()


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sar_subsidence (
                id SERIAL PRIMARY KEY, location VARCHAR(100) NOT NULL, kabupaten VARCHAR(100),
                lat FLOAT NOT NULL, lon FLOAT NOT NULL, period_start DATE NOT NULL, period_end DATE NOT NULL,
                displacement_mm FLOAT, rate_mm_year FLOAT, n_observations INTEGER, coherence FLOAT,
                geom GEOMETRY(Point, 4326), source VARCHAR(50) DEFAULT 'sentinel1_gee',
                created_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(location, period_start, period_end)
            );
            CREATE INDEX IF NOT EXISTS idx_sar_geom ON sar_subsidence USING GIST(geom);
            CREATE INDEX IF NOT EXISTS idx_sar_period ON sar_subsidence(period_start, period_end);
        """)
        conn.commit()


def load_csv(csv_path):
    records = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append({
                "location": row["location"].strip(),
                "kabupaten": row.get("kabupaten", "").strip() or None,
                "lat": float(row["lat"]),
                "lon": float(row["lon"]),
                "period_start": date.fromisoformat(row["period_start"]),
                "period_end": date.fromisoformat(row["period_end"]),
                "displacement_mm": float(row["displacement_mm"]) if row.get("displacement_mm") else None,
                "rate_mm_year": float(row["rate_mm_year"]) if row.get("rate_mm_year") else None,
                "n_observations": int(row["n_observations"]) if row.get("n_observations") else None,
                "coherence": float(row["coherence"]) if row.get("coherence") else None,
                "source": "sentinel1_csv",
            })
    return records


def run_gee(db_url):
    import ee

    GEE_KEY = os.getenv("GEE_KEY_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "gee-key.json"))
    GEE_ACCOUNT = os.getenv("GEE_SERVICE_ACCOUNT", "geoesg-worker@thermal-cathode-421211.iam.gserviceaccount.com")

    if os.path.exists(GEE_KEY):
        credentials = ee.ServiceAccountCredentials(GEE_ACCOUNT, GEE_KEY)
        ee.Initialize(credentials)
        print(f"GEE initialized with service account: {GEE_ACCOUNT}")
    else:
        ee.Initialize()
        print("GEE initialized with default credentials")

    s1 = ee.ImageCollection("COPERNICUS/S1_GRD") \
        .filter(ee.Filter.eq("instrumentMode", "IW")) \
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV")) \
        .select("VV")

    total_size = s1.size().getInfo()
    if total_size == 0:
        print("No Sentinel-1 images found for NTB region.")
        return

    midpoint = total_size // 2
    first_half = s1.limit(midpoint)
    second_half = s1.limit(midpoint, "system:time_start", False)

    mean_first = first_half.mean()
    mean_second = second_half.mean()
    displacement = mean_second.subtract(mean_first).rename("displacement_db")

    first_dates = first_half.aggregate_array("system:time_start").map(
        lambda t: ee.Date(t).format("YYYY-MM-dd")
    ).getInfo()
    second_dates = second_half.aggregate_array("system:time_start").map(
        lambda t: ee.Date(t).format("YYYY-MM-dd")
    ).getInfo()
    period_start = date.fromisoformat(sorted(first_dates)[0])
    period_end = date.fromisoformat(sorted(second_dates)[-1])
    days = (period_end - period_start).days or 1

    records = []
    for pt in SAMPLING_POINTS:
        geom = ee.Geometry.Point(pt["lon"], pt["lat"])
        sample = displacement.sample(geom, 10).first()
        try:
            val = sample.get("displacement_db").getInfo()
            disp_mm = round(float(val) * 10.0, 2)
            rate = round(disp_mm / (days / 365.25), 2)
        except Exception:
            disp_mm = None
            rate = None

        records.append({
            "location": pt["location"],
            "kabupaten": pt["kabupaten"],
            "lat": pt["lat"],
            "lon": pt["lon"],
            "period_start": period_start,
            "period_end": period_end,
            "displacement_mm": disp_mm,
            "rate_mm_year": rate,
            "n_observations": total_size,
            "coherence": None,
            "source": "sentinel1_gee",
        })

    conn = psycopg2.connect(db_url)
    try:
        ensure_table(conn)
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, UPSERT_SQL, records)
        conn.commit()
        print(f"Loaded {len(records)} SAR records from GEE ({period_start} to {period_end})")
    finally:
        conn.close()


def run_csv(csv_path, db_url):
    records = load_csv(csv_path)
    if not records:
        print(f"No records found in {csv_path}")
        return

    conn = psycopg2.connect(db_url)
    try:
        ensure_table(conn)
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, UPSERT_SQL, records)
        conn.commit()
        print(f"Loaded {len(records)} SAR records from {csv_path}")
    finally:
        conn.close()


def main():
    args = parse_args()

    if args.mode == "gee":
        run_gee(args.db_url)
    elif args.mode == "csv":
        if not args.input_path:
            print("Error: csv mode requires a file path argument.", file=sys.stderr)
            sys.exit(1)
        run_csv(args.input_path, args.db_url)


if __name__ == "__main__":
    main()
