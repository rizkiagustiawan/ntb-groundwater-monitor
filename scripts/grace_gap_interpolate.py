#!/usr/bin/env python3
"""GRACE Gap Interpolation — fill 2017-2018 gap using surrounding data."""
import os
import sys
import logging
import numpy as np
import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@localhost:5435/ntb_groundwater")

GAP_START = "2017-07-01"
GAP_END = "2018-05-01"


def interpolate_gap(conn):
    """Linear interpolation for missing GRACE months."""
    cur = conn.cursor()

    # Get all grid points
    cur.execute("SELECT DISTINCT lat, lon FROM grace_tws ORDER BY lat, lon")
    grid_points = cur.fetchall()
    log.info(f"Interpolating gap for {len(grid_points)} grid points")

    total_inserted = 0

    for lat, lon in grid_points:
        # Get pre-gap data (last 6 months before gap)
        cur.execute("""
            SELECT period_date, tws_anomaly, uncertainty
            FROM grace_tws
            WHERE lat = %s AND lon = %s AND period_date < %s
            ORDER BY period_date DESC LIMIT 6
        """, (lat, lon, GAP_START))
        pre_gap = cur.fetchall()

        # Get post-gap data (first 6 months after gap)
        cur.execute("""
            SELECT period_date, tws_anomaly, uncertainty
            FROM grace_tws
            WHERE lat = %s AND lon = %s AND period_date >= %s
            ORDER BY period_date ASC LIMIT 6
        """, (lat, lon, GAP_END))
        post_gap = cur.fetchall()

        if not pre_gap or not post_gap:
            continue

        pre_values = [(r[1], r[2]) for r in pre_gap]
        post_values = [(r[1], r[2]) for r in post_gap]

        # Generate gap months
        from datetime import datetime, timedelta
        from dateutil.relativedelta import relativedelta

        gap_start = datetime(2017, 7, 1)
        gap_end = datetime(2018, 4, 1)

        current = gap_start
        gap_months = []
        while current <= gap_end:
            gap_months.append(current)
            current += relativedelta(months=1)

        n_gap = len(gap_months)
        n_pre = len(pre_values)
        n_post = len(post_values)

        for i, month in enumerate(gap_months):
            # Weighted interpolation: closer to pre-gap = more weight from pre
            t = (i + 1) / (n_gap + 1)  # 0 to 1, where 0 = closer to pre

            pre_avg = np.mean([v[0] for v in pre_values[:3]])
            post_avg = np.mean([v[0] for v in post_values[:3]])

            interpolated = pre_avg * (1 - t) + post_avg * t
            uncertainty = abs(post_avg - pre_avg) * 0.5  # Higher uncertainty for interpolated

            # Insert with flag
            cur.execute("""
                INSERT INTO grace_tws (period_date, lat, lon, tws_anomaly, uncertainty, geom)
                VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                ON CONFLICT (period_date, lat, lon) DO UPDATE SET
                    tws_anomaly = EXCLUDED.tws_anomaly,
                    uncertainty = EXCLUDED.uncertainty
            """, (month, lat, lon, float(interpolated), float(uncertainty), lon, lat))

            total_inserted += 1

    conn.commit()
    cur.close()
    log.info(f"Interpolated {total_inserted} gap records")


def main():
    conn = psycopg2.connect(DATABASE_URL)
    interpolate_gap(conn)
    conn.close()
    log.info("Gap interpolation complete")


if __name__ == "__main__":
    main()
