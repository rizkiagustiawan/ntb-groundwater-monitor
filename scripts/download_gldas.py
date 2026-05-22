#!/usr/bin/env python3
import argparse
import os
import ssl
import urllib.request
from pathlib import Path
from tqdm import tqdm
import time

# Configuration
BASE_URL = "https://hydro1.gesdisc.eosdis.nasa.gov/data/GLDAS/GLDAS_NOAH025_M.2.1"
OUTPUT_DIR = Path("data/gldas")
DEFAULT_TOKEN = os.getenv("EARTHDATA_TOKEN", "")

def get_url(year, month):
    # Pattern: GLDAS_NOAH025_M.A202401.021.nc4
    filename = f"GLDAS_NOAH025_M.A{year}{month:02d}.021.nc4"
    return f"{BASE_URL}/{year}/{filename}"

def download_file_urllib(url, token, retries=3):
    filename = url.split("/")[-1]
    save_path = OUTPUT_DIR / filename

    if save_path.exists():
        return f"Skipped: {filename} (exists)"

    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    
    req = urllib.request.Request(url, headers=headers)
    
    # Create a custom SSL context to be more permissive if needed
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE # For debugging SSL EOF issues

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, context=context, timeout=60) as response:
                if response.getcode() == 200:
                    with open(save_path, "wb") as f:
                        f.write(response.read())
                    return f"Downloaded: {filename}"
                else:
                    return f"Error: Status {response.getcode()} for {filename}"
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return f"Exception for {filename} after {retries} attempts: {str(e)}"

def main():
    parser = argparse.ArgumentParser(description="Download GLDAS Noah 2.1 Monthly.")
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--years", nargs="+", type=int)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--current", action="store_true")
    args = parser.parse_args()

    if not args.token:
        print("Error: No NASA token provided.")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    years = args.years if args.years else []
    if args.baseline: years.extend(range(2004, 2010))
    if args.current: years.extend(range(2020, 2026))
    years = sorted(list(set(years)))

    if not years:
        print("No years specified.")
        return

    urls = []
    for y in years:
        for m in range(1, 13):
            urls.append(get_url(y, m))

    print(f"Downloading {len(urls)} files...")
    for url in tqdm(urls, desc="Files"):
        res = download_file_urllib(url, args.token)
        if "Error" in res or "Exception" in res:
            print(f"  {res}")

if __name__ == "__main__":
    main()
