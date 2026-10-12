import pytest
from fastapi.testclient import TestClient

from app import main
from app.routers import archives as archives_router
from app.services.footprints import regions_to_moc_fits
from app.services.hi import HI_SURVEYS, HICrossMatcher
from app.services.obscore import (
    AlmaArchive,
    ArchiveSearcher,
    EsoArchive,
    MastArchive,
    band_label,
    mjd_to_iso,
    normalise_em,
)
from app.services.stcs import parse_stcs

MUSE_POLY = "POLYGON J2000 53.3930 -36.1320 53.4100 -36.1320 53.4100 -36.1490 53.3930 -36.1490"
ALMA_REGION = "Circle ICRS 53.4015 -36.1404 0.0045"
UNION_REGION = "UNION ICRS (POLYGON 10 10 10.1 10 10.1 10.1 10 10.1 CIRCLE 20 20 0.05)"


# -- STC-S -----------------------------------------------------------------------------------
def test_parse_polygon_circle_and_union():
    (p,) = parse_stcs(MUSE_POLY)
    assert p.kind == "polygon" and len(p.lon) == 4 and p.lat[2] == pytest.approx(-36.149)
    (c,) = parse_stcs(ALMA_REGION)
    assert c.kind == "circle" and c.radius_deg == pytest.approx(0.0045)
    shapes = parse_stcs(UNION_REGION)
    assert [s.kind for s in shapes] == ["polygon", "circle"]
    assert parse_stcs(None) == [] and parse_stcs("garbage 1 2") == []


def test_region_contains_point():
    from app.services.stcs import region_contains

    assert region_contains(MUSE_POLY, 53.4015, -36.1404) is True
    assert region_contains(MUSE_POLY, 53.5, -36.1404) is False
    assert region_contains(ALMA_REGION, 53.4015, -36.1404) is True
    assert region_contains(ALMA_REGION, 53.42, -36.1404) is False
    assert region_contains("POLYGON ICRS 359.9 -0.1 0.1 -0.1 0.1 0.1 359.9 0.1", 0.0, 0.0) is True
    assert region_contains(None, 1, 1) is None and region_contains("junk", 1, 1) is None


def test_regions_to_moc_fits_contains_target():
    import os
    import tempfile

    import astropy.units as u
    from mocpy import MOC

    data = regions_to_moc_fits([MUSE_POLY, ALMA_REGION, None, "BOX ICRS 100 0 0.1 0.1"])
    assert data is not None and data.startswith(b"SIMPLE  =")
    fd, path = tempfile.mkstemp(suffix=".fits")
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    moc = MOC.load(path, format="fits")
    os.unlink(path)
    assert bool(moc.contains_lonlat(53.4015 * u.deg, -36.1404 * u.deg))
    assert bool(moc.contains_lonlat(100.0 * u.deg, 0.0 * u.deg))
    assert not bool(moc.contains_lonlat(200.0 * u.deg, 0.0 * u.deg))
    assert regions_to_moc_fits([None, ""]) is None


# -- unit helpers ----------------------------------------------------------------------------
def test_band_label_and_units():
    assert band_label(4.65e-7, 9.3e-7) == "465–930 nm"
    assert band_label(1.0e-3, 1.4e-3) == "214.1–299.8 GHz"
    assert band_label(5e-6, 2.8e-5) == "5.0–28.0 µm"
    assert band_label(None, None) is None
    assert normalise_em(600.0) == pytest.approx(6e-7)  # nm -> m
    assert normalise_em(6e-7) == pytest.approx(6e-7)
    assert mjd_to_iso(59580.5) == "2022-01-01T12:00:00Z"
    assert mjd_to_iso(None) is None


