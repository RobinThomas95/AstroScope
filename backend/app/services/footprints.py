"""Build FITS MOCs from observation footprints (STC-S regions)."""

from __future__ import annotations

import io
import logging

from app.services.stcs import Shape, parse_stcs

log = logging.getLogger(__name__)

DEFAULT_ORDER = 13  # ~26 arcsec cells: fine enough for MUSE's 1 arcmin field


def shapes_to_moc(shapes: list[Shape], max_order: int = DEFAULT_ORDER):
    import astropy.units as u
    from mocpy import MOC

    moc = None
    for s in shapes:
        try:
            if s.kind == "circle":
                part = MOC.from_cone(
                    lon=s.lon[0] * u.deg,
                    lat=s.lat[0] * u.deg,
                    radius=(s.radius_deg or 0) * u.deg,
                    max_depth=max_order,
                )
            else:
                part = MOC.from_polygon(
                    lon=u.Quantity(s.lon, u.deg),
                    lat=u.Quantity(s.lat, u.deg),
                    max_depth=max_order,
                )
        except Exception as exc:  # noqa: BLE001 - a malformed region must not sink the rest
            log.warning("Skipping footprint %s: %s", s, exc)
            continue
        moc = part if moc is None else moc.union(part)
    return moc


def regions_to_moc_fits(regions: list[str | None], max_order: int = DEFAULT_ORDER) -> bytes | None:
    """Union of the given STC-S regions as a FITS MOC (None if nothing usable)."""
    shapes: list[Shape] = []
    for r in regions:
        shapes.extend(parse_stcs(r))
    moc = shapes_to_moc(shapes, max_order=max_order)
    if moc is None:
        return None
    buf = io.BytesIO()
    moc.serialize(format="fits").writeto(buf)
    return buf.getvalue()
