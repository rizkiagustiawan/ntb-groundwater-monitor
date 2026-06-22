#!/usr/bin/env python3
"""BMKG rainfall data sync script for NTB groundwater monitoring.

Fetches rainfall/meteorological data from BMKG API and upserts into PostGIS.

Usage:
    python3 scripts/bmkg_sync.py [start_date] [end_date]
    python3 scripts/bmkg_sync.py 2024-01-01 2024-01-31

Environment variables:
    DATABASE_URL  - PostgreSQL connection string
    BMKG_API_KEY  - optional API key for Bearer auth
"""
import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta

import psycopg2
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bmkg_sync")

DEFAULT_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rizki:ntb_env_2024@localhost:5435/ntb_groundwater",
)
BMKG_API_KEY = os.getenv("BMKG_API_KEY", "")
BMKG_BASE_URL = "https://data.bmkg.go.id"

# NTB rainfall monitoring stations
NTB_STATIONS = [
    {
        "station_id": "96001",
        "station_name": "Sumbawa",
        "lat": -8.4823,
        "lon": 117.4174,
    },
    {
        "station_id": "96002",
        "station_name": "Lombok",
        "lat": -8.6500,
        "lon": 116.3249,
    },
    {
        "station_id": "96003",
        "station_name": "Bima",
        "lat": -8.4606,
        "lon": 118.7234,
    },
    {
        "station_id": "96004",
        "station_name": "Dompu",
        "lat": -8.5312,
        "lon": 118.4623,
    },
]

UPSERT_SQL = """
INSERT INTO bmkg_rainfall (
    station_id, station_name, lat, lon, date,
    precip_mm, humidity_pct, temp_c, wind_speed_ms, geom, source
) VALUES (
    %(station_id)s, %(station_name)s, %(lat)s, %(lon)s, %(date)s,
    %(precip_mm)s, %(humidity_pct)s, %(temp_c)s, %(wind_speed_ms)s,
    ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326), %(source)s
)
ON CONFLICT (station_id, date) DO UPDATE SET
    station_name = EXCLUDED.station_name,
    precip_mm = EXCLUDED.precip_mm,
    humidity_pct = EXCLUDED.humidity_pct,
    temp_c = EXCLUDED.temp_c,
    wind_speed_ms = EXCLUDED.wind_speed_ms,
    geom = EXCLUDED.geom,
    source = EXCLUDED.source
"""


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sync BMKG rainfall data for NTB stations."
    )
    parser.add_argument(
        "start_date",
        nargs="?",
        default=(date.today() - timedelta(days=30)).isoformat(),
        help="Start date (YYYY-MM-DD), default: 30 days ago",
    )
    parser.add_argument(
        "end_date",
        nargs="?",
        default=date.today().isoformat(),
        help="End date (YYYY-MM-DD), default: today",
    )
    parser.add_argument(
        "--db-url",
        dest="db_url",
        default=DEFAULT_DB_URL,
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print records instead of inserting to DB",
    )
    return parser.parse_args()