# -- adapters with canned TAP rows ----------------------------------------------------------
ESO_ROW = {
    "obs_collection": "MUSE",
    "instrument_name": "MUSE",
    "obs_id": "ADP.2019-01-01T00:00:00.001",
    "dataproduct_type": "cube",
    "calib_level": 3,
    "target_name": "NGC1365",
    "t_min": 58000.1,
    "t_exptime": 2400.0,
    "em_min": 4.65e-7,
    "em_max": 9.3e-7,
    "s_ra": 53.40,
    "s_dec": -36.14,
    "s_fov": 0.0167,
    "s_resolution": 0.8,
    "s_region": MUSE_POLY,
    "access_url": "https://archive.eso.org/datalink/links?ID=x",
    "obs_release_date": "2019-06-01T00:00:00Z",
    "proposal_id": "094.B-0321",
    "em_res_power": 3000,
}
MAST_ROW = {
    "obs_collection": "JWST",
    "instrument_name": "NIRCAM/IMAGE",
    "obs_id": "jw02107-o038",
    "dataproduct_type": "image",
    "calib_level": 3,
    "target_name": "NGC-1365",
    "t_min": 59900.2,
    "t_exptime": 1245.0,
    "em_min": 1.5e-6,
    "em_max": 4.4e-6,
    "s_ra": 53.41,
    "s_dec": -36.15,
    "s_region": "POLYGON ICRS 53.3 -36.2 53.5 -36.2 53.5 -36.1 53.3 -36.1",
    "dataURL": "mast:JWST/product/jw02107-o038_i2d.fits",
    "jpegURL": "mast:JWST/product/jw02107-o038_i2d.jpg",
    "proposal_id": "2107",
    "t_obs_release": "2023-01-01T00:00:00",
    "filters": "F200W",
}
ALMA_ROW = {
    "obs_id": "uid://A001/X1465/X1c1d",
    "target_name": "NGC_1365",
    "dataproduct_type": "cube",
    "calib_level": 2,
    "t_min": 58500.0,
    "t_exptime": 3000.0,
    "em_min": 1.0e-3,
    "em_max": 1.4e-3,
    "s_ra": 53.40,
    "s_dec": -36.14,
    "s_resolution": 1.2,
    "s_region": ALMA_REGION,
    "access_url": "https://almascience.org/datalink/sync?ID=x",
    "data_rights": "Public",
    "band_list": "6",
    "frequency": 230.5,
    "bandwidth": 1.875,
    "member_ous_uid": "uid://A001/X1465/X1c1d",
    "proposal_id": "2017.1.00886.L",
    "science_observation": "T",
}


class Canned:
    """Mixin replacing the TAP call with canned rows (and recording the ADQL)."""

    rows: list = []

    def __init__(self, rows, fail=False):
        super().__init__(timeout=1)
        self.rows = rows
        self.fail = fail
        self.adql: list[str] = []

    def run(self, adql, maxrec=500):
        self.adql.append(adql)
        if self.fail:
            raise ConnectionError("tap down")
        return [dict(r) for r in self.rows]


class FakeEso(Canned, EsoArchive):
    pass


class FakeMast(Canned, MastArchive):
    def discover(self):
        self._discovered = True


class FakeAlma(Canned, AlmaArchive):
    pass


def _searcher(eso_fail=False):
    return ArchiveSearcher(
        adapters={
            "eso": FakeEso([ESO_ROW], fail=eso_fail),
            "mast": FakeMast([MAST_ROW]),
            "alma": FakeAlma([ALMA_ROW]),
        }
    )


def test_adapters_map_rows_and_build_adql():
    s = _searcher()
    res = s.search(53.4015, -36.1404, 5.0)
    assert [r.archive for r in res.results] == ["eso", "mast", "alma"]
    eso, mast, alma = res.results
    assert eso.status == "ok" and eso.count == 1
    o = eso.observations[0]
    assert o.instrument == "MUSE" and o.band == "465–930 nm" and o.exptime_s == 2400.0
    assert o.date_obs.startswith("2017-09-04") and o.public is True
    assert o.separation_arcmin == pytest.approx(0.0, abs=0.2)
    assert o.extra["em_res_power"] == 3000 and "scienceportal" in o.archive_url
    assert o.covers_target is True
    assert alma.observations[0].covers_target is True
    adql = s.adapters["eso"].adql[0]
    assert "INTERSECTS(CIRCLE('ICRS', 53.401500, -36.140400, 0.083333), s_region) = 1" in adql
    assert "instrument_name IN ('MUSE', 'UVES', 'XSHOOTER')" in adql
    assert "UPPER" not in adql

    m = mast.observations[0]
    assert m.collection == "JWST"
    assert m.preview_url == (
        "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:JWST/product/jw02107-o038_i2d.jpg"
    )
    assert m.access_url.startswith("https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:")
    assert m.extra["filters"] == "F200W" and m.public is True
    assert "obs_collection IN ('HST', 'JWST')" in s.adapters["mast"].adql[0]
    assert "CONTAINS(POINT('ICRS', s_ra, s_dec)" in s.adapters["mast"].adql[0]

    a = alma.observations[0]
    assert a.instrument == "ALMA" and a.band.startswith("Band 6 (") and "GHz" in a.band
    assert m.band == "F200W (1.5–4.4 µm)"
    assert a.public is True and a.archive_url.startswith("https://almascience.org/aq/?mous=")
    assert "science_observation = 'T'" in s.adapters["alma"].adql[0]
    assert res.instrument_counts == {"ALMA": 1, "MUSE": 1, "NIRCAM/IMAGE": 1}


