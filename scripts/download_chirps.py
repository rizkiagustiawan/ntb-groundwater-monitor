#!/usr/bin/env python3
"""Download CHIRPS precipitation data for NTB via Google Earth Engine (optimized)."""
import os
import sys
import logging
import csv
import time
import ee

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

GEE_KEY = os.getenv("GEE_KEY_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "gee-key.json"))
GEE_ACCOUNT = os.getenv("GEE_SERVICE_ACCOUNT", "geoesg-worker@thermal-cathode-421211.iam.gserviceaccount.com")

NTB_BBOX = [115.5, -9.5, 120.0, -7.5]
GRID_POINTS = []
for lat in [-9.25, -8.75, -8.25, -7.75]:
    for lon in [115.75, 116.25, 116.75, 117.25, 117.75, 118.25, 118.75, 119.25]:
        GRID_POINTS.append((lat, lon))


def init_gee():
    if os.path.exists(GEE_KEY):
        credentials = ee.ServiceAccountCredentials(GEE_ACCOUNT, GEE_KEY)
        ee.Initialize(credentials)
        log.info(f"GEE initialized with service account: {GEE_ACCOUNT}")
    else:
        ee.Initialize()
        log.info("GEE initialized with default credentials")


def download_chirps(start_year=2000, end_year=2026, output_csv="data/chirps/chirps_ntb.csv"):
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    ntg_region = ee.Geometry.Rectangle(NTB_BBOX)
    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY") \
        .filterDate(f"{start_year}-01-01", f"{end_year}-12-31") \
        .filterBounds(ntg_region)

    log.info(f"CHIRPS collection: {chirps.size().getInfo()} daily images")

    # Create multi-point geometry for batch extraction
    fc = ee.FeatureCollection([ee.Feature(ee.Geometry.Point([lon, lat]), {'lat': lat, 'lon': lon})
                               for lat, lon in GRID_POINTS])

    # Process year by year to avoid memory issues
    all_features = []

    for year in range(start_year, end_year + 1):
        log.info(f"Processing {year}...")
        t0 = time.time()

        for month in range(1, 13):
            start = ee.Date.fromYMD(year, month, 1)
            end = start.advance(1, 'month')
            monthly = chirps.filterDate(start, end)

            # Skip if no images in this month
            if monthly.size().getInfo() == 0:
                continue

            monthly = monthly.sum()

            # Batch reduceRegion for all points
            def extract_for_point(feature):
                geom = feature.geometry()
                val = monthly.reduceRegion(ee.Reducer.first(), geom, 0.05)
                precip = val.get('precipitation')
                return feature.set('precip_mm', precip if precip is not None else -9999,
                                   'year', year, 'month', month)

            results = fc.map(extract_for_point).getInfo()

            for feat in results['features']:
                props = feat['properties']
                if props.get('precip_mm') is not None and props['precip_mm'] != -9999:
                    all_features.append(props)

        elapsed = time.time() - t0
        n_records = len([f for f in all_features if f.get('year') == year])
        log.info(f"  {year}: {n_records} records in {elapsed:.1f}s")

    # Load existing data to avoid overwriting
    existing = set()
    if os.path.exists(output_csv):
        with open(output_csv) as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing.add((float(row['lat']), float(row['lon']), int(row['year']), int(row['month'])))
        log.info(f"Loaded {len(existing)} existing records from {output_csv}")

    # Write CSV (append new records)
    if all_features:
        new_records = [f for f in all_features
                       if (f['lat'], f['lon'], f['year'], f['month']) not in existing]
        
        mode = 'a' if os.path.exists(output_csv) and existing else 'w'
        with open(output_csv, mode, newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['lat', 'lon', 'year', 'month', 'precip_mm'])
            if mode == 'w':
                writer.writeheader()
            writer.writerows(new_records)
        
        total = len(existing) + len(new_records)
        log.info(f"Added {len(new_records)} new records. Total: {total}")
    else:
        log.warning("No data extracted!")


if __name__ == "__main__":
    init_gee()
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    download_chirps(start, end)
