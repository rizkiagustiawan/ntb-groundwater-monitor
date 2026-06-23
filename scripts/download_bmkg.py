#!/usr/bin/env python3
"""Download BMKG rainfall data for NTB from public API (no key required)."""
import os
import sys
import json
import logging
import csv
import requests
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BMKG_API = "https://api.bmkg.go.id/publik/prakiraan-cuaca"

# NTB locations with valid ADM4 codes
NTB_LOCATIONS = [
    {"adm4": "52.01.01.1001", "name": "Gerung", "kabupaten": "Lombok Barat"},
    {"adm4": "52.02.01.1001", "name": "Praya", "kabupaten": "Lombok Tengah"},
    {"adm4": "52.03.01.2001", "name": "Keruak", "kabupaten": "Lombok Timur"},
    {"adm4": "52.04.02.2001", "name": "Lunyuk", "kabupaten": "Sumbawa"},
    {"adm4": "52.05.01.1001", "name": "Dompu", "kabupaten": "Dompu"},
    {"adm4": "52.06.01.1001", "name": "Woha", "kabupaten": "Bima"},
    {"adm4": "52.07.01.2001", "name": "Jereweh", "kabupaten": "Sumbawa Barat"},
    {"adm4": "52.07.02.2001", "name": "Taliwang", "kabupaten": "Sumbawa Barat"},
    {"adm4": "52.08.01.2001", "name": "Tanjung", "kabupaten": "Lombok Utara"},
    {"adm4": "52.71.02.1001", "name": "Mataram", "kabupaten": "Kota Mataram"},
    {"adm4": "52.72.03.1001", "name": "Asakota", "kabupaten": "Kota Bima"},
]


def fetch_bmkg(adm4: str) -> dict:
    """Fetch weather data from BMKG public API."""
    try:
        resp = requests.get(f"{BMKG_API}?adm4={adm4}", timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(f"  Failed {adm4}: {e}")
        return None


def download_bmkg(output_csv="data/bmkg/bmkg_rainfall_ntb.csv"):
    """Download current BMKG weather data for all NTB locations."""
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)

    # Load existing data
    existing = set()
    if os.path.exists(output_csv):
        with open(output_csv) as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing.add((row['station_id'], row['date']))
        log.info(f"Loaded {len(existing)} existing records")

    all_records = []
    
    for loc in NTB_LOCATIONS:
        log.info(f"Fetching {loc['name']} ({loc['kabupaten']})...")
        data = fetch_bmkg(loc['adm4'])
        
        if not data or 'data' not in data:
            continue
        
        # Structure: data[0]['cuaca'] = [day1, day2, day3], each day = [hour1, hour2, ...]
        entry = data['data'][0]
        loc_info = entry.get('lokasi', data.get('lokasi', {}))
        lat = loc_info.get('lat', 0)
        lon = loc_info.get('lon', 0)
        
        daily = {}
        for day in entry.get('cuaca', []):
            for hour in day:
                dt = hour.get('local_datetime', hour.get('datetime', ''))
                if not dt:
                    continue
                date = dt.split(' ')[0] if ' ' in dt else dt[:10]
                
                if date not in daily:
                    daily[date] = {'precip': [], 'temp': [], 'humidity': [], 'wind': []}
                
                if hour.get('tp') is not None:
                    daily[date]['precip'].append(float(hour['tp']))
                if hour.get('t') is not None:
                    daily[date]['temp'].append(float(hour['t']))
                if hour.get('hu') is not None:
                    daily[date]['humidity'].append(float(hour['hu']))
                if hour.get('ws') is not None:
                    daily[date]['wind'].append(float(hour['ws']))
        
        for date, vals in daily.items():
            record = {
                'station_id': loc['adm4'],
                'station_name': f"{loc['name']}, {loc['kabupaten']}",
                'lat': lat,
                'lon': lon,
                'date': date,
                'precip_mm': round(sum(vals['precip']), 1) if vals['precip'] else 0,
                'humidity_pct': round(sum(vals['humidity'])/len(vals['humidity']), 1) if vals['humidity'] else None,
                'temp_c': round(sum(vals['temp'])/len(vals['temp']), 1) if vals['temp'] else None,
                'wind_speed_ms': round(sum(vals['wind'])/len(vals['wind'])*1000/3600, 1) if vals['wind'] else None,
            }
            
            key = (record['station_id'], record['date'])
            if key not in existing:
                all_records.append(record)
                existing.add(key)
    
    # Write/append CSV
    if all_records:
        mode = 'a' if os.path.exists(output_csv) and existing else 'w'
        with open(output_csv, mode, newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['station_id','station_name','lat','lon','date',
                                                    'precip_mm','humidity_pct','temp_c','wind_speed_ms'])
            if mode == 'w':
                writer.writeheader()
            writer.writerows(all_records)
        log.info(f"Added {len(all_records)} new records")
    else:
        log.info("No new records to add")


if __name__ == "__main__":
    download_bmkg()
