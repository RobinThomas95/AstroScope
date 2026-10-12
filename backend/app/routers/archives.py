"""HTTP endpoints for Module 3 (archive cross-matching, footprints, HI coverage)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool

from app.schemas.archives import ArchiveSearch, HISearch
from app.services.footprints import regions_to_moc_fits
from app.services.hi import get_hi_matcher
from app.services.obscore import ARCHIVES, get_searcher

router = APIRouter(prefix="/api", tags=["archives"])

RA = Query(..., ge=0.0, lt=360.0, description="ICRS right ascension, degrees")
DEC = Query(..., ge=-90.0, le=90.0, description="ICRS declination, degrees")


@router.get("/archives", response_model=ArchiveSearch)
async def search_archives(
    ra: float = RA,
    dec: float = DEC,
    radius_arcmin: float = Query(5.0, gt=0.0, le=60.0),
    archives: str | None = Query(None, description="Comma-separated subset of eso,mast,alma"),
    maxrec: int = Query(500, ge=1, le=2000),
) -> ArchiveSearch:
    keys = [k.strip() for k in archives.split(",")] if archives else None
    if keys and any(k not in ARCHIVES for k in keys):
        raise HTTPException(status_code=400, detail=f"Unknown archive in {keys}")
    return await run_in_threadpool(get_searcher().search, ra, dec, radius_arcmin, keys, maxrec)


@router.get("/archives/{archive}/footprints/moc", response_class=Response)
async def archive_footprints(
    archive: str,
    ra: float = RA,
    dec: float = DEC,
    radius_arcmin: float = Query(10.0, gt=0.0, le=60.0),
    instrument: str | None = Query(None, description="Keep only this instrument (e.g. MUSE)"),
    collection: str | None = Query(None, description="Keep only this collection (e.g. HST)"),
    maxrec: int = Query(500, ge=1, le=2000),
) -> Response:
    """Union of the observation footprints near a position as a FITS MOC."""
    if archive not in ARCHIVES:
        raise HTTPException(status_code=404, detail=f"Unknown archive '{archive}'")
    search = await run_in_threadpool(
        get_searcher().search, ra, dec, radius_arcmin, [archive], maxrec
    )
    result = search.results[0]
    if result.status != "ok":
        raise HTTPException(status_code=502, detail=f"{result.label}: {result.error}")
    obs = result.observations
    if instrument:
        wanted = instrument.upper().replace("-", "")
        obs = [o for o in obs if (o.instrument or "").upper().replace("-", "") == wanted]
    if collection:
        obs = [o for o in obs if (o.collection or "").upper() == collection.upper()]
    data = await run_in_threadpool(regions_to_moc_fits, [o.s_region for o in obs])
    if data is None:
        what = instrument or collection or archive
        raise HTTPException(
            status_code=404,
            detail=f"No {what} footprints within {radius_arcmin}' of the position",
        )
    return Response(
        content=data,
        media_type="application/fits",
        headers={"Cache-Control": "public, max-age=3600", "X-Footprint-Count": str(len(obs))},
    )


@router.get("/hi", response_model=HISearch)
async def search_hi(
    ra: float = RA,
    dec: float = DEC,
    radius_arcmin: float | None = Query(
        None, gt=0.0, le=60.0, description="Default: half the survey beam, min 3'"
    ),
) -> HISearch:
    return await run_in_threadpool(get_hi_matcher().search, ra, dec, radius_arcmin)
