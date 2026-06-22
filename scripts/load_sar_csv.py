#!/usr/bin/env python3
"""Load SAR subsidence CSV into PostGIS."""
import os
import sys
import csv
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@localhost:5435/ntb_groundwater")

def load(csv_path):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        count = 0
        for row in reader:
            cur.execute("""
                INSERT INTO sar_subsidence 
                    (location, kabupaten, lat, lon, period_start, period_end,
                     displacement_mm, rate_mm_year, n_observations, coherence, geom, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326), 'sentinel1_gee')
                ON CONFLICT (location, period_start, period_end) DO UPDATE SET
                    displacement_mm = EXCLUDED.displacement_mm,
                    rate_mm_year = EXCLUDED.rate_mm_year,
                    n_observations = EXCLUDED.n_observations
            """, (
                row['location'], row['kabupaten'],
                float(row['lat']), float(row['lon']),
                row['period_start'], row['period_end'],
                float(row['displacement_mm']) if row.get('displacement_mm') else None,
                float(row['rate_mm_year']) if row.get('rate_mm_year') else None,
                int(row.get('n_observations', 0)),
                float(row.get('coherence', 0)),
                float(row['lon']), float(row['lat'])
            ))
            count += 1
    
    conn.commit()
    cur.close()
    conn.close()
    print(f"Loaded {count} SAR records")

if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "data/sar/sar_subsidence_ntb.csv"
    load(csv_path)
