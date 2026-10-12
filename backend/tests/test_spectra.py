import io

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import main
from app.routers import spectra as spectra_router
from app.schemas.archives import HIDetection
from app.services.hiprofile import profile_from_detection
from app.services.lines import spectral_lines
from app.services.muse import MuseSpectrumService, extract_spectrum

RA0, DEC0 = 53.4015, -36.1404
NZ, NY, NX = 200, 21, 21
PIX = 0.2  # arcsec


def make_cube(with_stat=True, extname="DATA"):
    """A MUSE-like cube: 4750-6750 A, a bright pixel at the centre with an emission line."""
    from astropy.io import fits

    wl0, dwl = 4750.0, 10.0
    cube = np.full((NZ, NY, NX), 1.0, dtype=np.float32)
    k = int((6564.61 * (1 + 0.005457) - wl0) / dwl)  # redshifted H-alpha channel
    cube[:, NY // 2, NX // 2] = 10.0
    cube[k, NY // 2, NX // 2] = 100.0
    cube[5, 0, 0] = np.nan
    hdr = fits.Header()
    hdr["BUNIT"] = "10**(-20)*erg/s/cm**2/Angstrom"
    hdr["OBJECT"] = "NGC1365"
    hdr["CTYPE1"], hdr["CTYPE2"], hdr["CTYPE3"] = "RA---TAN", "DEC--TAN", "AWAV"
    hdr["CUNIT1"], hdr["CUNIT2"], hdr["CUNIT3"] = "deg", "deg", "Angstrom"
    hdr["CRPIX1"], hdr["CRPIX2"], hdr["CRPIX3"] = NX // 2 + 1, NY // 2 + 1, 1.0
    hdr["CRVAL1"], hdr["CRVAL2"], hdr["CRVAL3"] = RA0, DEC0, wl0
    hdr["CD1_1"], hdr["CD1_2"] = -PIX / 3600, 0.0
    hdr["CD2_1"], hdr["CD2_2"] = 0.0, PIX / 3600
    hdr["CD3_3"] = dwl
    hdus = [fits.PrimaryHDU(), fits.ImageHDU(cube, header=hdr, name=extname)]
    if with_stat:
        hdus.append(fits.ImageHDU(np.full_like(cube, 0.25), header=hdr, name="STAT"))
    buf = io.BytesIO()
    fits.HDUList(hdus).writeto(buf)
    return buf.getvalue(), k


def test_extract_sum_spectrum_with_errors():
    data, k = make_cube()
    spec = extract_spectrum(data, RA0, DEC0, radius_arcsec=0.5)  # r = 2.5 px -> 21 spaxels
    assert spec.n_channels == NZ and len(spec.wavelength_A) == NZ
    assert spec.wavelength_A[0] == pytest.approx(4750.0) and spec.wavelength_A[1] == pytest.approx(
        4760.0
    )
    assert spec.n_spaxels == 21
    assert spec.pixel_scale_arcsec == pytest.approx(PIX, rel=1e-3)
    # 20 background spaxels at 1 + central at 10 (continuum) / 100 (line)
    assert spec.flux[0] == pytest.approx(30.0)
    assert spec.flux[k] == pytest.approx(120.0)
    assert spec.error[0] == pytest.approx(np.sqrt(21 * 0.25))
    assert spec.flux_unit.startswith("10**(-20)")
    assert spec.method == "sum" and spec.source["object"] == "NGC1365"


def test_extract_mean_and_tiny_aperture_and_nan_handling():
    data, k = make_cube(with_stat=False)
    # 0.1 arcsec off a pixel centre with a 0.01 arcsec aperture: no pixel inside -> nearest
    ra_off = RA0 + 0.1 / 3600 / np.cos(np.radians(DEC0))
    spec = extract_spectrum(data, ra_off, DEC0, radius_arcsec=0.01, method="mean")
    assert spec.n_spaxels == 1 and spec.flux[k] == pytest.approx(100.0)
    assert spec.error is None
    assert any("nearest spaxel" in w for w in spec.warnings)
    # NaN in a corner spaxel is excluded, not propagated
    big = extract_spectrum(data, RA0, DEC0, radius_arcsec=10.0)
    assert big.n_spaxels == NX * NY
    assert big.flux[5] == pytest.approx(
        NX * NY - 1 - 1 + 10
    )  # all 1s except NaN corner and centre 10


def test_extract_without_named_extension():
    data, _ = make_cube(with_stat=False, extname="SCI")
    spec = extract_spectrum(data, RA0, DEC0, 1.0)
    assert spec.n_channels == NZ


def test_lines_redshift_and_window():
    all_lines = spectral_lines()
    assert any(line.name == "Hα" for line in all_lines)
    z = 0.005457
    ha = next(line for line in spectral_lines(z) if line.name == "Hα")
    assert ha.observed_wavelength_A == pytest.approx(6564.61 * (1 + z))
    windowed = spectral_lines(z, 4750, 9350)  # MUSE range
    assert all(4750 <= line.observed_wavelength_A <= 9350 for line in windowed)
    assert not any(line.name == "Hδ" for line in windowed)


def test_hi_profile_matches_catalogue_values():
    det = HIDetection(
        survey="HIPASS",
        catalog="VIII/73",
        source_id="J0333-36",
        velocity_kms=1636.4,
        w50_kms=373.7,
        w20_kms=406.8,
        flux_jy_kms=174.7,
    )
    prof = profile_from_detection(det)
    v = np.array(prof.velocity_kms)
    f = np.array(prof.flux_jy)
    assert prof.model == "double-horn template" and "Not an observed spectrum" in prof.note
    assert float(np.trapezoid(f, v)) == pytest.approx(174.7, rel=1e-3)
    # half-power width close to W50, symmetric about Vsys
    half = f >= 0.5 * f.max()
    assert v[half].max() - v[half].min() == pytest.approx(373.7, abs=15)
    assert (v[half].max() + v[half].min()) / 2 == pytest.approx(1636.4, abs=3)
    with pytest.raises(ValueError):
        profile_from_detection(
            HIDetection(survey="x", catalog="y", velocity_kms=None, w50_kms=None)
        )


class FakeCutouts:
    def __init__(self):
        self.calls = []
        self.data, _ = make_cube()

    def cutout(self, url, ra, dec, radius_arcsec, band_A=None):
        self.calls.append((url, radius_arcsec, band_A))
        return self.data


@pytest.fixture
def client(monkeypatch):
    svc = MuseSpectrumService(cutouts=FakeCutouts())
    monkeypatch.setattr(spectra_router, "get_muse_service", lambda: svc)
    from tests.test_archives import FakeHI

    monkeypatch.setattr(spectra_router, "get_hi_matcher", lambda: FakeHI())
    return TestClient(main.app), svc


def test_spectrum_endpoint(client):
    c, svc = client
    url = "https://archive.eso.org/datalink/links?ID=ivo://eso.org/ID?ADP.2019-01-30T20:25:13.984"
    res = c.get(
        "/api/spectrum/muse",
        params={"datalink_url": url, "ra": RA0, "dec": DEC0, "radius_arcsec": 0.5},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["n_spaxels"] == 21 and body["flux"][0] == pytest.approx(30.0)
    assert body["source"]["datalink_url"] == url
    assert svc.cutouts.calls[0][1] == pytest.approx(1.5)  # aperture + 1 arcsec margin
    # cached on repeat
    again = c.get(
        "/api/spectrum/muse",
        params={"datalink_url": url, "ra": RA0, "dec": DEC0, "radius_arcsec": 0.5},
    ).json()
    assert again["cached"] is True and len(svc.cutouts.calls) == 1
    # non-ESO URLs are refused
    bad = c.get(
        "/api/spectrum/muse",
        params={"datalink_url": "https://evil.example/x", "ra": RA0, "dec": DEC0},
    )
    assert bad.status_code == 400
    assert (
        c.get(
            "/api/spectrum/muse",
            params={
                "datalink_url": url,
                "ra": RA0,
                "dec": DEC0,
                "band_min_A": 7000,
                "band_max_A": 6000,
            },
        ).status_code
        == 400
    )


def test_lines_and_hi_profile_endpoints(client):
    c, _ = client
    lines = c.get("/api/lines", params={"z": 0.005, "wmin": 4750, "wmax": 9350}).json()
    assert any(line["name"] == "Hα" for line in lines)
    prof = c.get("/api/hi/profile", params={"ra": 53.4, "dec": -36.14}).json()
    # HIPASS fake row wins: it has an integrated flux and a W20 (ALFALFA fake has no W20)
    assert prof["source_id"] == "J0333-36" and prof["w50_kms"] == 373.7
    alf = c.get("/api/hi/profile", params={"ra": 53.4, "dec": -36.14, "source_id": "123"}).json()
    assert alf["w50_kms"] == 350 and alf["integrated_flux_jy_kms"] == 12.3
    assert (
        c.get(
            "/api/hi/profile", params={"ra": 53.4, "dec": -36.14, "source_id": "nope"}
        ).status_code
        == 404
    )


def test_health_reports_spectra():
    assert TestClient(main.app).get("/api/health").json()["modules"]["spectra"] is True
