#!/usr/bin/env python3
"""Load Sumur_NTB.csv into PostGIS — replaces wells_esdm with real data."""
import os
import sys
import csv
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://rizki:ntb_env_2024@localhost:5435/ntb_groundwater")


def load(csv_path="data/Sumur_NTB.csv"):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Create enhanced wells_esdm table if not exists
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wells_esdm (
            id SERIAL PRIMARY KEY,
            kode_sumur VARCHAR(20) UNIQUE,
            fungsi VARCHAR(50),
            lat NUMERIC(10,6),
            lon NUMERIC(10,6),
            dusun VARCHAR(100),
            desa VARCHAR(100),
            kecamatan VARCHAR(100),
            kabupaten VARCHAR(100),
            dibangun_oleh VARCHAR(100),
            kedalaman_m NUMERIC(8,2),
            debit_lps NUMERIC(8,2),
            tahun_pembangunan INTEGER,
            geom GEOMETRY(Point, 4326)
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wells_esdm_geom ON wells_esdm USING GIST(geom);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wells_esdm_kab ON wells_esdm(kabupaten);")

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        count = 0
        skipped = 0

        for row in reader:
            kode = row['Kode Sumur'].strip()
            fungsi = row['Fungsi'].strip()
            lat_str = row['Latitude'].strip()
            lon_str = row['Longitude'].strip()
            dusun = row.get('Alamat (Dusun)', '').strip() or None
            desa = row.get('Desa', '').strip() or None
            kecamatan = row.get('Kecamatan', '').strip() or None
            kabupaten = row.get('Kabupaten/Kota', '').strip() or None
            dibangun = row.get('Dibangun Oleh', '').strip() or None
            kedalaman = row.get('Kedalaman (Meter)', '0').strip()
            debit = row.get('Debit Pengambilan (Liter/Detik)', '0').strip()
            tahun = row.get('Tahun Pembangunan', '').strip()

            # Skip rows without valid coordinates
            try:
                lat = float(lat_str)
                lon = float(lon_str)
                if lat == 0 or lon == 0:
                    skipped += 1
                    continue
                # Validate coordinate range for NTB
                if not (-10 < lat < -7 or 115 < lon < 120):
                    skipped += 1
                    continue
            except (ValueError, TypeError):
                skipped += 1
                continue

            kedalaman_val = float(kedalaman) if kedalaman and kedalaman != '0' else None
            debit_val = float(debit) if debit and debit != '0' else None
            tahun_val = int(float(tahun)) if tahun else None

            cur.execute("""
                INSERT INTO wells_esdm (kode_sumur, fungsi, lat, lon, dusun, desa, kecamatan, kabupaten,
                                        dibangun_oleh, kedalaman_m, debit_lps, tahun_pembangunan, geom)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                ON CONFLICT (kode_sumur) DO UPDATE SET
                    fungsi = EXCLUDED.fungsi,
                    lat = EXCLUDED.lat,
                    lon = EXCLUDED.lon,
                    dusun = EXCLUDED.dusun,
                    desa = EXCLUDED.desa,
                    kecamatan = EXCLUDED.kecamatan,
                    kabupaten = EXCLUDED.kabupaten,
                    dibangun_oleh = EXCLUDED.dibangun_oleh,
                    kedalaman_m = EXCLUDED.kedalaman_m,
                    debit_lps = EXCLUDED.debit_lps,
                    tahun_pembangunan = EXCLUDED.tahun_pembangunan,
                    geom = EXCLUDED.geom
            """, (kode, fungsi, lat, lon, dusun, desa, kecamatan, kabupaten,
                  dibangun, kedalaman_val, debit_val, tahun_val, lon, lat))
            count += 1

    conn.commit()

    # Also promote to wells table for monitoring
    cur.execute("""
        INSERT INTO wells (well_code, name, kecamatan, kabupaten, well_type, depth_m, aquifer_type, geom)
        SELECT kode_sumur, COALESCE(desa, dusun, kode_sumur), kecamatan, kabupaten,
               CASE WHEN fungsi LIKE '%%Irigasi%%' THEN 'irigasi' ELSE 'monitoring' END,
               COALESCE(kedalaman_m, 50.0), 'bebas', geom
        FROM wells_esdm
        ON CONFLICT (well_code) DO NOTHING;
    """)

    conn.commit()
    cur.close()
    conn.close()
    print(f"Loaded {count} wells ({skipped} skipped due to invalid coords)")


if __name__ == "__main__":
    csv_path = sys.argv[1] if len(sys.argv) > 1 else "data/Sumur_NTB.csv"
    load(csv_path)
