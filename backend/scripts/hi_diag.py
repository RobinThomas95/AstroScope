"""Show what VizieR returns for the HI catalogues around a position (default: NGC 1365).

.venv/bin/python backend/scripts/hi_diag.py [ra dec [radius_arcmin]]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.hi import HI_SURVEYS, HICrossMatcher  # noqa: E402


def find(keywords: list[str]) -> None:
    """List VizieR catalogues matching keywords (to confirm catalogue identifiers)."""
    from astroquery.vizier import Vizier

    for kw in keywords:
        print(f"=== VizieR catalogues matching {kw!r}")
        try:
            cats = Vizier.find_catalogs(kw, max_catalogs=40)
        except Exception as exc:  # noqa: BLE001
            print("  FAILED", type(exc).__name__, exc)
            continue
        if not cats:
            print("  (no matches)")
        for name, meta in cats.items():
            print(f"  {name}: {getattr(meta, 'description', meta)}")


def describe(catalogs: list[str]) -> None:
    """Print the tables and columns of VizieR catalogues (confirms an identifier exists)."""
    from astroquery.vizier import Vizier

    v = Vizier(row_limit=3)
    for cat in catalogs:
        print(f"=== VizieR catalogue {cat}")
        try:
            tables = v.get_catalogs(cat)
        except Exception as exc:  # noqa: BLE001
            print("  FAILED", type(exc).__name__, exc)
            continue
        if not tables:
            print("  (no such catalogue, or no tables)")
        for name, table in zip(tables.keys(), tables.values(), strict=True):
            print(f"  table {name}: columns {list(table.colnames)}")


def main(argv: list[str]) -> None:
    if argv and argv[0] == "--find":
        find(argv[1:] or ["Springob HI", "HIPASS", "ALFALFA"])
        return
    if argv and argv[0] == "--catalog":
        describe(argv[1:] or ["J/ApJS/160/149", "VIII/89"])
        return
    ra = float(argv[0]) if argv else 53.4015
    dec = float(argv[1]) if len(argv) > 1 else -36.1404
    radius = float(argv[2]) if len(argv) > 2 else None
    m = HICrossMatcher()
    for survey in HI_SURVEYS:
        r = radius or max(3.0, survey.beam_arcmin / 2)
        print(f"=== {survey.key}: {survey.catalog} within {r}'")
        try:
            tables = m.query_catalog(survey.catalog, ra, dec, r)
        except Exception as exc:  # noqa: BLE001
            print("  FAILED", type(exc).__name__, exc)
            continue
        if not tables:
            print("  no tables returned")
        for name, rows in tables.items():
            print(f"  table {name}: {len(rows)} row(s)")
            for row in rows[:3]:
                shown = {k: v for k, v in row.items() if v is not None}
                print("   ", shown)
                mapped = m.map_row(survey, name, row, ra, dec)
                print(
                    "    -> mapped:",
                    "dropped"
                    if mapped is None
                    else f"v={mapped.velocity_kms} W50={mapped.w50_kms} W20={mapped.w20_kms} "
                    f"S={mapped.flux_jy_kms}",
                )


if __name__ == "__main__":
    main(sys.argv[1:])
