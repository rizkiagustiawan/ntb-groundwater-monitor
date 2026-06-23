#!/usr/bin/env python3
"""Download CHIRPS precipitation data for NTB via Google Earth Engine."""
import os
import sys
import logging
import csv
import ee

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

GEE_KEY = os.getenv("GEE_KEY_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "gee-key.json"))
GEE_ACCOUNT = os.getenv("GEE_SERVICE_ACCOUNT", "geoesg-worker@thermal-cathode-421211.iam.gserviceaccount.com")


def init_gee():
    """Initialize GEE with service account."""
    if os.path.exists(GEE_KEY):
        credentials = ee.ServiceAccountCredentials(GEE_ACCOUNT, GEE_KEY)
        ee.Initialize(credentials)
        log.info(f"GEE initialized with service account: {GEE_ACCOUNT}")
    else:
        ee.Initialize()
        log.info("GEE initialized with default credentials")


# NTB bounding box
NTB_BBOX = [115.5, -9.5, 120.0, -7.5]
NTB_REGION = ee.Geometry.Rectangle(NTB_BBOX)

# Grid points matching GRACE resolution (0.5 degree)
GRID_POINTS = []
for lat in [-9.25, -8.75, -8.25, -7.75]:
    for lon in [115.75, 116.25, 116.75, 117.25, 117.75, 118.25, 118.75, 119.25]:
        GRID_POINTS.append((lat, lon))


def download_chirps(start_year=2000, end_year=2026, output_csv="data/chirps/chirps_ntb.csv"):
    """Download monthly CHIRPS precipitation for NTB grid points."""
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    chirps = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY") \
        .filterDate(f"{start_year}-01-01", f"{end_year}-12-31") \
        .filterBounds(NTB_REGION)

    log.info(f"CHIRPS collection size: {chirps.size().getInfo()} images")

    # Aggregate to monthly
    def monthly_sum(year, month):
        start = ee.Date.fromYMD(year, month, 1)
        end = start.advance(1, 'month')
        monthly = chirps.filterDate(start, end).sum()
        return monthly.set('system:time_start', start.millis()) \
                      .set('year', year).set('month', month)

    years = ee.List.sequence(start_year, end_year)
    months = ee.List.sequence(1, 12)

    monthly_collection = ee.ImageCollection.fromImages(
        years.map(lambda y: months.map(lambda m: monthly_sum(y, m))).flatten()
    )

    log.info(f"Monthly collection: {monthly_collection.size().getInfo()} months")

    # Sample at grid points
    features = []
    for lat, lon in GRID_POINTS:
        point = ee.Geometry.Point([lon, lat])

        def extract_point(img):
            val = img.reduceRegion(ee.Reducer.first(), point, 0.05)
            return ee.Feature(None, {
                'lat': lat,
                'lon': lon,
                'year': img.get('year'),
                'month': img.get('month'),
                'precip_mm': val.get('precipitation')
            })

        sampled = monthly_collection.map(extract_point)
        fc = sampled.getInfo()

        for feat in fc['features']:
            props = feat['properties']
            if props.get('precip_mm') is not None:
                features.append(props)

    # Write CSV
    with open(output_csv, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['lat', 'lon', 'year', 'month', 'precip_mm'])
        writer.writeheader()
        writer.writerows(features)

    log.info(f"Saved {len(features)} records to {output_csv}")


if __name__ == "__main__":
    init_gee()
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    download_chirps(start, end)
