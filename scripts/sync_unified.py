#!/usr/bin/env python3
"""Unified multi-sensor ETL pipeline — merges all sources into unified_monitoring table.

Usage:
    python3 scripts/sync_unified.py [start_year] [end_year]
    python3 scripts/sync_unified.py 2015 2024
"""
import argparse
import logging
import os
import sys

import psycopg2
import psycopg2.extras

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sync_unified")

DEFAULT_DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://rizki:ntb_env_2024@localhost:5435/ntb_groundwater",
)


def parse_args():
    parser = argparse.ArgumentParser(description="Unified multi-sensor sync pipeline.")
    parser.add_argument("start_year", nargs="?", type=int, default=2002, help="Start year (default: 2002)")
    parser.add_argument("end_year", nargs="?", type=int, default=2025, help="End year (default: 2025)")
    parser.add_argument("--db-url", dest="db_url", default=DEFAULT_DB_URL, help="PostgreSQL connection URL")
    return parser.parse_args()


def normalize(val, min_v, max_v):
    if val is None:
        return 0.5
    return max(0.0, min(1.0, (val - min_v) / (max_v - min_v)))


def risk_level_from_drought_index(di):
    if di is None:
        return None
    if di >= 0.6:
        return "normal"
    if di >= 0.4:
        return "waspada"
    if di >= 0.2:
        return "kritis"
    return "sangat_kritis"


UPSERT_SQL = """
INSERT INTO unified_monitoring (
    period_date, lat, lon,
    tws_anomaly, tws_uncertainty, sms_anomaly, gws_anomaly,
    chirps_precip_mm, chirps_anomaly, bmkg_precip_mm, bmkg_station_id, bmkg_distance_km,
    ndvi, ndvi_location, ndvi_distance_km,
    sar_subsidence_mm, sar_rate_mm_year,
    drought_index, risk_level, grid_resolution, data_completeness
) VALUES (
    %(period_date)s, %(lat)s, %(lon)s,
    %(tws_anomaly)s, %(tws_uncertainty)s, %(sms_anomaly)s, %(gws_anomaly)s,
    %(chirps_precip_mm)s, %(chirps_anomaly)s, %(bmkg_precip_mm)s, %(bmkg_station_id)s, %(bmkg_distance_km)s,
    %(ndvi)s, %(ndvi_location)s, %(ndvi_distance_km)s,
    %(sar_subsidence_mm)s, %(sar_rate_mm_year)s,
    %(drought_index)s, %(risk_level)s, %(grid_resolution)s, %(data_completeness)s
)
ON CONFLICT (period_date, lat, lon) DO UPDATE SET
    tws_anomaly = EXCLUDED.tws_anomaly,
    tws_uncertainty = EXCLUDED.tws_uncertainty,
    sms_anomaly = EXCLUDED.sms_anomaly,
    gws_anomaly = EXCLUDED.gws_anomaly,
    chirps_precip_mm = EXCLUDED.chirps_precip_mm,
    chirps_anomaly = EXCLUDED.chirps_anomaly,
    bmkg_precip_mm = EXCLUDED.bmkg_precip_mm,
    bmkg_station_id = EXCLUDED.bmkg_station_id,
    bmkg_distance_km = EXCLUDED.bmkg_distance_km,
    ndvi = EXCLUDED.ndvi,
    ndvi_location = EXCLUDED.ndvi_location,
    ndvi_distance_km = EXCLUDED.ndvi_distance_km,
    sar_subsidence_mm = EXCLUDED.sar_subsidence_mm,
    sar_rate_mm_year = EXCLUDED.sar_rate_mm_year,
    drought_index = EXCLUDED.drought_index,
    risk_level = EXCLUDED.risk_level,
    data_completeness = EXCLUDED.data_completeness
"""


def get_grace_grid_points(cur, start_year, end_year):
    """Return distinct (lat, lon) from grace_tws within year range."""
    cur.execute("""
        SELECT DISTINCT lat, lon
        FROM grace_tws
        WHERE EXTRACT(YEAR FROM period_date) BETWEEN %s AND %s
        ORDER BY lat, lon
    """, (start_year, end_year))
    return [(float(r[0]), float(r[1])) for r in cur.fetchall()]


def get_grace_records(cur, start_year, end_year):
    """Return all grace_tws records keyed by (lat, lon, period_date)."""
    cur.execute("""
        SELECT lat, lon, period_date, tws_anomaly, uncertainty
        FROM grace_tws
        WHERE EXTRACT(YEAR FROM period_date) BETWEEN %s AND %s
    """, (start_year, end_year))
    records = {}
    for lat, lon, pd, tws, unc in cur.fetchall():
        records[(float(lat), float(lon), pd)] = (float(tws) if tws else None, float(unc) if unc else None)
    return records


