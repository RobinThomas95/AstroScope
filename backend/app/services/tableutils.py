"""Helpers to turn astropy tables from VO services into plain Python rows."""

from __future__ import annotations

from typing import Any

import numpy as np


def cell(value: Any) -> Any:
    """Convert an astropy table cell into a plain Python value (masked / NaN -> None)."""
    if value is np.ma.masked:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and np.isnan(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def rows_to_dicts(table) -> list[dict[str, Any]]:
    if table is None or len(table) == 0:
        return []
    names = list(table.colnames)
    return [{name: cell(row[name]) for name in names} for row in table]


def first(row: dict[str, Any], *keys: str) -> Any:
    """First non-None value among several candidate column names (case-insensitive)."""
    lower = {k.lower(): v for k, v in row.items()}
    for key in keys:
        v = lower.get(key.lower())
        if v is not None:
            return v
    return None
