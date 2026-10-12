"""AstroScope FastAPI application."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.routers import archives, resolver, spectra, surveys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = get_settings()

app = FastAPI(
    title="AstroScope API",
    version=__version__,
    description="Astronomical target reconnaissance and multi-wavelength analysis",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(resolver.router)
app.include_router(surveys.router)
app.include_router(archives.router)
app.include_router(spectra.router)


@app.get("/api/health", tags=["meta"])
async def health() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "modules": {
            "resolver": True,
            "viewer": True,
            "archives": True,
            "spectra": True,
            "literature": bool(settings.ads_dev_key),
            "synthesis": bool(settings.anthropic_api_key or settings.openai_api_key),
        },
    }
