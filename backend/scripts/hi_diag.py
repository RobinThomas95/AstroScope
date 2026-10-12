"""Show what VizieR returns for the HI catalogues around a position (default: NGC 1365).

.venv/bin/python backend/scripts/hi_diag.py [ra dec [radius_arcmin]]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.hi import HI_SURVEYS, HICrossMatcher  # noqa: E402


def main(argv: list[str]) -> None:
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
