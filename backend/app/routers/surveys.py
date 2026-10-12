"""HTTP endpoints for Module 2 (sky viewer configuration and coverage overlays)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.services.mocserver import get_mocserver
from app.services.surveys import (
    DEFAULT_GRID,
    OVERLAY_BY_ID,
    OVERLAYS,
    SURVEYS,
    TARGET_OVERLAYS,
    Overlay,
    Survey,
    TargetOverlay,
)

router = APIRouter(prefix="/api", tags=["surveys"])


class OverlayOut(BaseModel):
    id: str
    label: str
    color: str
    description: str
    moc_url: str


class SurveyCatalog(BaseModel):
    surveys: list[Survey]
    overlays: list[OverlayOut]
    target_overlays: list[TargetOverlay]
    default_grid: list[str]


class OverlayRecord(BaseModel):
    id: str
    title: str | None = None
    sky_fraction: float | None = None


class OverlayStatus(BaseModel):
    id: str
    label: str
    resolved: bool
    records: list[OverlayRecord]
    expression: str | None = None
    expressions: list[str]
    moc_url: str


def _overlay_out(o: Overlay) -> OverlayOut:
    return OverlayOut(
        id=o.id, label=o.label, color=o.color, description=o.description, moc_url=o.moc_path
    )


@router.get("/surveys", response_model=SurveyCatalog)
async def list_surveys() -> SurveyCatalog:
    return SurveyCatalog(
        surveys=SURVEYS,
        overlays=[_overlay_out(o) for o in OVERLAYS],
        target_overlays=TARGET_OVERLAYS,
        default_grid=DEFAULT_GRID,
    )


def _status(o: Overlay) -> OverlayStatus:
    recs = get_mocserver().resolve(o.id, o.expressions)
    return OverlayStatus(
        id=o.id,
        label=o.label,
        resolved=bool(recs),
        records=[OverlayRecord(id=r.id, title=r.title, sky_fraction=r.sky_fraction) for r in recs],
        expression=recs[0].expression if recs else None,
        expressions=o.expressions,
        moc_url=o.moc_path,
    )


@router.get("/overlays", response_model=list[OverlayStatus])
async def overlay_status() -> list[OverlayStatus]:
    """Which MOCServer record each overlay resolves to (diagnostics)."""
    return await run_in_threadpool(lambda: [_status(o) for o in OVERLAYS])


@router.get("/overlays/{overlay_id}", response_model=OverlayStatus)
async def overlay_detail(overlay_id: str) -> OverlayStatus:
    o = OVERLAY_BY_ID.get(overlay_id)
    if o is None:
        raise HTTPException(status_code=404, detail=f"Unknown overlay '{overlay_id}'")
    return await run_in_threadpool(_status, o)


@router.get("/overlays/{overlay_id}/moc", response_class=Response)
async def overlay_moc(overlay_id: str) -> Response:
    """The overlay's coverage as a FITS MOC, proxied from the CDS MOCServer."""
    o = OVERLAY_BY_ID.get(overlay_id)
    if o is None:
        raise HTTPException(status_code=404, detail=f"Unknown overlay '{overlay_id}'")
    client = get_mocserver()
    recs = await run_in_threadpool(client.resolve, o.id, o.expressions)
    if not recs:
        raise HTTPException(
            status_code=502,
            detail=f"No MOCServer record found for overlay '{overlay_id}' "
            f"(tried: {', '.join(o.expressions)})",
        )
    ids = [r.id for r in recs]
    try:
        data = await run_in_threadpool(client.fetch_union_fits, recs)
    except Exception as exc:  # noqa: BLE001 - remote service
        raise HTTPException(
            status_code=502, detail=f"Could not fetch MOC for {', '.join(ids)}: {exc}"
        ) from exc
    return Response(
        content=data,
        media_type="application/fits",
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Moc-Records": ", ".join(ids),
            "Content-Disposition": f'inline; filename="{overlay_id}.fits"',
        },
    )
