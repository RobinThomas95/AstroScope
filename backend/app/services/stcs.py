"""Minimal STC-S region parser (the ``s_region`` column of ObsCore tables).

Handles the shapes archives actually emit: ``POLYGON``, ``CIRCLE``, ``BOX`` and ``UNION`` of
those, with optional frame words (ICRS, J2000, FK5, ...). Anything else is ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FRAME_WORDS = {
    "ICRS",
    "J2000",
    "FK5",
    "FK4",
    "GALACTIC",
    "ECLIPTIC",
    "UNKNOWNFRAME",
    "TOPOCENTER",
    "GEOCENTER",
    "BARYCENTER",
    "SPHERICAL2",
    "CARTESIAN2",
}
_SHAPE_WORDS = {"POLYGON", "CIRCLE", "BOX", "UNION", "INTERSECTION", "NOT", "DIFFERENCE"}
_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


@dataclass
class Shape:
    kind: str  # "polygon" or "circle"
    lon: list[float] = field(default_factory=list)
    lat: list[float] = field(default_factory=list)
    radius_deg: float | None = None


def parse_stcs(text: str | None) -> list[Shape]:
    """Return the polygons and circles described by an STC-S string."""
    if not text:
        return []
    tokens = re.split(r"[\s(),]+", text.strip())
    shapes: list[Shape] = []
    i = 0
    while i < len(tokens):
        word = tokens[i].upper()
        if word in ("POLYGON", "CIRCLE", "BOX"):
            i += 1
            numbers: list[float] = []
            while i < len(tokens):
                t = tokens[i]
                if t.upper() in _FRAME_WORDS or t == "":
                    i += 1
                    continue
                if _NUM.match(t):
                    numbers.append(float(t))
                    i += 1
                    continue
                break  # next shape keyword
            if word == "POLYGON" and len(numbers) >= 6 and len(numbers) % 2 == 0:
                shapes.append(Shape("polygon", numbers[0::2], numbers[1::2]))
            elif word == "CIRCLE" and len(numbers) == 3:
                shapes.append(Shape("circle", [numbers[0]], [numbers[1]], numbers[2]))
            elif word == "BOX" and len(numbers) == 4:
                ra, dec, w, h = numbers
                shapes.append(
                    Shape(
                        "polygon",
                        [ra - w / 2, ra + w / 2, ra + w / 2, ra - w / 2],
                        [dec - h / 2, dec - h / 2, dec + h / 2, dec + h / 2],
                    )
                )
        else:
            i += 1
    return shapes


def _wrap(delta_deg: float) -> float:
    return (delta_deg + 180.0) % 360.0 - 180.0


def shape_contains(shape: Shape, ra: float, dec: float) -> bool:
    """Point-in-shape test on a local tangent plane (fine for fields up to a few degrees)."""
    import math

    if shape.kind == "circle":
        d = math.radians
        a = (
            math.sin(d(dec - shape.lat[0]) / 2) ** 2
            + math.cos(d(shape.lat[0])) * math.cos(d(dec)) * math.sin(d(ra - shape.lon[0]) / 2) ** 2
        )
        sep = math.degrees(2 * math.asin(min(1.0, math.sqrt(a))))
        return sep <= (shape.radius_deg or 0.0)
    ra0 = shape.lon[0]
    dec0 = sum(shape.lat) / len(shape.lat)
    cosd = math.cos(math.radians(dec0))
    xs = [_wrap(lon - ra0) * cosd for lon in shape.lon]
    ys = [lat - dec0 for lat in shape.lat]
    px, py = _wrap(ra - ra0) * cosd, dec - dec0
    inside = False
    n = len(xs)
    for i in range(n):
        j = (i - 1) % n
        if (ys[i] > py) != (ys[j] > py):
            x_cross = xs[j] + (py - ys[j]) * (xs[i] - xs[j]) / (ys[i] - ys[j])
            if px < x_cross:
                inside = not inside
    return inside


def region_contains(text: str | None, ra: float, dec: float) -> bool | None:
    """Whether an STC-S region covers a position; None when the region is missing/unparseable."""
    shapes = parse_stcs(text)
    if not shapes:
        return None
    return any(shape_contains(s, ra, dec) for s in shapes)
