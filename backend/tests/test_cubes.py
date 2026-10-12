import io
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main
from app.routers import spectra as spectra_router
from app.services import cubes
from app.services.cubes import CubeProduct, CubeSpectrumService, CubeStore, list_products
from app.services.muse import extract_spectrum

RA0, DEC0 = 53.4015, -36.1404


def make_alma_cube(ctype3="FREQ", restfrq=230.538e9):
    """A 4-D (Stokes, freq, y, x) ALMA-like cube with a CO line at the redshifted frequency."""
    from astropy.io import fits

    nz, ny, nx = 100, 15, 15
    cube = np.zeros((1, nz, ny, nx), dtype=np.float32)
    z = 0.005457
    f_obs = restfrq / (1 + z)
    f0, df = 229.0e9, 10e6  # 229.0 .. 230.0 GHz
    k = int(round((f_obs - f0) / df))
    cube[0, :, ny // 2, nx // 2] = 0.01
    cube[0, k, ny // 2, nx // 2] = 1.0
    hdr = fits.Header()
    hdr["BUNIT"] = "Jy/beam"
    hdr["OBJECT"] = "NGC_1365"
    hdr["RESTFRQ"] = restfrq
    hdr["CTYPE1"], hdr["CTYPE2"], hdr["CTYPE4"] = "RA---SIN", "DEC--SIN", "STOKES"
    hdr["CRPIX1"], hdr["CRPIX2"], hdr["CRPIX4"] = nx // 2 + 1, ny // 2 + 1, 1
    hdr["CRVAL1"], hdr["CRVAL2"], hdr["CRVAL4"] = RA0, DEC0, 1
    hdr["CDELT1"], hdr["CDELT2"], hdr["CDELT4"] = -0.5 / 3600, 0.5 / 3600, 1
    hdr["CUNIT1"], hdr["CUNIT2"] = "deg", "deg"
    if ctype3 == "FREQ":
        hdr["CTYPE3"], hdr["CUNIT3"] = "FREQ", "Hz"
        hdr["CRPIX3"], hdr["CRVAL3"], hdr["CDELT3"] = 1, f0, df
    else:  # radio velocity axis in km/s
        c = 299792.458
        v0 = c * (1 - f0 / restfrq)
        dv = -c * df / restfrq
        hdr["CTYPE3"], hdr["CUNIT3"] = "VRAD", "km/s"
        hdr["CRPIX3"], hdr["CRVAL3"], hdr["CDELT3"] = 1, v0, dv
        hdr["SPECSYS"] = "LSRK"
    buf = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(cube, header=hdr)]).writeto(buf)
    return buf.getvalue(), k, f_obs


def test_extract_from_4d_frequency_cube():
    data, k, f_obs = make_alma_cube()
    spec = extract_spectrum(data, RA0, DEC0, radius_arcsec=0.6)
    assert spec.n_channels == 100 and any("Stokes" in w for w in spec.warnings)
    freq = cubes.frequency_axis_ghz(spec.wavelength_A)
    assert freq[0] == pytest.approx(229.0, rel=1e-6)
    assert freq[k] == pytest.approx(f_obs / 1e9, abs=0.006)
    assert spec.flux[k] == pytest.approx(1.0) and spec.flux_unit == "Jy/beam"


def test_extract_from_velocity_axis_cube():
    data, k, f_obs = make_alma_cube(ctype3="VRAD")
    spec = extract_spectrum(data, RA0, DEC0, radius_arcsec=0.6)
    freq = cubes.frequency_axis_ghz(spec.wavelength_A)
    assert freq[k] == pytest.approx(f_obs / 1e9, abs=0.006)


class Row:
    def __init__(self, url, ctype, size, semantics="#this"):
        self.access_url = url
        self.content_type = ctype
        self.content_length = size
        self.semantics = semantics


class DL(list):
    pass


