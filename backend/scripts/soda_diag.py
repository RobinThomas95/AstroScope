"""Diagnose an ESO DataLink / SODA cutout request.

    .venv/bin/python backend/scripts/soda_diag.py "<datalink url>" [ra dec radius_arcsec]

Prints the DataLink rows and service descriptors, the SODA query pyvo would send, and the raw
HTTP response of that request (status, headers, first bytes).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main(argv: list[str]) -> None:
    import astropy.units as u
    import requests
    from pyvo.dal.adhoc import DatalinkResults, SodaQuery

    url = (
        argv[0]
        if argv
        else (
            "https://archive.eso.org/datalink/links?ID=ivo://eso.org/ID?ADP.2019-01-30T20:25:13.992"
        )
    )
    ra = float(argv[1]) if len(argv) > 1 else 53.4015
    dec = float(argv[2]) if len(argv) > 2 else -36.1404
    radius = float(argv[3]) if len(argv) > 3 else 4.0

    print("=== DataLink document:", url)
    dl = DatalinkResults.from_result_url(url)
    for row in dl:
        print(
            f"  [{row.semantics}] {row.content_type} {row.content_length} "
            f"{row.access_url or ''} service_def={row.service_def or ''} "
            f"err={row.error_message or ''}"
        )
    print("=== Ad hoc services")
    for svc in dl.iter_adhocservices():
        print(f"  ID={svc.ID} ivoid={getattr(svc, 'ivoid', None)}")
        for p in svc.params:
            print(f"    param {p.name} = {p.value}")
        for g in svc.groups:
            for p in g.entries:
                print(f"    input {p.name} ({getattr(p, 'datatype', '?')}) {p.description or ''}")

    rows = list(dl.bysemantics("#this"))
    if not rows:
        print("no #this row")
        return
    row = rows[0]
    soda = row._get_soda_resource()  # noqa: SLF001
    print("=== SODA resource:", None if soda is None else getattr(soda, "ID", soda))
    if soda is None:
        return
    q = SodaQuery.from_resource(row, soda, circle=u.Quantity([ra, dec, radius / 3600], u.deg))
    print("=== pyvo query URL:", q.queryurl)
    print("    params:", dict(q))
    for method in ("GET", "POST"):
        try:
            res = requests.request(
                method,
                q.queryurl,
                params=dict(q) if method == "GET" else None,
                data=dict(q) if method == "POST" else None,
                timeout=180,
                allow_redirects=True,
                stream=True,
            )
            body = res.raw.read(400)
            print(
                f"=== {method} -> HTTP {res.status_code} {res.headers.get('content-type')} "
                f"len={res.headers.get('content-length')} final_url={res.url}"
            )
            print("    body starts:", body[:200])
            res.close()
        except Exception as exc:  # noqa: BLE001
            print(f"=== {method} failed: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main(sys.argv[1:])
