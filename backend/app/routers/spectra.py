"""HTTP endpoints for Module 4 (MUSE spectra, spectral lines, HI profile)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.schemas.spectra import (
    CubeCacheStatus,
    CubeJobStatus,
    CubeProductOut,
    HIProfile,
    SpectralLine,
    Spectrum1D,
)
from app.services import cubes
from app.services.hi import get_hi_matcher
from app.services.hiprofile import profile_from_detection
from app.services.lines import radio_lines, spectral_lines
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


@router.get("/lines/radio")
async def lines_radio(
    z: float | None = Query(None, ge=-0.01, le=20.0),
    fmin_ghz: float | None = Query(None, gt=0),
    fmax_ghz: float | None = Query(None, gt=0),
) -> list[dict]:
    return radio_lines(z, fmin_ghz, fmax_ghz)


def _parse_spw_ranges(text: str | None) -> dict[int, tuple[float, float]]:
    """'23:85.300-87.200; 25:87.200-89.000' -> {23: (85.3, 87.2), ...}"""
    out: dict[int, tuple[float, float]] = {}
    for part in (text or "").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        spw, rng = part.split(":", 1)
        try:
            lo, hi = rng.split("-", 1)
            out[int(spw)] = (float(lo), float(hi))
        except ValueError:
            continue
    return out


@router.get("/cubes/products", response_model=list[CubeProductOut])
async def cube_products(
    datalink_url: str = Query(..., description="DataLink URL of the observation (access_url)"),
    spw_ranges: str | None = Query(None, description="'spw:fmin-fmax; ...' in GHz from ObsCore"),
    line_ghz: float | None = Query(None, gt=0, description="Observed line frequency to match"),
) -> list[CubeProductOut]:
    """FITS products (cubes first) of an ALMA/ESO observation, with the window holding a line."""
    if not cubes._allowed(datalink_url):
        raise HTTPException(status_code=400, detail="datalink_url must point at ALMA or ESO")
    try:
        products = await run_in_threadpool(cubes.list_products, datalink_url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502, detail=f"DataLink: {type(exc).__name__}: {exc}"[:500]
        ) from exc
    ranges = _parse_spw_ranges(spw_ranges)
    out = []
    for p in products:
        rng, approx = _window_for(p, ranges)
        contains = None
        if rng and line_ghz:
            contains = rng[0] <= line_ghz <= rng[1]
        out.append(
            CubeProductOut(
                name=p.name,
                url=p.url,
                size_mb=p.size_mb,
                spw=p.spw,
                kind=p.kind,
                semantics=p.semantics,
                intent=p.intent,
                freq_center_ghz=p.freq_center_ghz,
                freq_min_ghz=rng[0] if rng else None,
                freq_max_ghz=rng[1] if rng else None,
                freq_approx=approx,
                contains_line=contains,
            )
        )
    out.sort(key=lambda o: (o.contains_line is not True, *cubes.product_sort_key(o)))
    return out


_DEFAULT_HALF_WIDTH_GHZ = 0.9375  # half of the widest ALMA spectral window (1.875 GHz)


def _window_for(
    p: cubes.CubeProduct, ranges: dict[int, tuple[float, float]]
) -> tuple[tuple[float, float] | None, bool]:
    """Frequency window of a product: the ObsCore spw range holding its centre frequency when the
    name carries one (pipeline names use local spw indices, so indices cannot be matched), else
    the range with the same spw number; else an approximate window around the centre."""
    if p.freq_center_ghz is not None:
        for lo, hi in ranges.values():
            if lo <= p.freq_center_ghz <= hi:
                return (lo, hi), False
        widths = sorted(hi - lo for lo, hi in ranges.values() if hi > lo)
        half = widths[len(widths) // 2] / 2 if widths else _DEFAULT_HALF_WIDTH_GHZ
        return (p.freq_center_ghz - half, p.freq_center_ghz + half), True
    if p.spw is not None and p.spw in ranges:
        return ranges[p.spw], False
    return None, False


@router.get("/spectrum/cube", response_model=Spectrum1D)
async def cube_spectrum(
    product_url: str = Query(..., description="FITS cube product URL from /api/cubes/products"),
    ra: float = Query(..., ge=0.0, lt=360.0),
    dec: float = Query(..., ge=-90.0, le=90.0),
    radius_arcsec: float = Query(3.0, gt=0.0, le=60.0),
    method: str = Query("sum", pattern="^(sum|mean)$"),
    max_mb: float | None = Query(None, gt=0, le=200_000, description="Override the download cap"),
    fmin_ghz: float | None = Query(None, gt=0, description="SODA band lower edge (GHz)"),
    fmax_ghz: float | None = Query(None, gt=0, description="SODA band upper edge (GHz)"),
    mode: str = Query("auto", pattern="^(auto|cutout|download)$"),
) -> Spectrum1D:
    """Aperture spectrum of a cube product: SODA cutout when available, else a cached download."""
    if not cubes._allowed(product_url):
        raise HTTPException(status_code=400, detail="product_url must point at ALMA or ESO")
    band = (fmin_ghz, fmax_ghz) if fmin_ghz and fmax_ghz else None
    try:
        return await run_in_threadpool(
            cubes.get_cube_service().spectrum,
            product_url,
            ra,
            dec,
            radius_arcsec,
            method,
            max_mb,
            None,
            None,
            band,
            mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"{type(exc).__name__}: {exc}"[:500]) from exc


def _job_status(job: cubes.CubeJob) -> CubeJobStatus:
    return CubeJobStatus(
        job_id=job.id,
        state=job.state,
        downloaded_mb=round(job.downloaded / 1e6, 1),
        total_mb=round(job.total / 1e6, 1) if job.total else None,
        message=job.message,
        spectrum=job.result if job.state == "done" else None,
    )


@router.post("/spectrum/cube/jobs", response_model=CubeJobStatus)
async def cube_spectrum_start(
    product_url: str = Query(..., description="FITS cube product URL from /api/cubes/products"),
    ra: float = Query(..., ge=0.0, lt=360.0),
    dec: float = Query(..., ge=-90.0, le=90.0),
    radius_arcsec: float = Query(3.0, gt=0.0, le=60.0),
    method: str = Query("sum", pattern="^(sum|mean)$"),
    max_mb: float | None = Query(None, gt=0, le=200_000, description="Override the download cap"),
    fmin_ghz: float | None = Query(None, gt=0, description="SODA band lower edge (GHz)"),
    fmax_ghz: float | None = Query(None, gt=0, description="SODA band upper edge (GHz)"),
    mode: str = Query("auto", pattern="^(auto|cutout|download)$"),
) -> CubeJobStatus:
    """Start a cutout-or-download job; poll /spectrum/cube/jobs/{id} for progress."""
    if not cubes._allowed(product_url):
        raise HTTPException(status_code=400, detail="product_url must point at ALMA or ESO")
    band = (fmin_ghz, fmax_ghz) if fmin_ghz and fmax_ghz else None
    job = cubes.get_cube_service().start_job(
        product_url, ra, dec, radius_arcsec, method, max_mb, band, mode
    )
    return _job_status(job)


@router.get("/spectrum/cube/jobs/{job_id}", response_model=CubeJobStatus)
async def cube_spectrum_job(job_id: str) -> CubeJobStatus:
    job = cubes.get_cube_service().job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown or expired job")
    return _job_status(job)


def _cache_status(usage: dict) -> CubeCacheStatus:
    return CubeCacheStatus(
        dir=usage["dir"],
        files=usage["files"],
        bytes_mb=round(usage["bytes"] / 1e6, 1),
        limit_mb=round(usage["limit_bytes"] / 1e6, 1),
    )


@router.get("/cubes/cache", response_model=CubeCacheStatus)
async def cube_cache() -> CubeCacheStatus:
    """Disk used by downloaded cube products."""
    return _cache_status(cubes.get_cube_service().store.usage())


@router.delete("/cubes/cache", response_model=CubeCacheStatus)
async def cube_cache_clear() -> CubeCacheStatus:
    """Delete every downloaded cube product."""
    return _cache_status(await run_in_threadpool(cubes.get_cube_service().store.clear))