def test_list_products_orders_cubes_first(monkeypatch):
    from pyvo.dal import adhoc

    rows = DL(
        [
            Row(
                "https://almascience.org/dataPortal/member.uid___A001_X1.NGC_1365_sci.spw25.mfs.I.pbcor.fits",
                "application/fits",
                2e6,
            ),
            Row(
                "https://almascience.org/dataPortal/2017.1.00886.L_uid___A001_X1_001_of_001.tar",
                "application/x-tar",
                9e9,
            ),
            Row(
                "https://almascience.org/dataPortal/member.uid___A001_X1.NGC_1365_sci.spw25.cube.I.pbcor.fits",
                "application/fits",
                350e6,
            ),
            Row(
                "https://almascience.org/dataPortal/member.uid___A001_X1.NGC_1365_sci.spw23.cube.I.pbcor.fits",
                "application/fits",
                320e6,
            ),
            Row(
                "https://almascience.org/dataPortal/member.uid___A001_X1.NGC_1365_sci.spw23.cube.I.pb.fits.gz",
                "application/fits",
                40e6,
            ),
        ]
    )
    monkeypatch.setattr(
        adhoc.DatalinkResults, "from_result_url", staticmethod(lambda url, session=None: rows)
    )
    prods = list_products("https://almascience.org/datalink/sync?ID=uid://A001/X1")
    assert [p.kind for p in prods][:3] == ["cube", "cube", "cube"]
    assert prods[0].spw == 23 and prods[0].size_mb == 320.0 and "pbcor" in prods[0].name
    assert all(p.kind != "other" or "tar" not in p.name for p in prods)
    assert len(prods) == 4  # tarball excluded
    with pytest.raises(ValueError):
        list_products("https://evil.example/datalink")


def test_cube_store_caps_size_and_caches(tmp_path, monkeypatch):
    import httpx

    data, _, _ = make_alma_cube()
    served = []

    class FakeStream:
        def __init__(self, length):
            self.headers = {"content-length": str(length)}
            self.length = length

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self, chunk_size=1 << 20):
            served.append(1)
            yield data

    def fake_stream(self, method, url, headers=None):
        return FakeStream(len(data) if "small" in url else 10e9)

    monkeypatch.setattr(httpx.Client, "stream", fake_stream)
    store = CubeStore(cache_dir=tmp_path, max_mb=1.0)
    with pytest.raises(ValueError, match="download limit"):
        store.fetch("https://almascience.org/dataPortal/big.fits")
    path = store.fetch("https://almascience.org/dataPortal/small.fits")
    assert path.exists() and path.read_bytes() == data
    store.fetch("https://almascience.org/dataPortal/small.fits")
    assert len(served) == 1  # second fetch came from disk
    svc = CubeSpectrumService(store=store)
    spec = svc.spectrum("https://almascience.org/dataPortal/small.fits", RA0, DEC0, 0.6)
    assert spec.frequency_ghz and spec.source["product"] == "small.fits"
    assert svc.spectrum("https://almascience.org/dataPortal/small.fits", RA0, DEC0, 0.6).cached


@pytest.fixture
def client(monkeypatch, tmp_path):
    data, _, _ = make_alma_cube()

    class FakeStore(CubeStore):
        def fetch(self, url, max_mb=None):
            p = Path(tmp_path) / "x-cube.fits"
            p.write_bytes(data)
            return p

    svc = CubeSpectrumService(store=FakeStore(cache_dir=tmp_path))
    monkeypatch.setattr(cubes, "get_cube_service", lambda: svc)
    monkeypatch.setattr(
        cubes,
        "list_products",
        lambda url, timeout=60: [
            CubeProduct(
                "a.spw25.cube.I.pbcor.fits",
                "https://almascience.org/dataPortal/a.spw25.cube.I.pbcor.fits",
                350.0,
                25,
                "cube",
            ),
            CubeProduct(
                "a.spw23.cube.I.pbcor.fits",
                "https://almascience.org/dataPortal/a.spw23.cube.I.pbcor.fits",
                320.0,
                23,
                "cube",
            ),
        ],
    )
    assert spectra_router  # routers import cubes module functions at call time
    return TestClient(main.app)


def test_products_endpoint_marks_line_window(client):
    res = client.get(
        "/api/cubes/products",
        params={
            "datalink_url": "https://almascience.org/datalink/sync?ID=uid://A001/X1",
            "spw_ranges": "23:85.300-87.200; 25:229.000-230.900",
            "line_ghz": 229.287,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body[0]["spw"] == 25 and body[0]["contains_line"] is True
    assert body[1]["contains_line"] is False and body[1]["freq_min_ghz"] == 85.3
    assert (
        client.get(
            "/api/cubes/products", params={"datalink_url": "https://x.example/d"}
        ).status_code
        == 400
    )


def test_cube_spectrum_endpoint_and_radio_lines(client):
    res = client.get(
        "/api/spectrum/cube",
        params={
            "product_url": "https://almascience.org/dataPortal/a.spw25.cube.I.pbcor.fits",
            "ra": RA0,
            "dec": DEC0,
            "radius_arcsec": 0.6,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["frequency_ghz"][0] == pytest.approx(229.0, rel=1e-6)
    lines = client.get(
        "/api/lines/radio", params={"z": 0.005457, "fmin_ghz": 229, "fmax_ghz": 231}
    ).json()
    assert [line["name"] for line in lines] == ["CO(2-1)"]
    assert lines[0]["observed_frequency_ghz"] == pytest.approx(230.538 / 1.005457)
