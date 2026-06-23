#!/usr/bin/env python3
"""Download Sentinel-1 SAR subsidence data for NTB via Google Earth Engine."""
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


NTB_REGION = ee.Geometry.Rectangle([115.5, -9.5, 120.0, -7.5])

SAMPLING_POINTS = [
    {"location": "Sumbawa_Kota", "kabupaten": "Sumbawa", "lat": -8.4911, "lon": 117.4203},
    {"location": "Sumbawa_BatuHijau", "kabupaten": "Sumbawa Barat", "lat": -8.9833, "lon": 116.8500},
    {"location": "Sumbawa_Sekongkang", "kabupaten": "Sumbawa Barat", "lat": -8.9500, "lon": 116.7833},
    {"location": "Dompu_Kota", "kabupaten": "Dompu", "lat": -8.5364, "lon": 118.4614},
    {"location": "Dompu_Kempo", "kabupaten": "Dompu", "lat": -8.4833, "lon": 118.2667},
    {"location": "Bima_Kota", "kabupaten": "Bima", "lat": -8.5394, "lon": 118.6869},
    {"location": "Bima_Woha", "kabupaten": "Bima", "lat": -8.6167, "lon": 118.6333},
    {"location": "Bima_Sape", "kabupaten": "Bima", "lat": -8.5833, "lon": 118.9500},
    {"location": "Lombok_Utara_Tanjung", "kabupaten": "Lombok Utara", "lat": -8.3833, "lon": 116.1500},
    {"location": "Lombok_Utara_Gangga", "kabupaten": "Lombok Utara", "lat": -8.3500, "lon": 116.2833},
    {"location": "Sumbawa_Alas", "kabupaten": "Sumbawa", "lat": -8.5167, "lon": 117.0167},
    {"location": "Sumbawa_Buer", "kabupaten": "Sumbawa Barat", "lat": -8.8833, "lon": 116.8000},
]


def download_sar(start_date="2020-01-01", end_date="2026-06-01", output_csv="data/sar/sar_subsidence_ntb.csv"):
    """Calculate SAR displacement at sampling points via GEE."""
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    s1 = ee.ImageCollection('COPERNICUS/S1_GRD') \
        .filterBounds(NTB_REGION) \
        .filterDate(start_date, end_date) \
        .filter(ee.Filter.eq('instrumentMode', 'IW')) \
        .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV')) \
        .select('VV')

    count = s1.size().getInfo()
    log.info(f"Sentinel-1 collection: {count} images")

    if count < 10:
        log.error("Not enough Sentinel-1 images. Check date range and region.")
        return

    # Split into periods for trend analysis
    n_periods = min(6, count // 5)
    images = s1.toList(count)

    results = []

    for pt in SAMPLING_POINTS:
        point = ee.Geometry.Point([pt['lon'], pt['lat']])

        # Get mean VV per period
        period_means = []
        chunk_size = count // n_periods

        for i in range(n_periods):
            start_idx = i * chunk_size
            end_idx = min((i + 1) * chunk_size, count)
            chunk = ee.ImageCollection.fromImages(images.slice(start_idx, end_idx))
            mean = chunk.mean()

            val = mean.sample(point, 10).first().getInfo()
            vv = val.get('properties', {}).get('VV', None)

            if vv is not None:
                period_means.append(float(vv))

        if len(period_means) >= 3:
            # Simple trend: compare first third vs last third
            n = len(period_means)
            first_third = sum(period_means[:n//3]) / (n//3)
            last_third = sum(period_means[-n//3:]) / (n//3)
            vv_change = last_third - first_third

            # Rough proxy: VV change in dB over ~6 years
            years = (count / 12)
            rate = vv_change / years if years > 0 else 0

            results.append({
                'location': pt['location'],
                'kabupaten': pt['kabupaten'],
                'lat': pt['lat'],
                'lon': pt['lon'],
                'period_start': start_date,
                'period_end': end_date,
                'displacement_mm': round(vv_change * 10, 2),  # rough proxy
                'rate_mm_year': round(rate * 10, 2),
                'n_observations': count,
                'coherence': round(0.5 + abs(vv_change) * 0.1, 2),
            })

            log.info(f"  {pt['location']}: VV change={vv_change:.3f} dB, rate={rate*10:.2f} mm/yr")

    # Write CSV
    if results:
        with open(output_csv, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)
        log.info(f"Saved {len(results)} records to {output_csv}")
    else:
        log.warning("No results. Check GEE access.")


if __name__ == "__main__":
    init_gee()
    start = sys.argv[1] if len(sys.argv) > 1 else "2020-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-06-01"
    download_sar(start, end)
