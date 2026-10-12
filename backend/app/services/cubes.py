"""Spectral cubes without a SODA service (ALMA): list DataLink products, download one under a
size cap with a disk cache, and extract an aperture spectrum with a frequency axis."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.schemas.spectra import Spectrum1D
from app.services.muse import extract_spectrum

log = logging.getLogger(__name__)

ALLOWED_HOSTS = (
    "almascience.org",
    "almascience.eso.org",
    "almascience.nrao.edu",
    "almascience.nao.ac.jp",
    "archive.eso.org",
    "dataportal.eso.org",
)
C_KM_S = 299792.458
DEFAULT_MAX_MB = 600.0
CACHE_DIR = Path(tempfile.gettempdir()) / "astroscope-cubes"
CACHE_LIMIT_BYTES = 6 * 1024**3
_SPW_RE = re.compile(r"spw(\d+)", re.IGNORECASE)


def _allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


@dataclass
class CubeProduct:
    name: str
    url: str
    size_mb: float | None
    spw: int | None
    kind: str  # cube | image | other
    semantics: str | None = None
    content_type: str | None = None


def list_products(datalink_url: str, timeout: float = 60.0) -> list[CubeProduct]:
    """FITS products listed by a DataLink document, cubes first."""
    from pyvo.dal.adhoc import DatalinkResults

    from app.services.simbad import _TimeoutSession

    if not _allowed(datalink_url):
        raise ValueError("DataLink URL must point at the ALMA or ESO archive")
    dl = DatalinkResults.from_result_url(datalink_url, session=_TimeoutSession(timeout))
    products: list[CubeProduct] = []
    for row in dl:
        url = row.access_url
        if not url:
            continue
        name = os.path.basename(urlparse(url).path)
        ctype = (row.content_type or "").lower()
        lname = name.lower()
        if "fits" not in ctype and not lname.endswith((".fits", ".fits.gz")):
            continue
        if "cube" in lname:
            kind = "cube"
        elif any(k in lname for k in (".mfs.", "cont", "mom0", "mom1", "pbcor.fits")):
            kind = "image"
        else:
            kind = "other"
        m = _SPW_RE.search(name)
        size = row.content_length
        products.append(
            CubeProduct(
                name=name,
                url=url,
                size_mb=round(float(size) / 1e6, 1) if size else None,
                spw=int(m.group(1)) if m else None,
                kind=kind,
                semantics=row.semantics,
                content_type=row.content_type,
            )
        )
    # cubes first, primary-beam-corrected first, then by size
    products.sort(key=lambda p: (p.kind != "cube", "pbcor" not in p.name.lower(), p.size_mb or 0))
    return products


class CubeStore:
    """Download cube products once, keep them on disk, evict oldest beyond the cache limit."""

    def __init__(
        self, cache_dir: Path = CACHE_DIR, max_mb: float = DEFAULT_MAX_MB, timeout: float = 600.0
    ):
        self.cache_dir = cache_dir
        self.max_mb = max_mb
        self.timeout = timeout
        self._lock = threading.Lock()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        name = os.path.basename(urlparse(url).path) or "cube.fits"
        return self.cache_dir / f"{digest}-{name}"

    def fetch(self, url: str, max_mb: float | None = None) -> Path:
        if not _allowed(url):
            raise ValueError("Product URL must point at the ALMA or ESO archive")
        limit = (max_mb or self.max_mb) * 1e6
        path = self.path_for(url)
        if path.exists() and path.stat().st_size > 0:
            os.utime(path, None)
            return path
        tmp = path.with_suffix(path.suffix + ".part")
        t0 = time.monotonic()
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            with client.stream("GET", url, headers={"User-Agent": "AstroScope/0.1"}) as res:
                res.raise_for_status()
                length = res.headers.get("content-length")
                if length and float(length) > limit:
                    raise ValueError(
                        f"Product is {float(length) / 1e6:.0f} MB, above the {limit / 1e6:.0f} MB "
                        "download limit; raise the limit or pick a smaller product"
                    )
                written = 0
                with open(tmp, "wb") as fh:
                    for chunk in res.iter_bytes(chunk_size=1 << 20):
                        written += len(chunk)
                        if written > limit:
                            fh.close()
                            tmp.unlink(missing_ok=True)
                            raise ValueError(
                                f"Download exceeded the {limit / 1e6:.0f} MB limit; aborted"
                            )
                        fh.write(chunk)
        tmp.replace(path)
        log.info("Downloaded %s (%.0f MB) in %.0fs", url, written / 1e6, time.monotonic() - t0)
        self._evict()
        return path

    def _evict(self) -> None:
        with self._lock:
            files = sorted(self.cache_dir.glob("*"), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            while files and total > CACHE_LIMIT_BYTES:
                victim = files.pop(0)
                total -= victim.stat().st_size
                victim.unlink(missing_ok=True)


def frequency_axis_ghz(wavelength_A: list[float]) -> list[float | None]:
    return [C_KM_S * 1e3 / (w * 1e-10) / 1e9 if w and w > 0 else None for w in wavelength_A]


def extract_from_file(
    path: Path, ra: float, dec: float, radius_arcsec: float, method: str = "sum"
) -> Spectrum1D:
    data = path.read_bytes()
    spec = extract_spectrum(data, ra, dec, radius_arcsec, method=method)
    spec.frequency_ghz = frequency_axis_ghz(spec.wavelength_A)
    spec.source["product"] = path.name.split("-", 1)[-1]
    return spec


class CubeSpectrumService:
    def __init__(self, store: CubeStore | None = None, cache_ttl: int = 3600):
        self.store = store or CubeStore()
        self.cache_ttl = cache_ttl
        self._cache: dict[tuple, tuple[float, Spectrum1D]] = {}
        self._lock = threading.Lock()

    def spectrum(
        self,
        product_url: str,
        ra: float,
        dec: float,
        radius_arcsec: float,
        method: str = "sum",
        max_mb: float | None = None,
    ) -> Spectrum1D:
        key = (product_url, round(ra, 6), round(dec, 6), round(radius_arcsec, 2), method)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1].model_copy(update={"cached": True})
        path = self.store.fetch(product_url, max_mb=max_mb)
        spec = extract_from_file(path, ra, dec, radius_arcsec, method=method)
        spec.source["product_url"] = product_url
        with self._lock:
            self._cache[key] = (now + self.cache_ttl, spec)
        return spec


_service: CubeSpectrumService | None = None
_service_lock = threading.Lock()


def get_cube_service() -> CubeSpectrumService:
    global _service
    with _service_lock:
        if _service is None:
            from app.config import get_settings

            s = get_settings()
            _service = CubeSpectrumService(
                store=CubeStore(max_mb=s.cube_download_max_mb, timeout=s.remote_timeout * 30)
            )
        return _service
