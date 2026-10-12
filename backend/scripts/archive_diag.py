"""Diagnose the ObsCore archive adapters: run each archive's query around a position and, for
MAST, list the tables and columns its TAP service exposes.

    .venv/bin/python backend/scripts/archive_diag.py [ra dec [radius_arcmin]] [--mast-schema]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.obscore import ARCHIVES, MastArchive  # noqa: E402


def mast_schema() -> None:
    m = MastArchive(timeout=60)
    for url in m.tap_candidates:
        m.tap_url = url
        m._service = None
        print(f"=== {url}")
        try:
            tables = m.list_tables()
        except Exception as exc:  # noqa: BLE001
            print("  FAILED", type(exc).__name__, str(exc)[:200])
            continue
        print("  tables:", ", ".join(tables[:40]))
        for t in tables:
            if t.lower() in {c.lower() for c in m.table_candidates}:
                try:
                    cols = m.list_columns(t)
                    print(f"  columns of {t} ({len(cols)}):", ", ".join(cols))
                except Exception as exc:  # noqa: BLE001
                    print(f"  columns of {t}: FAILED {exc}")


def main(argv: list[str]) -> None:
    if "--mast-schema" in argv:
        mast_schema()
        argv = [a for a in argv if a != "--mast-schema"]
        if not argv:
            return
    ra = float(argv[0]) if argv else 53.4015
    dec = float(argv[1]) if len(argv) > 1 else -36.1404
    radius = float(argv[2]) if len(argv) > 2 else 5.0
    for key, cls in ARCHIVES.items():
        a = cls(timeout=60)
        res = a.search(ra, dec, radius / 60.0)
        print(
            f"=== {key}: {res.status} count={res.count} table={res.table} elapsed={res.elapsed_s}s"
        )
        if res.error:
            print("  error:", res.error)
        for o in res.observations[:3]:
            print(
                f"  {o.instrument} {o.obs_id} {o.date_obs} exp={o.exptime_s} band={o.band} "
                f"res={o.resolution_arcsec} sep={o.separation_arcmin} region={str(o.s_region)[:60]}"
            )
            print(f"    access={o.access_url} preview={o.preview_url} archive={o.archive_url}")


if __name__ == "__main__":
    main(sys.argv[1:])
