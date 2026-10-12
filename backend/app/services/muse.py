"""ESO DataLink / SODA cutouts on MUSE cubes and 1D spectrum extraction."""

from __future__ import annotations

import io
import logging
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

import numpy as np

from app.schemas.spectra import Spectrum1D
from app.services.simbad import _TimeoutSession

log = logging.getLogger(__name__)

ALLOWED_DATALINK_HOSTS = ("archive.eso.org", "dataportal.eso.org", "eso.org")
MAX_CUTOUT_RADIUS_ARCSEC = 30.0
FITS_MAGIC = b"SIMPLE  ="


def _allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_DATALINK_HOSTS)


class SodaCutoutClient:
    """Fetch a spatial (and optionally spectral) cutout of a dataset through its DataLink."""

    def __init__(self, timeout: float = 300.0, max_cached: int = 4):
        self.timeout = timeout
        self._cache: OrderedDict[tuple, bytes] = OrderedDict()
        self._max_cached = max_cached
        self._lock = threading.Lock()

    def cutout(
        self,
        datalink_url: str,
        ra: float,
        dec: float,
        radius_arcsec: float,
        band_A: tuple[float, float] | None = None,
    ) -> bytes:
        key = (datalink_url, round(ra, 6), round(dec, 6), round(radius_arcsec, 2), band_A)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        data = self._fetch(datalink_url, ra, dec, radius_arcsec, band_A)
        with self._lock:
            self._cache[key] = data
            while len(self._cache) > self._max_cached:
                self._cache.popitem(last=False)
        return data

    def _fetch(self, datalink_url, ra, dec, radius_arcsec, band_A) -> bytes:
        import astropy.units as u
        from pyvo.dal.adhoc import DatalinkResults

        if not _allowed(datalink_url):
            raise ValueError("DataLink URL must point at the ESO archive")
        session = _TimeoutSession(self.timeout)
        dl = DatalinkResults.from_result_url(datalink_url, session=session)
        rows = list(dl.bysemantics("#this"))
        if not rows:
            raise ValueError("DataLink document has no '#this' dataset row")
        row = rows[0]
        if row._get_soda_resource() is None:  # noqa: SLF001 - pyvo offers no public check
            raise ValueError(
                "This dataset has no SODA cutout service; refusing to download the full cube"
            )
        circle = u.Quantity([ra, dec, radius_arcsec / 3600.0], u.deg)
        band = u.Quantity([band_A[0] * 1e-10, band_A[1] * 1e-10], u.m) if band_A else None
        t0 = time.monotonic()
        stream = row.processed(circle=circle, band=band)
        data = stream.read()
        log.info(
            "SODA cutout %s r=%.1f arcsec -> %d bytes in %.1fs",
            datalink_url,
            radius_arcsec,
            len(data),
            time.monotonic() - t0,
        )
        if not data:
            raise ValueError(
                "SODA returned no data (HTTP 204): the requested position lies outside this "
                "cube's footprint. Extract at the cube centre or pick a cube that covers "
                "the target."
            )
        if not data.startswith(FITS_MAGIC):
            head = data[:200].decode("utf-8", errors="replace")
            raise ValueError(f"SODA service did not return FITS: {head}")
        return data


# -- extraction -----------------------------------------------------------------------------------
def _find_cube(hdul):
    """(data HDU, variance HDU or None) of a MUSE-style cube."""
    data_hdu = None
    for hdu in hdul:
        name = (hdu.name or "").upper()
        if hdu.data is not None and getattr(hdu.data, "ndim", 0) == 3 and name == "DATA":
            data_hdu = hdu
            break
    if data_hdu is None:
        for hdu in hdul:
            if hdu.data is not None and getattr(hdu.data, "ndim", 0) == 3:
                data_hdu = hdu
                break
    if data_hdu is None:
        raise ValueError("No 3-D data array found in the cutout")
    stat = None
    for hdu in hdul:
        if (hdu.name or "").upper() in ("STAT", "VARIANCE", "VAR") and hdu.data is not None:
            if hdu.data.shape == data_hdu.data.shape:
                stat = hdu
            break
    return data_hdu, stat


def _spectral_axis_A(header, n: int) -> tuple[np.ndarray, list[str]]:
    """Wavelength of each channel in Angstrom."""
    warnings: list[str] = []
    try:
        from astropy.wcs import WCS

        w = WCS(header)
        spec = w.spectral
        pix = np.arange(n)
        coord = spec.pixel_to_world(pix)
        wl = coord.to_value("Angstrom")
        return np.asarray(wl, dtype=float), warnings
    except Exception as exc:  # noqa: BLE001 - fall back to linear keywords
        warnings.append(f"spectral WCS fallback: {type(exc).__name__}: {exc}")
    crval = float(header.get("CRVAL3", 0.0))
    cdelt = float(header.get("CD3_3", header.get("CDELT3", 1.0)))
    crpix = float(header.get("CRPIX3", 1.0))
    unit = str(header.get("CUNIT3", "Angstrom")).lower()
    wl = crval + (np.arange(n) + 1 - crpix) * cdelt
    if unit in ("m", "meter", "metre"):
        wl = wl * 1e10
    elif unit in ("nm", "nanometer"):
        wl = wl * 10.0
    elif unit in ("um", "micron", "micrometer"):
        wl = wl * 1e4
    return wl, warnings


