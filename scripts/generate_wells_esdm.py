#!/usr/bin/env python3
"""Generate wells_esdm.sql from data/Sumur_NTB.csv."""
import csv
import sys

INPUT = "data/Sumur_NTB.csv"
OUTPUT = "scripts/wells_esdm.sql"


def utm_to_dd(easting, northing, zone=50, south=True):
    """Convert UTM to WGS84 decimal degrees (Zone 50S for NTB)."""
    import math
    a = 6378137.0
    f = 1 / 298.257223563
    e = math.sqrt(2 * f - f * f)
    k0 = 0.9996
    e2 = e * e / (1 - e * e)
    
    if south:
        northing -= 10000000
    
    lon0 = (zone - 1) * 6 - 180 + 3
    M = northing / k0
    mu = M / (a * (1 - e2/4 - 3*e2**2/64 - 5*e2**3/256))
    
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    J1 = 3*e1/2 - 27*e1**3/32
    J2 = 21*e1**2/16 - 55*e1**4/32
    J3 = 151*e1**3/96
    J4 = 1097*e1**4/512
    
    fp = mu + J1*math.sin(2*mu) + J2*math.sin(4*mu) + J3*math.sin(6*mu) + J4*math.sin(8*mu)
    
    e2_2 = e**2 / (1 - e**2)
    C1 = e2_2 * math.cos(fp)**2
    T1 = math.tan(fp)**2
    N1 = a / math.sqrt(1 - e**2 * math.sin(fp)**2)
    R1 = a * (1 - e**2) / (1 - e**2 * math.sin(fp)**2)**1.5
    D = (easting - 500000) / (N1 * k0)
    
    Q1 = N1 * math.tan(fp) / R1
    Q2 = (D**2 / 2)
    Q3 = (5 + 3*T1 + 10*C1 - 4*C1**2 - 9*e2_2) * D**4 / 24
    Q4 = (61 + 90*T1 + 298*C1 + 45*T1**2 - 252*e2_2 - 3*C1**2) * D**6 / 720
    
    lat = fp - Q1 * (Q2 - Q3 + Q4)
    lat_deg = math.degrees(lat)
    
    Q5 = D
    Q6 = (1 + 2*T1 + C1) * D**3 / 6
    Q7 = (5 - 2*C1 + 28*T1 - 3*C1**2 + 8*e2_2 + 24*T1**2) * D**5 / 120
    
    lon = lon0 + math.degrees((Q5 - Q6 + Q7) / math.cos(fp))
    
    return round(lat_deg, 6), round(lon, 6)


def is_valid_coord(lat_str, lon_str):
    """Check if coordinates are valid decimal degrees for NTB region."""
    try:
        lat_f = float(lat_str)
        lon_f = float(lon_str)
        return -10 <= lat_f <= -7 and 115 <= lon_f <= 120
    except (ValueError, TypeError):
        return False


def is_utm(lat_str, lon_str):
    """Check if coordinates look like UTM (northing > 100000, reasonable range)."""
    try:
        lat_f = float(lat_str)
        lon_f = float(lon_str)
        # Valid UTM: easting 100000-900000, northing 8000000-10000000 (for NTB)
        return 100000 <= lat_f <= 900000 and 8000000 <= lon_f <= 10000000
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
    utm_converted = 0

    with open(INPUT, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lat = row.get("Latitude", "").strip()
            lon = row.get("Longitude", "").strip()

            if not lat or not lon or lat == "-" or lon == "-":
                skipped += 1
                continue

            # Check if decimal degrees
            if is_valid_coord(lat, lon):
                lat_f = float(lat)
                lon_f = float(lon)
            # Check if UTM and convert
            elif is_utm(lat, lon):
                try:
                    lat_f, lon_f = utm_to_dd(float(lat), float(lon))
                    if not (-10 <= lat_f <= -7 and 115 <= lon_f <= 120):
                        skipped += 1
                        continue
                    utm_converted += 1
                except Exception:
                    skipped += 1
                    continue
            else:
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

    print(f"Generated {OUTPUT}: {len(rows)} wells ({utm_converted} UTM converted, {skipped} skipped)")


if __name__ == "__main__":
    main()