def test_archive_failure_is_isolated():
    res = _searcher(eso_fail=True).search(53.4, -36.1, 5.0)
    eso = res.results[0]
    assert eso.status == "error" and "tap down" in eso.error
    assert res.results[1].status == "ok"


def test_intersects_falls_back_to_point_search():
    class Flaky(Canned, EsoArchive):
        def run(self, adql, maxrec=500):
            self.adql.append(adql)
            if "INTERSECTS" in adql:
                raise ValueError("INTERSECTS not supported")
            return [dict(ESO_ROW)]

    a = Flaky([])
    res = a.search(53.4, -36.1, 0.1)
    assert res.status == "ok" and res.count == 1
    assert "CONTAINS(POINT" in a.adql[-1]


def test_mast_discovers_endpoint_table_and_columns():
    class Discovering(MastArchive):
        def __init__(self):
            super().__init__(timeout=1)
            self.calls: list[tuple[str, str]] = []

        def run(self, adql, maxrec=500):
            self.calls.append((self.tap_url, adql))
            if self.tap_url.startswith("https://mast.stsci.edu"):
                raise ConnectionError("404")
            if "TAP_SCHEMA.tables" in adql:
                return [{"table_name": "dbo.ObsPointing"}, {"table_name": "dbo.CaomObservation"}]
            if "TAP_SCHEMA.columns" in adql:
                if "dbo.CaomObservation" in adql:  # preferred table but no position columns
                    return [{"column_name": "obsID"}, {"column_name": "collection"}]
                return [
                    {"column_name": "ra"},
                    {"column_name": "dec"},
                    {"column_name": "collection"},
                    {"column_name": "instrument_name"},
                    {"column_name": "intentType"},
                ]
            return [dict(MAST_ROW, ra=53.41, dec=-36.15, collection="JWST")]

    a = Discovering()
    res = a.search(53.4, -36.1, 0.1)
    assert res.status == "ok" and res.count == 1
    assert a.tap_url == "https://vao.stsci.edu/caomtap/CaomTapService.svc"
    assert a.table == "dbo.ObsPointing"  # CaomObservation skipped: no position columns
    assert (a.ra_column, a.dec_column, a.collection_column) == ("ra", "dec", "collection")
    adql = a.calls[-1][1]
    assert "FROM dbo.ObsPointing WHERE" in adql
    assert "CONTAINS(POINT('ICRS', ra, dec)" in adql and "collection IN ('HST', 'JWST')" in adql
    assert "AND intentType = 'science'" in adql
    # discovery happens once
    n = len(a.calls)
    a.search(53.4, -36.1, 0.1)
    assert len(a.calls) == n + 1


def test_mast_reports_when_no_endpoint_answers():
    class Dead(MastArchive):
        def run(self, adql, maxrec=500):
            raise ConnectionError("down")

    res = Dead(timeout=1).search(1, 1, 0.1)
    assert res.status == "error" and "No usable MAST TAP endpoint" in res.error


def test_alma_merges_spectral_windows():
    rows = []
    for spw, (lo, hi) in {23: (85.3, 87.2), 25: (87.2, 89.0), 29: (99.0, 100.9)}.items():
        rows.append(
            dict(
                ALMA_ROW,
                obs_id=f"uid://A001/X3833/X20e.source.NGC_1365.spw.{spw}",
                em_min=299792458.0 / (hi * 1e9),
                em_max=299792458.0 / (lo * 1e9),
                band_list="3",
                s_resolution=2.0 if spw != 25 else 1.8,
            )
        )
    rows.append(
        dict(
            ALMA_ROW,
            obs_id="uid://A001/X1465/X1c1d.source.NGC_1365.spw.17",
            em_min=1.0e-3,
            em_max=1.4e-3,
            band_list="6",
        )
    )
    a = FakeAlma(rows)
    res = a.search(53.4, -36.14, 0.1)
    assert res.count == 2
    first_obs, second = res.observations
    assert first_obs.obs_id == "uid://A001/X3833/X20e.source.NGC_1365"
    assert first_obs.extra["n_spw"] == 3 and first_obs.resolution_arcsec == 1.8
    assert first_obs.band == "Band 3 (85.3–100.9 GHz), 3 spw"
    assert second.extra["n_spw"] == 1 and second.band.startswith("Band 6 (")


