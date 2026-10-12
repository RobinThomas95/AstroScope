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
