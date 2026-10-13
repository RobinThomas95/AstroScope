"""ESO DataLink / SODA cutouts on MUSE cubes and 1D spectrum extraction."""

from __future__ import annotations

import gzip
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
        data = self._request_with_retry(row, circle, band)
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

    def _request_with_retry(self, row, circle, band, attempts: int = 3) -> bytes:
        """ESO's data portal sometimes answers 502/503/504 when a cutout job times out behind
        its proxy; those are worth a couple of retries before giving up."""
        from pyvo.dal.exceptions import DALServiceError

        delay = 5.0
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                return row.processed(circle=circle, band=band).read()
            except DALServiceError as exc:
                msg = str(exc)
                transient = any(code in msg for code in ("502", "503", "504"))
                last = exc
                if not transient or attempt == attempts:
                    break
                log.warning("SODA attempt %d failed (%s); retrying in %.0fs", attempt, msg, delay)
                time.sleep(delay)
                delay *= 2
        assert last is not None
        msg = str(last)
        if any(code in msg for code in ("502", "503", "504")):
            raise ValueError(
                "ESO's SODA service failed on its side (" + msg.split(" for url")[0] + "). "
                "This happens for large mosaics when the cutout job times out; try again in a "
                "minute, use a smaller aperture, or pick another cube."
            ) from last
        raise last


# -- extraction -----------------------------------------------------------------------------------
def _ndim(hdu) -> int:
    """Dimensionality from the header, without loading the data."""
    try:
        return int(hdu.header.get("NAXIS", 0)) if getattr(hdu, "is_image", False) else 0
    except Exception:  # noqa: BLE001
        return 0


def _find_cube(hdul):
    """(data HDU, variance HDU or None) of a MUSE-style cube."""
    data_hdu = None
    for hdu in hdul:
        name = (hdu.name or "").upper()
        if _ndim(hdu) in (3, 4) and name == "DATA":
            data_hdu = hdu
            break
    if data_hdu is None:
        for hdu in hdul:
            if _ndim(hdu) in (3, 4):
                data_hdu = hdu
                break
    if data_hdu is None:
        raise ValueError("No 3-D data array found in the cutout")
    stat = None
    for hdu in hdul:
        if (hdu.name or "").upper() in ("STAT", "VARIANCE", "VAR") and _ndim(hdu) > 0:
            if tuple(hdu.shape) == tuple(data_hdu.shape):
                stat = hdu
            break
    return data_hdu, stat


def _read_box(hdu, key: tuple) -> np.ndarray:
    """Read one sub-array of an image HDU, through `section` when the HDU is file-backed so a
    multi-GB cube is never loaded whole."""
    try:
        return np.asarray(hdu.section[key], dtype=float)
    except Exception:  # noqa: BLE001  (in-memory HDUs have no file section)
        return np.asarray(hdu.data[key], dtype=float)