def extract_spectrum(
    fits_bytes: bytes, ra: float, dec: float, radius_arcsec: float, method: str = "sum"
) -> Spectrum1D:
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astropy.io import fits
    from astropy.wcs import WCS
    from astropy.wcs.utils import proj_plane_pixel_scales

    warnings: list[str] = []
    with fits.open(io.BytesIO(fits_bytes), memmap=False) as hdul:
        data_hdu, stat_hdu = _find_cube(hdul)
        cube = np.asarray(data_hdu.data, dtype=float)
        var = np.asarray(stat_hdu.data, dtype=float) if stat_hdu is not None else None
        header = data_hdu.header
        nz, ny, nx = cube.shape
        wl, w_warn = _spectral_axis_A(header, nz)
        warnings += w_warn

        # Aperture mask in pixel space.
        try:
            wcs = WCS(header).celestial
            x0, y0 = wcs.world_to_pixel(SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs"))
            scales = proj_plane_pixel_scales(wcs) * 3600.0  # arcsec / pixel
            pix_scale = float(np.mean(np.abs(scales)))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"celestial WCS unavailable ({exc}); using the cutout centre")
            x0, y0 = (nx - 1) / 2, (ny - 1) / 2
            pix_scale = float(abs(header.get("CD2_2", header.get("CDELT2", 0.2 / 3600))) * 3600)
        r_pix = radius_arcsec / pix_scale if pix_scale else 1.0
        yy, xx = np.mgrid[0:ny, 0:nx]
        mask = (xx - float(x0)) ** 2 + (yy - float(y0)) ** 2 <= r_pix**2
        if not mask.any():
            ix, iy = int(round(float(x0))), int(round(float(y0)))
            ix, iy = min(max(ix, 0), nx - 1), min(max(iy, 0), ny - 1)
            mask[iy, ix] = True
            warnings.append("aperture smaller than a pixel; using the nearest spaxel")
        n_spax = int(mask.sum())

        sub = cube[:, mask]  # (nz, n_spax)
        valid = np.isfinite(sub)
        n_valid = valid.sum(axis=1)
        summed = np.where(valid, sub, 0.0).sum(axis=1)
        if method == "mean":
            with np.errstate(invalid="ignore", divide="ignore"):
                flux = np.where(n_valid > 0, summed / np.maximum(n_valid, 1), np.nan)
        else:
            flux = np.where(n_valid > 0, summed, np.nan)
        error = None
        if var is not None:
            vsub = var[:, mask]
            vvalid = np.isfinite(vsub) & valid
            vsum = np.where(vvalid, vsub, 0.0).sum(axis=1)
            err = np.sqrt(vsum)
            if method == "mean":
                err = err / np.maximum(n_valid, 1)
            error = [None if not np.isfinite(e) else float(e) for e in err]

        unit = str(header.get("BUNIT", "")) or "unknown"
        if method == "sum":
            unit_out = unit
        else:
            unit_out = f"{unit} per spaxel"

        source: dict[str, Any] = {
            "object": header.get("OBJECT"),
            "date_obs": header.get("DATE-OBS"),
            "instrument": header.get("INSTRUME"),
            "exptime": header.get("EXPTIME"),
            "cutout_shape": f"{nz}x{ny}x{nx}",
        }
        return Spectrum1D(
            wavelength_A=[float(x) for x in wl],
            flux=[None if not np.isfinite(f) else float(f) for f in flux],  # type: ignore[list-item]
            error=error,
            flux_unit=unit_out,
            method=method,
            n_spaxels=n_spax,
            aperture_radius_arcsec=radius_arcsec,
            center_ra_deg=ra,
            center_dec_deg=dec,
            pixel_scale_arcsec=pix_scale,
            n_channels=int(nz),
            source=source,
            warnings=warnings,
        )


class MuseSpectrumService:
    def __init__(self, cutouts: SodaCutoutClient | None = None, cache_ttl: int = 3600):
        self.cutouts = cutouts or SodaCutoutClient()
        self.cache_ttl = cache_ttl
        self._cache: dict[tuple, tuple[float, Spectrum1D]] = {}
        self._lock = threading.Lock()

    def spectrum(
        self,
        datalink_url: str,
        ra: float,
        dec: float,
        radius_arcsec: float,
        method: str = "sum",
        band_A: tuple[float, float] | None = None,
    ) -> Spectrum1D:
        radius_arcsec = min(max(radius_arcsec, 0.2), MAX_CUTOUT_RADIUS_ARCSEC)
        key = (datalink_url, round(ra, 6), round(dec, 6), round(radius_arcsec, 2), method, band_A)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1].model_copy(update={"cached": True})
        # Fetch a slightly larger cutout so the aperture edge is fully covered.
        data = self.cutouts.cutout(datalink_url, ra, dec, radius_arcsec + 1.0, band_A)
        spec = extract_spectrum(data, ra, dec, radius_arcsec, method=method)
        spec.source["datalink_url"] = datalink_url
        with self._lock:
            self._cache[key] = (now + self.cache_ttl, spec)
        return spec


_service: MuseSpectrumService | None = None
_service_lock = threading.Lock()


def get_muse_service() -> MuseSpectrumService:
    global _service
    with _service_lock:
        if _service is None:
            from app.config import get_settings

            _service = MuseSpectrumService(
                cutouts=SodaCutoutClient(timeout=max(get_settings().remote_timeout * 10, 120))
            )
        return _service
