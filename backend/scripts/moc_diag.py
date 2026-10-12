"""Diagnose coverage overlays: query the CDS MOCServer directly for each record and report
what comes back. Usage (from the repo root):

    .venv/bin/python backend/scripts/moc_diag.py            # all overlays
    .venv/bin/python backend/scripts/moc_diag.py muse alma  # selected overlays
"""

from __future__ import annotations

import os
import sys
import tempfile

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.mocserver import (  # noqa: E402
    FITS_MAGIC,
    MOCSERVER_URL,
    MocServerClient,
    moc_dimension,
    to_spatial_fits,
)
from app.services.surveys import OVERLAYS  # noqa: E402

TEST_POINTS = {
    "NGC 1365": (53.4015, -36.1404),
    "HUDF": (53.1625, -27.7914),
    "M 87": (187.7059, 12.3911),
}


def tomcat_message(html: str) -> str:
    import re

    m = re.search(r"<b>message</b>\s*<u>(.*?)</u>", html, re.S)
    return m.group(1).strip() if m else html[:200]


def describe_fits(data: bytes) -> str:
    from astropy.io import fits

    try:
        dim = moc_dimension(data)
        if dim != "SPACE":
            data = to_spatial_fits(data)
            print(f"    {dim} MOC projected onto the sky")
    except Exception as exc:  # noqa: BLE001
        print(f"    projection FAILED {type(exc).__name__}: {exc}")

    fd, path = tempfile.mkstemp(suffix=".fits")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    try:
        out = []
        with fits.open(path) as hdul:
            for i, hdu in enumerate(hdul):
                h = hdu.header
                keys = {
                    k: h.get(k)
                    for k in (
                        "XTENSION",
                        "PIXTYPE",
                        "ORDERING",
                        "COORDSYS",
                        "MOCORDER",
                        "MOCORD_S",
                        "MOCORD_T",
                        "MOCDIM",
                        "MOCTYPE",
                        "MOCVERS",
                        "TTYPE1",
                        "NAXIS2",
                    )
                }
                shown = ", ".join(f"{k}={v}" for k, v in keys.items() if v is not None)
                out.append(f"    HDU{i}: {shown}")
        try:
            from mocpy import MOC

            moc = MOC.load(path, format="fits")
            out.append(
                f"    mocpy: ok, max_order={moc.max_order}, sky_fraction={moc.sky_fraction:.3e}"
            )
            for name, (ra, dec) in TEST_POINTS.items():
                import astropy.units as u

                inside = bool(moc.contains_lonlat(ra * u.deg, dec * u.deg))
                out.append(f"    contains {name}: {inside}")
        except Exception as exc:  # noqa: BLE001
            out.append(f"    mocpy: FAILED {type(exc).__name__}: {exc}")
        return "\n".join(out)
    finally:
        os.unlink(path)


def main(argv: list[str]) -> None:
    wanted = set(argv) or {o.id for o in OVERLAYS}
    client = MocServerClient(MOCSERVER_URL)
    for o in OVERLAYS:
        if o.id not in wanted:
            continue
        print(f"=== {o.id}: {o.label}")
        records = client.resolve(o.id, o.expressions)
        if not records:
            print("  no records matched", o.expressions)
            continue
        for r in records:
            print(f"  {r.id}  ({r.title}, sky_fraction={r.sky_fraction})")
            attempts = [{"ID": r.id, "get": "moc"}]
            attempts += [{"ID": r.id, "get": "moc", "order": o} for o in (12, 11, 10, 9, 8)]
            for params in attempts:
                try:
                    res = httpx.get(MOCSERVER_URL, params=params, timeout=60, follow_redirects=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"    {params}: request failed {type(exc).__name__}: {exc}")
                    continue
                ctype = res.headers.get("content-type")
                head = res.content[:40]
                print(
                    f"    {params}: HTTP {res.status_code}, {ctype}, "
                    f"{len(res.content)} bytes, starts {head!r}"
                )
                if res.content.startswith(FITS_MAGIC):
                    print(describe_fits(res.content))
                    break
                print("    message:", tomcat_message(res.text))
                if res.status_code != 413:
                    break
            else:
                url = r.moc_access_url
                print(f"    moc_access_url: {url}")
                if url:
                    res = httpx.get(url, timeout=120, follow_redirects=True)
                    print(
                        f"    direct: HTTP {res.status_code}, {res.headers.get('content-type')}, "
                        f"{len(res.content)} bytes"
                    )
                    if res.content.startswith(FITS_MAGIC):
                        print(describe_fits(res.content))


if __name__ == "__main__":
    main(sys.argv[1:])