def _spectral_axis_A(header, n: int) -> tuple[np.ndarray, list[str]]:
    """Wavelength of each channel in Angstrom (frequency and velocity axes are converted)."""
    warnings: list[str] = []
    c = 299792458.0
    try:
        from astropy.wcs import WCS

        spec = WCS(header).spectral
        coord = spec.pixel_to_world(np.arange(n))
        unit = str(coord.unit)
        if unit in ("m / s", "km / s"):
            raise ValueError("velocity axis")
        wl = coord.to_value("Angstrom")
        return np.asarray(wl, dtype=float), warnings
    except Exception as exc:  # noqa: BLE001 - fall back to linear keywords
        warnings.append(f"spectral WCS fallback: {type(exc).__name__}: {exc}")
    # Linear axis from keywords; find which axis is spectral (3 for MUSE/ALMA cubes).
    axis = 3
    ctype = str(header.get(f"CTYPE{axis}", "WAVE")).upper()
    crval = float(header.get(f"CRVAL{axis}", 0.0))
    cdelt = float(header.get(f"CD{axis}_{axis}", header.get(f"CDELT{axis}", 1.0)))
    crpix = float(header.get(f"CRPIX{axis}", 1.0))
    unit = str(header.get(f"CUNIT{axis}", "")).lower().strip()
    values = crval + (np.arange(n) + 1 - crpix) * cdelt
    if ctype.startswith("FREQ"):
        scale = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9}.get(unit or "hz", 1.0)
        freq = values * scale
        return c / freq * 1e10, warnings
    if ctype.startswith(("VRAD", "VELO", "VOPT")):
        rest = header.get("RESTFRQ") or header.get("RESTFREQ")
        if not rest:
            warnings.append("velocity axis without RESTFRQ: cannot convert to wavelength")
            return values, warnings
        v = values * (1e3 if unit.startswith("km") else 1.0)
        f0 = float(rest)
        freq = f0 / (1 + v / c) if ctype.startswith("VOPT") else f0 * (1 - v / c)
        return c / freq * 1e10, warnings
    wl = values
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
    from astropy.io import fits

    if fits_bytes[:2] == b"\x1f\x8b":  # gzipped FITS
        fits_bytes = gzip.decompress(fits_bytes)
    if not fits_bytes.startswith(b"SIMPLE"):
        head = fits_bytes[:120].decode("latin-1", "replace").strip()
        raise ValueError(f"Payload is not a FITS file (starts with: {head!r})")
    with fits.open(io.BytesIO(fits_bytes), memmap=False) as hdul:
        return extract_from_hdul(hdul, ra, dec, radius_arcsec, method=method)


def extract_from_hdul(
    hdul, ra: float, dec: float, radius_arcsec: float, method: str = "sum"
) -> Spectrum1D:
    """Aperture spectrum from an open cube; only the pixel box around the aperture is read."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astropy.wcs import WCS
    from astropy.wcs.utils import proj_plane_pixel_scales

    warnings: list[str] = []
    data_hdu, stat_hdu = _find_cube(hdul)
    header = data_hdu.header
    shape = tuple(int(n) for n in data_hdu.shape)
    stokes = len(shape) == 4  # radio cubes carry a Stokes axis first: take Stokes I
    if stokes:
        warnings.append("4-D cube: used the first Stokes plane")
    nz, ny, nx = shape[-3:]
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
    x0f, y0f = float(x0), float(y0)
    # Pixel box around the aperture, clipped to the cube.
    xa, xb = max(int(np.floor(x0f - r_pix)), 0), min(int(np.ceil(x0f + r_pix)) + 1, nx)
    ya, yb = max(int(np.floor(y0f - r_pix)), 0), min(int(np.ceil(y0f + r_pix)) + 1, ny)
    if xa >= xb or ya >= yb:  # aperture entirely outside: nearest edge pixel
        xa, ya = min(max(int(round(x0f)), 0), nx - 1), min(max(int(round(y0f)), 0), ny - 1)
        xb, yb = xa + 1, ya + 1
    yy, xx = np.mgrid[ya:yb, xa:xb]
    mask = (xx - x0f) ** 2 + (yy - y0f) ** 2 <= r_pix**2
    if not mask.any():
        ix, iy = int(round(x0f)), int(round(y0f))
        ix, iy = min(max(ix, xa), xb - 1), min(max(iy, ya), yb - 1)
        mask[iy - ya, ix - xa] = True
        warnings.append("aperture smaller than a pixel; using the nearest spaxel")
    n_spax = int(mask.sum())

    key = (
        (0, slice(None), slice(ya, yb), slice(xa, xb))
        if stokes
        else (slice(None), slice(ya, yb), slice(xa, xb))
    )
    cube = _read_box(data_hdu, key)
    var = _read_box(stat_hdu, key) if stat_hdu is not None else None
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
        flux=[None if not np.isfinite(f) else float(f) for f in flux],
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
