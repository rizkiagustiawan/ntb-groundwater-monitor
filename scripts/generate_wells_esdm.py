#!/usr/bin/env python3
"""Generate wells_esdm.sql from data/Sumur_NTB.csv."""
import csv
import sys

INPUT = "data/Sumur_NTB.csv"
OUTPUT = "scripts/wells_esdm.sql"


def is_valid_coord(lat, lon):
    """Check if coordinates are valid decimal degrees for NTB region."""
    try:
        lat_f = float(lat)
        lon_f = float(lon)
        return -10 <= lat_f <= -7 and 115 <= lon_f <= 120
    except (ValueError, TypeError):
        return False


def escape_sql(val):
    """Escape single quotes for SQL string literals."""
    if val is None:
        return "NULL"
    return "'" + str(val).replace("'", "''") + "'"


def main():
    rows = []
    skipped = 0

    with open(INPUT, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lat = row.get("Latitude", "").strip()
            lon = row.get("Longitude", "").strip()

            if not is_valid_coord(lat, lon):
                skipped += 1
                continue

            kode = row.get("Kode Sumur", "").strip()
            fungsi = row.get("Fungsi", "").strip()
            dusun = row.get("Alamat (Dusun)", "").strip()
            desa = row.get("Desa", "").strip()
            kecamatan = row.get("Kecamatan", "").strip()
            kabupaten = row.get("Kabupaten/Kota", "").strip()
            dibangun = row.get("Dibangun Oleh", "").strip()

            kedalaman = row.get("Kedalaman (Meter)", "").strip()
            debit = row.get("Debit Pengambilan (Liter/Detik)", "").strip()
            tahun = row.get("Tahun Pembangunan", "").strip()

            # Convert numeric fields
            kedalaman_val = None
            if kedalaman:
                try:
                    k = float(kedalaman)
                    kedalaman_val = k if k != 0 else None
                except ValueError:
                    pass

            debit_val = None
            if debit:
                try:
                    d = float(debit)
                    debit_val = d if d != 0 else None
                except ValueError:
                    pass

            tahun_val = None
            if tahun:
                try:
                    tahun_val = int(float(tahun))
                except ValueError:
                    pass

            lat_f = float(lat)
            lon_f = float(lon)

            rows.append({
                "kode": kode,
                "fungsi": fungsi,
                "lat": lat_f,
                "lon": lon_f,
                "dusun": dusun,
                "desa": desa,
                "kecamatan": kecamatan,
                "kabupaten": kabupaten,
                "dibangun": dibangun,
                "kedalaman": kedalaman_val,
                "debit": debit_val,
                "tahun": tahun_val,
            })

    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write("""CREATE TABLE IF NOT EXISTS wells_esdm (
    id SERIAL PRIMARY KEY,
    kode_sumur VARCHAR(20) UNIQUE,
    fungsi VARCHAR(50),
    lat NUMERIC(10,6), lon NUMERIC(10,6),
    dusun VARCHAR(100), desa VARCHAR(100),
    kecamatan VARCHAR(100), kabupaten VARCHAR(100),
    dibangun_oleh VARCHAR(100),
    kedalaman_m NUMERIC(8,2),
    debit_lps NUMERIC(8,2),
    tahun_pembangunan INTEGER,
    geom GEOMETRY(Point, 4326)
);
CREATE INDEX IF NOT EXISTS idx_wells_esdm_geom ON wells_esdm USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_wells_esdm_kab ON wells_esdm(kabupaten);
INSERT INTO wells_esdm (kode_sumur,fungsi,lat,lon,dusun,desa,kecamatan,kabupaten,dibangun_oleh,kedalaman_m,debit_lps,tahun_pembangunan,geom) VALUES
""")

        for i, r in enumerate(rows):
            kedalaman_str = str(r["kedalaman"]) if r["kedalaman"] is not None else "NULL"
            debit_str = str(r["debit"]) if r["debit"] is not None else "NULL"
            tahun_str = str(r["tahun"]) if r["tahun"] is not None else "NULL"
            sep = "," if i < len(rows) - 1 else " ON CONFLICT DO NOTHING;"
            f.write(
                f"  ({escape_sql(r['kode'])},{escape_sql(r['fungsi'])},"
                f"{r['lat']},{r['lon']},"
                f"{escape_sql(r['dusun'])},{escape_sql(r['desa'])},"
                f"{escape_sql(r['kecamatan'])},{escape_sql(r['kabupaten'])},"
                f"{escape_sql(r['dibangun'])},{kedalaman_str},{debit_str},{tahun_str},"
                f"ST_SetSRID(ST_MakePoint({r['lon']},{r['lat']}),4326)){sep}\n"
            )

    print(f"Generated {OUTPUT}: {len(rows)} wells (skipped {skipped} invalid rows)")


if __name__ == "__main__":
    main()
