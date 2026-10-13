"""Diagnose an ALMA SODA cutout request for one cube product.

    .venv/bin/python backend/scripts/alma_soda_diag.py "<product url>" \
        [ra dec radius_arcsec [fmin_ghz fmax_ghz]]

For each ALMA mirror it prints the SODA URL AstroScope sends (with and without BAND), the HTTP
status, the content type and the first bytes of the reply, so a rejected request can be read.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main(argv: list[str]) -> None:
    import httpx

    from app.services.cubes import SODA_HOSTS, alma_soda_url

    if not argv:
        print(__doc__)
        return
    url = argv[0]
    ra = float(argv[1]) if len(argv) > 1 else 53.4015
    dec = float(argv[2]) if len(argv) > 2 else -36.1404
    radius = float(argv[3]) if len(argv) > 3 else 3.0
    band = (float(argv[4]), float(argv[5])) if len(argv) > 5 else None
    from urllib.parse import urlparse

    own = urlparse(url).hostname or ""
    hosts = [own] + [h for h in SODA_HOSTS if h != own]
    for host in hosts:
        for b in [band, None] if band else [None]:
            soda = alma_soda_url(url, ra, dec, radius, b, host=host)
            print("\n==>", soda)
            try:
                with httpx.Client(timeout=300, follow_redirects=True) as client:
                    with client.stream(
                        "GET", soda, headers={"User-Agent": "AstroScope/0.1"}
                    ) as res:
                        head = b""
                        for chunk in res.iter_bytes(4096):
                            head += chunk
                            if len(head) >= 4096:
                                break
                print(
                    "   status",
                    res.status_code,
                    "type",
                    res.headers.get("content-type"),
                    "length",
                    res.headers.get("content-length"),
                )
                print("   first bytes:", head[:300])
            except Exception as exc:  # noqa: BLE001
                print("   error:", type(exc).__name__, exc)


if __name__ == "__main__":
    main(sys.argv[1:])
