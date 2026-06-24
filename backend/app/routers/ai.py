import os
import requests
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.db import get_pool
from app.utils import classify_ndvi, format_period_label
from app.queries import get_latest_ndvi_rows

router = APIRouter(tags=["ai"])

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-flash-lite-latest",
]


def call_gemini(prompt: str) -> str:
    """Call Gemini API with model fallback."""
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": GEMINI_API_KEY,
    }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024}
    }

    last_error = None
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            else:
                last_error = f"{model}: {resp.status_code}"
                continue
        except Exception as e:
            last_error = f"{model}: {str(e)}"
            continue

    raise Exception(f"All Gemini models failed. Last error: {last_error}")


@router.get("/ai/interpret")
async def ai_interpret_ntb():
    """
    Interpretasi otomatis kondisi air tanah NTB menggunakan AI (Gemini).
    Menggabungkan data GWS (TWS - SMS) + Curah Hujan + NDVI + status sumur.
    """
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Ambil data terbaru GWS (TWS - SMS)
        gws_rows = await conn.fetch("""
            SELECT t.period_date,
                   ROUND(AVG(t.tws_anomaly - COALESCE(s.sms_anomaly, 0))::numeric, 2) AS avg_gws
            FROM grace_tws t
            LEFT JOIN gldas_sms s ON
                EXTRACT(YEAR FROM t.period_date) = s.year AND
                EXTRACT(MONTH FROM t.period_date) = s.month AND
                ABS(t.lat - s.lat) < 0.01 AND ABS(t.lon - s.lon) < 0.01
            GROUP BY t.period_date
            ORDER BY t.period_date DESC
            LIMIT 6
        """)

        # Ambil data curah hujan terbaru
        rain_rows = await conn.fetch("""
            SELECT year, month, ROUND(AVG(precip_mm)::numeric, 2) as avg_precip
            FROM chirps_precip
            GROUP BY year, month
            ORDER BY year DESC, month DESC
            LIMIT 6
        """)

        # Ambil snapshot NDVI terbaru per lokasi
        ndvi_rows = await get_latest_ndvi_rows(conn, ascending=True, limit=5)

        # Ambil ringkasan sumur
        well_rows = await conn.fetch("""
            SELECT kabupaten,
                   COUNT(*) FILTER (WHERE status_level='kritis' OR status_level='sangat_kritis') AS kritis,
                   COUNT(*) AS total
            FROM well_latest_status
            GROUP BY kabupaten
            ORDER BY kritis DESC
        """)

        # Susun konteks data untuk AI
        gws_summary = "\n".join([
            f"  {r['period_date'].strftime('%Y-%m')}: {r['avg_gws']:+.2f} cm EWH"
            for r in gws_rows
        ])

        rain_summary = "\n".join([
            f"  {r['year']}-{r['month']:02d}: {float(r['avg_precip']):.1f} mm"
            for r in rain_rows
        ])

        ndvi_summary = "\n".join([
            f"  {r['location']} ({r['kabupaten']}): NDVI terbaru {float(r['latest_ndvi']):.3f} pada {format_period_label(r['latest_period'])} - {classify_ndvi(float(r['latest_ndvi']), title_case=True)}"
            for r in ndvi_rows
        ])

        well_summary = "\n".join([
            f"  {r['kabupaten']}: {r['kritis']} dari {r['total']} sumur kritis"
            for r in well_rows
        ])

        prompt = f"""Kamu adalah Senior Environmental Engineer dengan spesialisasi hidrologi dan monitoring lingkungan di Indonesia.

Berikut adalah data monitoring air tanah Nusa Tenggara Barat (NTB) terkini:

DATA NASA GRACE + GLDAS - Anomali Groundwater Storage (GWS) regional (6 bulan terakhir):
{gws_summary}
(GWS = TWS - Soil Moisture. Nilai negatif = defisit simpanan air tanah regional.)

DATA CURAH HUJAN CHIRPS (6 bulan terakhir):
{rain_summary}
(Gunakan untuk melihat apakah defisit air tanah sejalan dengan kurangnya hujan atau indikasi pemompaan berlebih.)

DATA SENTINEL-2 NDVI - Snapshot vegetasi terbaru (5 lokasi paling kritis):
{ndvi_summary}

STATUS SUMUR PANTAU:
{well_summary}

Berikan interpretasi komprehensif dalam Bahasa Indonesia (maksimal 200 kata) yang mencakup:
1. Kondisi simpanan air tanah (GWS) regional NTB saat ini sehubungan dengan curah hujan
2. Hubungan indikatif antara kondisi vegetasi dan potensi tekanan sumber daya air tanah
3. Kabupaten/area yang paling memerlukan perhatian segera
4. Analisis potensi pengaruh antropogenik (jika hujan normal tapi GWS turun tajam)
5. Rekomendasi tindakan prioritas untuk Dinas ESDM NTB.

Referensikan PP No. 43 Tahun 2008."""

        try:
            interpretation = call_gemini(prompt)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Gemini API error: {str(e)}")

        return {
            "generated_at": datetime.now().isoformat(),
            "data_sources": [
                "NASA GRACE RL06.3 Mascon",
                "NASA GLDAS Noah 2.1",
                "UCSB-CHG CHIRPS",
                "Copernicus Sentinel-2 MSI",
                "Data Sumur Pantau NTB"
            ],
            "legal_reference": "PP No. 43 Tahun 2008",
            "ai_model": "gemini-flash-latest",
            "interpretation": interpretation,
            "raw_data": {
                "gws_6months": [
                    {"period": r['period_date'].strftime('%Y-%m'),
                     "gws_cm": float(r['avg_gws'])}
                    for r in gws_rows
                ],
                "ndvi_critical": [
                    {"location": r['location'],
                     "ndvi": float(r['latest_ndvi']),
                     "kondisi": classify_ndvi(float(r['latest_ndvi']), title_case=True),
                     "latest_period": format_period_label(r['latest_period'])}
                    for r in ndvi_rows
                ]
            }
        }
