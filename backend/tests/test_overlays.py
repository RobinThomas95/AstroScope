import pytest
from fastapi.testclient import TestClient

from app import main
from app.routers import surveys as surveys_router
from app.services.mocserver import MocRecord, MocServerClient

FITS = b"SIMPLE  =                    T / conforms to FITS standard" + b" " * 2000


class FakeMocServer(MocServerClient):
    def __init__(self, records=None, fits=FITS):
        super().__init__()
        self.records = records or {}
        self.fits = fits
        self.searches: list[str] = []

    def search(self, expression):
        self.searches.append(expression)
        return [MocRecord.from_json(r, expression) for r in self.records.get(expression, [])]

    def _get(self, params):  # pragma: no cover - the fake never hits the network
        raise AssertionError("network call attempted")

    def fetch_moc_fits(self, record_id):
        if self.fits is None:
            raise ValueError("no moc")
        return self.fits[record_id] if isinstance(self.fits, dict) else self.fits


def _client(monkeypatch, fake):
    monkeypatch.setattr(surveys_router, "get_mocserver", lambda: fake)
    return TestClient(main.app)


def test_resolve_keeps_all_usable_records_and_falls_through_expressions(monkeypatch):
    fake = FakeMocServer(
        records={
            "ID=ESAVO/P/HST/*": [],
            "ID=CDS/B/hst/*": [
                {"ID": "CDS/B/hst/small", "obs_title": "small", "moc_sky_fraction": 0.001},
                {"ID": "CDS/B/hst/log", "obs_title": "HST log", "moc_sky_fraction": 0.02},
                {"ID": "CDS/B/hst/empty", "obs_title": "empty", "moc_sky_fraction": 0},
            ],
        }
    )
    client = _client(monkeypatch, fake)
    body = client.get("/api/overlays/hst").json()
    assert body["resolved"] is True
    # widest first, zero-coverage records dropped
    assert [r["id"] for r in body["records"]] == ["CDS/B/hst/log", "CDS/B/hst/small"]
    assert body["expression"] == "ID=CDS/B/hst/*"
    assert body["moc_url"] == "/api/overlays/hst/moc"
    # Resolution is cached: a second call performs no new searches.
    n = len(fake.searches)
    client.get("/api/overlays/hst")
    assert len(fake.searches) == n


def test_moc_endpoint_serves_fits(monkeypatch):
    fake = FakeMocServer(
        records={"ID=ESAVO/P/HST/*": [{"ID": "ESAVO/P/HST/ACS", "moc_sky_fraction": 0.02}]}
    )
    client = _client(monkeypatch, fake)
    res = client.get("/api/overlays/hst/moc")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/fits")
    assert res.headers["x-moc-records"] == "ESAVO/P/HST/ACS"
    assert res.content.startswith(b"SIMPLE  =")


def _cone_fits(lon, lat, radius_deg):
    import io

    import astropy.units as u
    from mocpy import MOC

    moc = MOC.from_cone(lon=lon * u.deg, lat=lat * u.deg, radius=radius_deg * u.deg, max_depth=8)
    buf = io.BytesIO()
    moc.serialize(format="fits").writeto(buf)
    return buf.getvalue(), moc.sky_fraction


def test_moc_endpoint_unions_several_records(monkeypatch):
    import io

    from mocpy import MOC

    a, fa = _cone_fits(10, 10, 1)
    b, fb = _cone_fits(40, -20, 1)
    fake = FakeMocServer(
        records={
            "ID=ESAVO/P/JWST/*": [
                {"ID": "ESAVO/P/JWST/NIRCam_Imaging", "moc_sky_fraction": fa},
                {"ID": "ESAVO/P/JWST/MIRI_Imaging", "moc_sky_fraction": fb},
            ]
        },
        fits={"ESAVO/P/JWST/NIRCam_Imaging": a, "ESAVO/P/JWST/MIRI_Imaging": b},
    )
    client = _client(monkeypatch, fake)
    res = client.get("/api/overlays/jwst/moc")
    assert res.status_code == 200
    assert res.headers["x-moc-records"] == "ESAVO/P/JWST/NIRCam_Imaging, ESAVO/P/JWST/MIRI_Imaging"
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".fits")
    with os.fdopen(fd, "wb") as fh:
        fh.write(res.content)
    merged = MOC.load(path, format="fits")
    os.unlink(path)
    # disjoint cones: the union covers the sum of both
    assert merged.sky_fraction == pytest.approx(fa + fb, rel=1e-6)
    assert io.BytesIO(res.content).read(9) == b"SIMPLE  ="


def test_moc_endpoint_reports_unresolved_overlay(monkeypatch):
    client = _client(monkeypatch, FakeMocServer())
    res = client.get("/api/overlays/jwst/moc")
    assert res.status_code == 502
    assert "No MOCServer record" in res.json()["detail"]
    assert client.get("/api/overlays/nope/moc").status_code == 404


def test_surveys_catalog_points_overlays_at_proxy(monkeypatch):
    client = _client(monkeypatch, FakeMocServer())
    body = client.get("/api/surveys").json()
    assert all(o["moc_url"].startswith("/api/overlays/") for o in body["overlays"])


def test_union_failure_falls_back_to_largest_member(monkeypatch):
    from app.services import mocserver

    a, fa = _cone_fits(10, 10, 1)
    fake = FakeMocServer(
        records={
            "ID=CDS/C/*ALMA*": [
                {"ID": "CDS/C/PHANGS-ALMA", "moc_sky_fraction": fa},
                {"ID": "CDS/C/ALMA-test", "moc_sky_fraction": 1e-6},
            ]
        },
        fits={"CDS/C/PHANGS-ALMA": a, "CDS/C/ALMA-test": b"SIMPLE  = garbage" + b" " * 100},
    )
    monkeypatch.setattr(
        mocserver, "union_fits_mocs", lambda blobs: (_ for _ in ()).throw(ValueError("bad MOC"))
    )
    client = _client(monkeypatch, fake)
    res = client.get("/api/overlays/alma/moc")
    assert res.status_code == 200
    assert res.content == a
