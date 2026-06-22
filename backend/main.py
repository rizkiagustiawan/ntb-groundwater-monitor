"""
NTB Groundwater Monitoring API
Landasan hukum: PP No. 43 Tahun 2008 tentang Air Tanah
Referensi ilmiah: NASA GRACE RL06 Mascon Solutions
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from app.db import get_pool, close_pool
from app.routers import groundwater, grace, climate
from app.routers.wells import router as wells_router
from app.routers.ndvi import router as ndvi_router
from app.routers.summary import router as summary_router
from app.routers.health import router as health_router
from app.routers.ai import router as ai_router
from app.routers.report import router as report_router
from app.routers.bmkg import router as bmkg_router
from app.routers.sar import router as sar_router
from app.routers.fusion import router as fusion_router
from app.routers.drought import router as drought_router
from app.routers.chirps import router as chirps_router

ALLOWED_ORIGINS = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3003,http://13.236.148.26:3003,https://gw.rizkiagustiawan.tech"
).split(",")

app = FastAPI(
    title="NTB Groundwater Monitoring API",
    description="Platform monitoring air tanah Nusa Tenggara Barat berbasis satelit NASA GRACE dan data lapangan. Referensi: PP 43/2008, Perpres 33/2018.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(groundwater.router)
app.include_router(grace.router)
app.include_router(climate.router)
app.include_router(wells_router)
app.include_router(ndvi_router)
app.include_router(summary_router)
app.include_router(health_router)
app.include_router(ai_router)
app.include_router(report_router)
app.include_router(bmkg_router)
app.include_router(sar_router)
app.include_router(fusion_router)
app.include_router(drought_router)
app.include_router(chirps_router)


@app.get("/")
async def root():
    return {
        "platform": "NTB Groundwater Monitoring",
        "version": "1.0.0",
        "legal_basis": ["PP No. 43 Tahun 2008", "Perpres No. 33 Tahun 2018", "PerMenLHK P.68/2016"],
        "data_sources": ["NASA GRACE RL06 Mascon", "Sentinel-2 MSI", "Data lapangan ESDM NTB"],
        "coverage": "Nusa Tenggara Barat, Indonesia",
        "docs": "/docs"
    }


@app.on_event("startup")
async def startup():
    await get_pool()


@app.on_event("shutdown")
async def shutdown():
    await close_pool()