def get_gldas_sms(cur, start_year, end_year):
    """Return gldas_sms keyed by (lat, lon, year, month)."""
    cur.execute("""
        SELECT lat, lon, year, month, sms_anomaly
        FROM gldas_sms
        WHERE year BETWEEN %s AND %s
    """, (start_year, end_year))
    records = {}
    for lat, lon, yr, mo, sms in cur.fetchall():
        records[(float(lat), float(lon), yr, mo)] = float(sms) if sms else None
    return records


def find_nearest_gldas(gldas_map, lat, lon, year, month):
    """Find GLDAS SMS with spatial + temporal match (ABS lat diff < 0.01)."""
    for (glat, glon, gyear, gmonth), sms in gldas_map.items():
        if gyear == year and gmonth == month and abs(glat - lat) < 0.01 and abs(glon - lon) < 0.01:
            return sms
    return None


def get_chirps_precip(cur, start_year, end_year):
    """Return chirps_precip keyed by (lat, lon, year, month)."""
    cur.execute("""
        SELECT lat, lon, year, month, precip_mm
        FROM chirps_precip
        WHERE year BETWEEN %s AND %s
    """, (start_year, end_year))
    records = {}
    for lat, lon, yr, mo, precip in cur.fetchall():
        records[(float(lat), float(lon), yr, mo)] = float(precip) if precip else None
    return records


def find_nearest_chirps(chirps_map, lat, lon, year, month):
    """Find CHIRPS with spatial match (ABS lat/lon diff < 0.1)."""
    for (clat, clon, cyr, cmo), precip in chirps_map.items():
        if cyr == year and cmo == month and abs(clat - lat) < 0.1 and abs(clon - lon) < 0.1:
            return precip
    return None


def get_chirps_baseline(cur):
    """Compute baseline mean precipitation per grid cell from chirps (2004-2009)."""
    cur.execute("""
        SELECT lat, lon, AVG(precip_mm)
        FROM chirps_precip
        WHERE year BETWEEN 2004 AND 2009
        GROUP BY lat, lon
    """)
    baseline = {}
    for lat, lon, avg in cur.fetchall():
        if avg is not None:
            baseline[(float(lat), float(lon))] = float(avg)
    return baseline


def find_nearest_chirps_baseline(baseline, lat, lon):
    """Find baseline mean with spatial tolerance 0.1 deg."""
    for (blat, blon), avg in baseline.items():
        if abs(blat - lat) < 0.1 and abs(blon - lon) < 0.1:
            return avg
    return None


def get_bmkg_stations(cur):
    """Return all BMKG stations with their coordinates."""
    cur.execute("""
        SELECT DISTINCT station_id, lat, lon
        FROM bmkg_rainfall
    """)
    return [(r[0], float(r[1]), float(r[2])) for r in cur.fetchall()]


def get_bmkg_rainfall(cur, start_year, end_year):
    """Return bmkg_rainfall keyed by (station_id, date)."""
    cur.execute("""
        SELECT station_id, date, precip_mm
        FROM bmkg_rainfall
        WHERE EXTRACT(YEAR FROM date) BETWEEN %s AND %s
    """, (start_year, end_year))
    records = {}
    for sid, dt, precip in cur.fetchall():
        records[(sid, dt)] = float(precip) if precip else None
    return records


def find_nearest_bmkg(bmkg_stations, bmkg_map, lat, lon, target_date):
    """Find nearest BMKG station on that date."""
    best = None
    best_dist = float("inf")
    for station_id, slat, slon in bmkg_stations:
        dist = ((slat - lat) ** 2 + (slon - lon) ** 2) ** 0.5
        if dist < best_dist:
            val = bmkg_map.get((station_id, target_date))
            if val is not None:
                best_dist = dist
                best = (station_id, val, dist)
    return best


def get_nearest_ndvi(cur, lat, lon, target_date):
    """Find nearest Sentinel-2 NDVI pixel using <-> operator."""
    cur.execute("""
        SELECT ndvi, location,
               ST_Distance(
                   geom::geography,
                   ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography
               ) / 1000.0 AS dist_km
        FROM sentinel2_ndvi
        WHERE period_date = %s
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
        LIMIT 1
    """, (lon, lat, target_date, lon, lat))
    row = cur.fetchone()
    if row:
        return float(row[0]) if row[0] else None, row[1], float(row[2]) if row[2] else None
    return None, None, None


def get_nearest_sar(cur, lat, lon):
    """Find nearest SAR subsidence point using <-> operator."""
    cur.execute("""
        SELECT displacement_mm, rate_mm_year
        FROM sar_subsidence
        ORDER BY geom <-> ST_SetSRID(ST_MakePoint(%s, %s), 4326)
        LIMIT 1
    """, (lon, lat))
    row = cur.fetchone()
    if row:
        return float(row[0]) if row[0] else None, float(row[1]) if row[1] else None
    return None, None


