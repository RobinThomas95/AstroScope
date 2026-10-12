"""HTTP endpoints for Module 4 (MUSE spectra, spectral lines, HI profile)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.spectra import HIProfile, SpectralLine, Spectrum1D
from app.services.hi import get_hi_matcher
from app.services.hiprofile import profile_from_detection
from app.services.lines import spectral_lines
from app.services.muse import MAX_CUTOUT_RADIUS_ARCSEC, _allowed, get_muse_service

router = APIRouter(prefix="/api", tags=["spectra"])


@router.get("/spectrum/muse", response_model=Spectrum1D)
async def muse_spectrum(
    datalink_url: str = Query(..., description="ESO DataLink URL of the cube (ObsCore access_url)"),
    ra: float = Query(..., ge=0.0, lt=360.0),
    dec: float = Query(..., ge=-90.0, le=90.0),
    radius_arcsec: float = Query(3.0, gt=0.0, le=MAX_CUTOUT_RADIUS_ARCSEC),
    method: str = Query("sum", pattern="^(sum|mean)$"),
    band_min_A: float | None = Query(None, gt=0),
    band_max_A: float | None = Query(None, gt=0),
) -> Spectrum1D:
    if not _allowed(datalink_url):
        raise HTTPException(status_code=400, detail="datalink_url must point at the ESO archive")
    band = None
    if band_min_A is not None and band_max_A is not None:
        if band_max_A <= band_min_A:
            raise HTTPException(status_code=400, detail="band_max_A must exceed band_min_A")
        band = (band_min_A, band_max_A)
    try:
        return await run_in_threadpool(
            get_muse_service().spectrum, datalink_url, ra, dec, radius_arcsec, method, band
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - remote service / FITS problems
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}"[:500]) from exc


@router.get("/lines", response_model=list[SpectralLine])
async def lines(
    z: float | None = Query(None, ge=-0.01, le=20.0, description="Redshift to apply"),
    wmin: float | None = Query(None, description="Observed window start, Angstrom"),
    wmax: float | None = Query(None, description="Observed window end, Angstrom"),
) -> list[SpectralLine]:
    return spectral_lines(z, wmin, wmax)


@router.get("/hi/profile", response_model=HIProfile)
async def hi_profile(
    ra: float = Query(..., ge=0.0, lt=360.0),
    dec: float = Query(..., ge=-90.0, le=90.0),
    radius_arcmin: float | None = Query(None, gt=0.0, le=60.0),
    source_id: str | None = Query(None, description="Pick a specific catalogue source"),
) -> HIProfile:
    """A template 21 cm profile built from the nearest catalogued HI detection."""
    search = await run_in_threadpool(get_hi_matcher().search, ra, dec, radius_arcmin)
    detections = [d for r in search.results for d in r.detections]
    if source_id:
        detections = [d for d in detections if d.source_id == source_id]
    usable = [d for d in detections if d.velocity_kms is not None and d.w50_kms]
    if not usable:
        raise HTTPException(status_code=404, detail="No HI detection with velocity and W50 nearby")
    # Prefer entries with an integrated flux and a W20, then the closest.
    usable.sort(
        key=lambda d: (
            d.flux_jy_kms is None,
            d.w20_kms is None,
            d.separation_arcmin if d.separation_arcmin is not None else 1e9,
        )
    )
    return profile_from_detection(usable[0])
