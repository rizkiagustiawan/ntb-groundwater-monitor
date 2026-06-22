#!/usr/bin/env python3
"""Master script: download all real data sources for NTB Groundwater Monitor."""
import subprocess
import sys
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

scripts = [
    ("CHIRPS Precipitation", "scripts/download_chirps.py"),
    ("BMKG Rainfall", "scripts/download_bmkg.py"),
    ("SAR Subsidence", "scripts/download_sar.py"),
]

def run(name, script):
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"  {script}")
    print(f"{'='*60}")
    result = subprocess.run([sys.executable, script], capture_output=False)
    return result.returncode == 0

success = 0
for name, script in scripts:
    if os.path.exists(script):
        if run(name, script):
            success += 1
    else:
        print(f"SKIP: {script} not found")

print(f"\n{'='*60}")
print(f"  Downloaded: {success}/{len(scripts)} sources")
print(f"{'='*60}")
