#!/usr/bin/env python3
"""Download BMKG rainfall data for NTB stations."""
import os
import sys
import json
import logging
import csv
import requests
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BMKG_API_KEY = os.getenv("BMKG_API_KEY", "")
BMKG_BASE = "https://data.bmkg.go.id"

# NTB BMKG stations (official station IDs)
NTB_STATIONS = [
    {"id": "96001", "name": "Sumbawa Sultan Muhammad Kaharuddin III", "lat": -8.4911, "lon": 117.4203},
    {"id": "96003", "name": "Lombok Praya", "lat": -8.7569, "lon": 116.2769},
    {"id": "96004", "name": "Bima", "lat": -8.5394, "lon": 118.6869},
    {"id": "96009", "name": "Dompu", "lat": -8.5364, "lon": 118.4614},
]


def try_bmkg_endpoints(station_id, start_date, end_date):
    """Try multiple known BMKG API patterns."""
    headers = {"Authorization": f"Bearer {BMKG_API_KEY}"} if BMKG_API_KEY else {}

    endpoints = [
        f"{BMKG_BASE}/v1/climate/daily?station={station_id}&start={start_date}&end={end_date}",
        f"{BMKG_BASE}/api/v1/climate/daily?id={station_id}&from={start_date}&to={end_date}",
        f"{BMKG_BASE}/v1/weather/daily?station_id={station_id}&date_start={start_date}&date_end={end_date}",
    ]

    for url in endpoints:
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                if data:
                    log.info(f"  Success with: {url}")
                    return data
        except Exception as e:
            log.debug(f"  Failed: {url} — {e}")

    return None


def download_bmkg(start_date="2020-01-01", end_date=None, output_csv="data/bmkg/bmkg_rainfall_ntb.csv"):
    """Download BMKG rainfall data for NTB stations."""
    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    all_records = []

    for station in NTB_STATIONS:
        log.info(f"Fetching station {station['id']} ({station['name']})")

        data = try_bmkg_endpoints(station['id'], start_date, end_date)

        if data is None:
            log.warning(f"  No data from API. Check BMKG_API_KEY or API availability.")
            log.info(f"  Register at: https://data.bmkg.go.id")
            continue

        # Parse response (format varies)
        records = data if isinstance(data, list) else data.get('data', data.get('results', []))

        for r in records:
            all_records.append({
                'station_id': station['id'],
                'station_name': station['name'],
                'lat': station['lat'],
                'lon': station['lon'],
                'date': r.get('date', r.get('tanggal', '')),
                'precip_mm': float(r.get('rainfall', r.get('rr', 0)) or 0),
                'humidity_pct': float(r.get('humidity', r.get('hu', 0)) or 0),
                'temp_c': float(r.get('temperature', r.get('tavg', 0)) or 0),
            })

    if all_records:
        with open(output_csv, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=all_records[0].keys())
            writer.writeheader()
            writer.writerows(all_records)
        log.info(f"Saved {len(all_records)} records to {output_csv}")
    else:
        log.warning("No records downloaded. Create empty CSV as placeholder.")
        with open(output_csv, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['station_id','station_name','lat','lon','date','precip_mm','humidity_pct','temp_c'])


if __name__ == "__main__":
    start = sys.argv[1] if len(sys.argv) > 1 else "2020-01-01"
    end = sys.argv[2] if len(sys.argv) > 2 else None
    download_bmkg(start, end)