def main():
    args = parse_args()
    log.info(f"Unified sync: {args.start_year} - {args.end_year}")

    conn = psycopg2.connect(args.db_url)
    try:
        with conn.cursor() as cur:
            log.info("Loading grace_tws data...")
            grace_records = get_grace_records(cur, args.start_year, args.end_year)
            log.info(f"  {len(grace_records)} grace records loaded")

            log.info("Loading gldas_sms data...")
            gldas_map = get_gldas_sms(cur, args.start_year, args.end_year)
            log.info(f"  {len(gldas_map)} gldas records loaded")

            log.info("Loading chirps_precip data...")
            chirps_map = get_chirps_precip(cur, args.start_year, args.end_year)
            log.info(f"  {len(chirps_map)} chirps records loaded")

            log.info("Computing chirps baseline (2004-2009)...")
            chirps_baseline = get_chirps_baseline(cur)
            log.info(f"  {len(chirps_baseline)} baseline grid cells")

            log.info("Loading BMKG rainfall data...")
            bmkg_stations = get_bmkg_stations(cur)
            bmkg_map = get_bmkg_rainfall(cur, args.start_year, args.end_year)
            log.info(f"  {len(bmkg_stations)} BMKG stations, {len(bmkg_map)} rainfall records")

        # Build unified records
        total = len(grace_records)
        log.info(f"Processing {total} grace records into unified_monitoring...")
        batch = []
        count = 0

        with conn.cursor() as cur:
            for (lat, lon, period_date), (tws_anomaly, tws_uncertainty) in grace_records.items():
                year = period_date.year
                month = period_date.month

                # GLDAS SMS — spatial + temporal match
                sms_anomaly = find_nearest_gldas(gldas_map, lat, lon, year, month)

                # GWS anomaly
                gws_anomaly = tws_anomaly - (sms_anomaly if sms_anomaly is not None else 0.0)

                # CHIRPS
                chirps_precip = find_nearest_chirps(chirps_map, lat, lon, year, month)
                baseline_mean = find_nearest_chirps_baseline(chirps_baseline, lat, lon)
                chirps_anomaly = None
                if chirps_precip is not None and baseline_mean is not None:
                    chirps_anomaly = chirps_precip - baseline_mean

                # BMKG nearest station
                bmkg_result = find_nearest_bmkg(bmkg_stations, bmkg_map, lat, lon, period_date)
                bmkg_precip, bmkg_station_id, bmkg_distance_km = None, None, None
                if bmkg_result:
                    bmkg_station_id, bmkg_precip, bmkg_distance_km = bmkg_result

                # NDVI nearest pixel
                ndvi, ndvi_location, ndvi_distance_km = get_nearest_ndvi(cur, lat, lon, period_date)

                # SAR nearest point
                sar_subsidence, sar_rate = get_nearest_sar(cur, lat, lon)

                # Drought index
                gws_norm = normalize(gws_anomaly, -5, 5)
                rain_norm = normalize(chirps_anomaly, -200, 200) if chirps_anomaly is not None else 0.5
                ndvi_norm = normalize(ndvi, 0, 0.8) if ndvi is not None else 0.5
                drought_index = 0.4 * gws_norm + 0.3 * rain_norm + 0.3 * ndvi_norm

                # Risk level
                risk = risk_level_from_drought_index(drought_index)

                # Data completeness (6 possible sensors)
                sensor_count = sum([
                    tws_anomaly is not None,
                    sms_anomaly is not None,
                    chirps_precip is not None,
                    bmkg_precip is not None,
                    ndvi is not None,
                    sar_subsidence is not None,
                ])
                data_completeness = sensor_count / 6.0

                batch.append({
                    "period_date": period_date,
                    "lat": lat,
                    "lon": lon,
                    "tws_anomaly": tws_anomaly,
                    "tws_uncertainty": tws_uncertainty,
                    "sms_anomaly": sms_anomaly,
                    "gws_anomaly": round(gws_anomaly, 4) if gws_anomaly is not None else None,
                    "chirps_precip_mm": chirps_precip,
                    "chirps_anomaly": round(chirps_anomaly, 4) if chirps_anomaly is not None else None,
                    "bmkg_precip_mm": bmkg_precip,
                    "bmkg_station_id": bmkg_station_id,
                    "bmkg_distance_km": round(bmkg_distance_km, 2) if bmkg_distance_km is not None else None,
                    "ndvi": ndvi,
                    "ndvi_location": ndvi_location,
                    "ndvi_distance_km": round(ndvi_distance_km, 2) if ndvi_distance_km is not None else None,
                    "sar_subsidence_mm": sar_subsidence,
                    "sar_rate_mm_year": sar_rate,
                    "drought_index": round(drought_index, 4) if drought_index is not None else None,
                    "risk_level": risk,
                    "grid_resolution": "0.5deg",
                    "data_completeness": round(data_completeness, 2),
                })

                if len(batch) >= 100:
                    psycopg2.extras.execute_batch(cur, UPSERT_SQL, batch, page_size=100)
                    conn.commit()
                    count += len(batch)
                    log.info(f"  {count}/{total} records committed")
                    batch = []

            if batch:
                psycopg2.extras.execute_batch(cur, UPSERT_SQL, batch, page_size=100)
                conn.commit()
                count += len(batch)

        log.info(f"Done. {count} unified records upserted.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
