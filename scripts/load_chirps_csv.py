#!/usr/bin/env python3
"""Load CHIRPS precipitation CSV into PostGIS."""
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
            lat = float(row['lat'])
            lon = float(row['lon'])
            year = int(row['year'])
            month = int(row['month'])
            precip = float(row['precip_mm']) if row.get('precip_mm') else None
            
            if precip is None:
                continue
            
            cur.execute("""
                INSERT INTO chirps_precip (lat, lon, year, month, precip_mm)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (lat, lon, year, month) DO UPDATE SET precip_mm = EXCLUDED.precip_mm
            """, (lat, lon, year, month, precip))
            count += 1
            
            if count % 1000 == 0:
                conn.commit()
                print(f"  {count} records...")
    
    conn.commit()
    cur.close()
    conn.close()
    print(f"Loaded {count} CHIRPS records")

if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "data/chirps/chirps_ntb.csv"
    load(csv_path)