# -- HI --------------------------------------------------------------------------------------
class FakeHI(HICrossMatcher):
    def query_catalog(self, catalog, ra, dec, radius_arcmin):
        if catalog == "J/ApJ/861/49":
            return {
                "J/ApJ/861/49/table2": [
                    {
                        "AGC": 123,
                        "Name": "UGC 1",
                        "RAJ2000": 53.40,
                        "DEJ2000": -36.14,
                        "_r": 0.3,
                        "Vhelio": 1636,
                        "W50": 350,
                        "e_W50": 5,
                        "HIflux": 12.3,
                        "e_HIflux": 0.4,
                        "SNR": 20.1,
                        "Dist": 18.0,
                        "logMHI": 9.8,
                    },
                    {"AGC": 999, "Name": "notes row"},
                ]
            }
        if catalog == "VIII/73":
            return {
                "VIII/73/hicat": [
                    {  # real HICAT column names and sexagesimal coordinates
                        "HIPASS": "J0333-36",
                        "RAJ2000": "03 33 33.4",
                        "DEJ2000": "-36 08 27",
                        "_r": 0.598,
                        "RV50max": 1636.4,
                        "RV50min": 1753.0,
                        "RVmom": 1637.1,
                        "RVsp": 1784.7,
                        "W50max": 373.7,
                        "W50min": 140.4,
                        "W20max": 406.8,
                        "Speak": 0.6767,
                        "Sint": 174.7,
                        "RMS": 0.0158,
                        "SimbadName": "HIPASSJ0333-36",
                    },
                ]
            }
        raise TimeoutError("vizier slow")


def test_hi_cross_match_maps_catalog_columns():
    m = FakeHI()
    res = m.search(53.4, -36.14)
    by = {r.catalog: r for r in res.results}
    alf = by["J/ApJ/861/49"]
    assert alf.status == "ok" and len(alf.detections) == 1  # notes row skipped
    d = alf.detections[0]
    assert d.source_id == "123" and d.velocity_kms == 1636 and d.w50_kms == 350
    assert d.flux_jy_kms == 12.3 and d.log_mhi == 9.8 and d.separation_arcmin == 0.3
    hip = by["VIII/73"].detections[0]
    assert hip.source_id == "J0333-36" and hip.name == "HIPASSJ0333-36"
    assert hip.velocity_kms == 1636.4 and hip.w50_kms == 373.7 and hip.w20_kms == 406.8
    assert hip.flux_jy_kms == 174.7 and hip.rms_mjy == 0.0158
    assert hip.ra_deg == pytest.approx(53.389, abs=1e-3)
    assert hip.dec_deg == pytest.approx(-36.1408, abs=1e-3)
    spr = by["J/AJ/128/16"]
    assert spr.status == "error" and "vizier slow" in spr.error
    assert res.radius_arcmin == pytest.approx(7.75)  # half the HIPASS beam
    assert {s.key for s in HI_SURVEYS} == {"alfalfa", "hipass", "nhicat", "hipass_bgc"}
    assert next(s.catalog for s in HI_SURVEYS if s.key == "nhicat") == "VIII/89"


# -- endpoints -------------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(archives_router, "get_searcher", lambda: _searcher())
    monkeypatch.setattr(archives_router, "get_hi_matcher", lambda: FakeHI())
    return TestClient(main.app)


def test_archives_endpoint(client):
    body = client.get(
        "/api/archives", params={"ra": 53.4, "dec": -36.14, "radius_arcmin": 5}
    ).json()
    assert body["instrument_counts"]["MUSE"] == 1
    assert len(body["results"]) == 3
    sub = client.get("/api/archives", params={"ra": 53.4, "dec": -36.14, "archives": "alma"}).json()
    assert [r["archive"] for r in sub["results"]] == ["alma"]
    assert (
        client.get("/api/archives", params={"ra": 53.4, "dec": -36.14, "archives": "x"}).status_code
        == 400
    )
    assert client.get("/api/archives", params={"ra": 400, "dec": 0}).status_code == 422


def test_footprint_moc_endpoint(client):
    res = client.get(
        "/api/archives/eso/footprints/moc", params={"ra": 53.4, "dec": -36.14, "instrument": "MUSE"}
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/fits")
    assert res.headers["x-footprint-count"] == "1"
    none = client.get(
        "/api/archives/eso/footprints/moc", params={"ra": 53.4, "dec": -36.14, "instrument": "UVES"}
    )
    assert none.status_code == 404
    assert (
        client.get("/api/archives/nope/footprints/moc", params={"ra": 1, "dec": 1}).status_code
        == 404
    )


def test_hi_endpoint(client):
    body = client.get("/api/hi", params={"ra": 53.4, "dec": -36.14}).json()
    assert len(body["results"]) == 4
    assert body["results"][0]["detections"][0]["w50_kms"] == 350


def test_health_reports_archives():
    assert TestClient(main.app).get("/api/health").json()["modules"]["archives"] is True