def fetch_bmkg_rainfall(station_id: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch rainfall data from BMKG API for a station and date range.

    Tries multiple known BMKG endpoint patterns. Returns list of dicts with keys:
      date, precip_mm, humidity_pct, temp_c, wind_speed_ms
    """
    headers = {"Accept": "application/json"}
    if BMKG_API_KEY:
        headers["Authorization"] = f"Bearer {BMKG_API_KEY}"

    # Known BMKG API endpoint patterns (try in order)
    endpoints = [
        f"{BMKG_BASE_URL}/DataDMRS/4a16b33d8e5f4a6e/dmrs/{station_id}/{start_date}/{end_date}",
        f"{BMKG_BASE_URL}/DataAnalisis/prakiraan-hujan/{station_id}?start={start_date}&end={end_date}",
        f"{BMKG_BASE_URL}/stasiun/{station_id}/iklim?from={start_date}&to={end_date}",
    ]

    last_error = None
    for url in endpoints:
        try:
            log.info(f"Fetching {url}")
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return _parse_bmkg_response(data, start_date, end_date)
        except requests.RequestException as exc:
            last_error = exc
            log.debug(f"Endpoint failed: {url} -> {exc}")
            continue

    log.warning(
        f"All BMKG endpoints failed for station {station_id}. "
        f"Last error: {last_error}"
    )
    return []


def _parse_bmkg_response(data: dict, start_date: str, end_date: str) -> list[dict]:
    """Parse BMKG JSON response into flat record dicts.

    Handles multiple known response shapes.
    """
    records = []

    # Shape 1: {"data": [{"Tanggal": "...", "RR": ..., "RH": ..., "Tavg": ..., "ff_avg": ...}, ...]}
    if "data" in data and isinstance(data["data"], list):
        for row in data["data"]:
            rec = _extract_row(row)
            if rec:
                records.append(rec)
        return records

    # Shape 2: {"features": [...]}  (GeoJSON-like)
    if "features" in data:
        for feat in data["features"]:
            props = feat.get("properties", feat)
            rec = _extract_row(props)
            if rec:
                records.append(rec)
        return records

    # Shape 3: top-level list
    if isinstance(data, list):
        for row in data:
            rec = _extract_row(row)
            if rec:
                records.append(rec)
        return records

    log.warning(f"Unrecognised BMKG response shape: {list(data.keys())[:5]}")
    return []


def _extract_row(row: dict) -> dict | None:
    """Extract a single record from a BMKG response row.

    Returns None if the row can't be parsed.
    """
    # Date field candidates
    raw_date = (
        row.get("Tanggal")
        or row.get("tanggal")
        or row.get("date")
        or row.get("Date")
        or row.get("tgl")
    )
    if not raw_date:
        return None

    try:
        if isinstance(raw_date, str):
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
                try:
                    parsed_date = datetime.strptime(raw_date, fmt).date()
                    break
                except ValueError:
                    continue
            else:
                return None
        else:
            return None
    except Exception:
        return None

    def _float(row, *keys):
        for k in keys:
            val = row.get(k)
            if val is not None and val != "":
                try:
                    f = float(val)
                    return None if f == 9999 else f
                except (ValueError, TypeError):
                    continue
        return None

    return {
        "date": parsed_date,
        "precip_mm": _float(row, "RR", "rr", "precip_mm", "rainfall", "curah_hujan"),
        "humidity_pct": _float(row, "RH", "rh", "humidity_pct", "humidity", "kelembaban"),
        "temp_c": _float(row, "Tavg", "tavg", "temp_c", "temperature", "suhu"),
        "wind_speed_ms": _float(row, "ff_avg", "ff_avg", "wind_speed_ms", "wind_speed", "angin"),
    }


def fetch_all_stations(
    start_date: str, end_date: str
) -> list[dict]:
    """Fetch BMKG data for all NTB stations and return enriched records."""
    all_records = []
    for stn in NTB_STATIONS:
        log.info(
            f"Fetching station {stn['station_name']} ({stn['station_id']})..."
        )
        rows = fetch_bmkg_rainfall(stn["station_id"], start_date, end_date)
        if not rows:
            log.warning(f"  No data for {stn['station_name']}")
            continue
        for row in rows:
            row.update(
                {
                    "station_id": stn["station_id"],
                    "station_name": stn["station_name"],
                    "lat": stn["lat"],
                    "lon": stn["lon"],
                    "source": "bmkg_api",
                }
            )
        all_records.extend(rows)
        log.info(f"  Got {len(rows)} records for {stn['station_name']}")
    return all_records


def upsert_records(conn, records: list[dict]) -> int:
    """Upsert records into bmkg_rainfall. Returns count inserted/updated."""
    with conn.cursor() as cur:
        for rec in records:
            cur.execute(UPSERT_SQL, rec)
    conn.commit()
    return len(records)


def main():
    args = parse_args()

    # Validate dates
    try:
        sd = date.fromisoformat(args.start_date)
        ed = date.fromisoformat(args.end_date)
    except ValueError:
        log.error("Dates must be in YYYY-MM-DD format.")
        sys.exit(1)

    if sd > ed:
        log.error(f"start_date ({sd}) must be <= end_date ({ed})")
        sys.exit(1)

    log.info(f"BMKG sync: {sd} to {ed}")

    # Fetch
    records = fetch_all_stations(sd.isoformat(), ed.isoformat())
    if not records:
        log.warning("No records fetched from BMKG API.")
        sys.exit(0)

    log.info(f"Fetched {len(records)} total records from {len(NTB_STATIONS)} stations.")

    if args.dry_run:
        print(f"DRY RUN: {len(records)} records. First 5:")
        for r in records[:5]:
            print(
                f"  {r['station_name']:12s} {r['date']} "
                f"rain={r['precip_mm']}mm temp={r['temp_c']}C "
                f"humid={r['humidity_pct']}% wind={r['wind_speed_ms']}m/s"
            )
        return

    # Insert
    log.info(f"Connecting to database...")
    conn = psycopg2.connect(args.db_url)
    try:
        count = upsert_records(conn, records)
        log.info(f"Upserted {count} BMKG records.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
