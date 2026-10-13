"""Spectral cubes without a SODA service (ALMA): list DataLink products, download one under a
size cap with a disk cache, and extract an aperture spectrum with a frequency axis."""

from __future__ import annotations

import gzip
import hashlib
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

from app.schemas.spectra import Spectrum1D
from app.services.muse import extract_from_hdul, extract_spectrum

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
C_M_S = 299792458.0
# ALMA mirrors that expose the SODA cutout service (REQUEST=queryData&ID=<product file name>)
SODA_HOSTS = ("almascience.eso.org", "almascience.org", "almascience.nrao.edu")
USER_AGENT = {"User-Agent": "AstroScope/0.1"}
DEFAULT_MAX_MB = 600.0
CACHE_DIR = Path(tempfile.gettempdir()) / "astroscope-cubes"
CACHE_LIMIT_BYTES = 6 * 1024**3
# ".spw25." (archive product IDs) or "spw0_229397MHz" (ARI-L / pipeline product names, where the
# number is a local window index and the centre frequency follows it)
_SPW_RE = re.compile(r"spw(\d+)(?:_(\d+(?:\.\d+)?)(MHz|GHz))?", re.IGNORECASE)
_INTENT_RE = re.compile(r"_(sci|chk|ph|bp|amp|flux|pol|cont)[._]", re.IGNORECASE)
_CAL_INTENTS = {"chk", "ph", "bp", "amp", "flux", "pol"}
_AUX_TOKENS = (
    ".mask.",
    ".pb.",
    ".psf.",
    ".residual.",
    ".model.",
    ".alpha.",
    ".sumwt.",
    ".weight.",
    ".flux.",
)
_KIND_RANK = {"cube": 0, "image": 1, "auxiliary": 2, "calibrator": 3, "other": 4, "package": 5}


def _allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


@dataclass
class CubeProduct:
    name: str
    url: str
    size_mb: float | None
    spw: int | None
    kind: str  # cube | image | auxiliary | calibrator | package | other
    semantics: str | None = None
    content_type: str | None = None
    freq_center_ghz: float | None = None
    intent: str | None = None


def classify_product(name: str) -> tuple[str, int | None, float | None, str | None]:
    """(kind, spw, centre frequency in GHz, intent) from an ALMA/ESO product file name."""
    lname = name.lower()
    m = _SPW_RE.search(name)
    spw = int(m.group(1)) if m else None
    centre = None
    if m and m.group(2):
        centre = float(m.group(2)) / (1e3 if m.group(3).lower() == "mhz" else 1.0)
    im = _INTENT_RE.search(name)
    intent = im.group(1).lower() if im else None
    if lname.endswith((".tar", ".tar.gz", ".tgz", ".zip")):
        kind = "package"
    elif "fits" not in lname:
        kind = "other"
    elif "cube" in lname:
        if intent in _CAL_INTENTS:
            kind = "calibrator"
        elif any(tok in lname for tok in _AUX_TOKENS):
            kind = "auxiliary"
        else:
            kind = "cube"
    elif any(k in lname for k in (".mfs.", "cont", "mom0", "mom1", "pbcor.fits")):
        kind = "image"
    else:
        kind = "other"
    return kind, spw, centre, intent


def _product_from_row(row) -> CubeProduct | None:
    url = row.access_url
    if not url:
        return None
    name = os.path.basename(urlparse(url).path)
    ctype = (row.content_type or "").lower()
    lname = name.lower()
    is_fits = "fits" in ctype or lname.endswith((".fits", ".fits.gz", ".fits.fz"))
    kind, spw, centre, intent = classify_product(name)
    if not is_fits and kind != "package":
        return None
    if is_fits and kind == "package":
        kind = "other"
    size = row.content_length
    return CubeProduct(
        name=name,
        url=url,
        size_mb=round(float(size) / 1e6, 1) if size else None,
        spw=spw,
        kind=kind,
        semantics=row.semantics,
        content_type=row.content_type,
        freq_center_ghz=centre,
        intent=intent,
    )


def _is_recursive_datalink(row) -> bool:
    """ALMA lists the files inside a tar package through a nested DataLink document."""
    ctype = (row.content_type or "").lower()
    return "content=datalink" in ctype and bool(row.access_url)


def list_products(
    datalink_url: str, timeout: float = 60.0, expand_packages: bool = True, max_nested: int = 24
) -> list[CubeProduct]:
    """Products listed by a DataLink document: science cubes first, then images, auxiliary
    cubes (mask/pb/psf/...), calibrator cubes and packaged tarballs (kind "package"). Nested
    DataLink documents (the contents of ALMA tar packages) are expanded one level."""
    from pyvo.dal.adhoc import DatalinkResults

    from app.services.simbad import _TimeoutSession

    if not _allowed(datalink_url):
        raise ValueError("DataLink URL must point at the ALMA or ESO archive")
    session = _TimeoutSession(timeout)
    dl = DatalinkResults.from_result_url(datalink_url, session=session)
    products: list[CubeProduct] = []
    nested: list[str] = []
    for row in dl:
        if expand_packages and _is_recursive_datalink(row):
            if row.access_url not in nested and _allowed(row.access_url):
                nested.append(row.access_url)
            continue
        p = _product_from_row(row)
        if p is not None:
            products.append(p)

    def _nested(url: str) -> list[CubeProduct]:
        try:
            sub = DatalinkResults.from_result_url(url, session=session)
        except Exception as exc:  # noqa: BLE001
            log.warning("Nested DataLink %s failed: %s", url, exc)
            return []
        return [p for p in (_product_from_row(r) for r in sub) if p is not None]

    if nested:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for chunk in pool.map(_nested, nested[:max_nested]):
                products.extend(chunk)
    seen: set[str] = set()
    unique = []
    for p in products:
        if p.url in seen:
            continue
        seen.add(p.url)
        unique.append(p)
    unique.sort(key=product_sort_key)
    return unique


def product_sort_key(p: CubeProduct) -> tuple:
    """Science cubes first, primary-beam-corrected first, then by size."""
    return (_KIND_RANK.get(p.kind, 9), "pbcor" not in p.name.lower(), p.size_mb or 0)


Progress = Callable[[int, int | None], None]


class CutoutUnavailable(Exception):
    """The SODA service could not serve this product; the caller may download it instead."""


def alma_soda_url(
    product_url: str,
    ra: float,
    dec: float,
    radius_arcsec: float,
    band_ghz: tuple[float, float] | None = None,
    host: str | None = None,
) -> str:
    """ALMA SODA sync request for a spatial (and optional spectral) cutout of one product.
    BAND is in metres, as the SODA standard requires."""
    name = os.path.basename(urlparse(product_url).path)
    host = host or urlparse(product_url).hostname or SODA_HOSTS[0]
    params = [
        ("REQUEST", "queryData"),
        ("ID", name),
        ("POS", f"CIRCLE {ra:.6f} {dec:.6f} {radius_arcsec / 3600.0:.6f}"),
    ]
    if band_ghz:
        lo, hi = sorted(band_ghz)
        if lo > 0 and hi > 0:
            params.append(("BAND", f"{C_M_S / (hi * 1e9):.12f} {C_M_S / (lo * 1e9):.12f}"))
    return f"https://{host}/soda/sync?{urlencode(params)}"


class AlmaCutoutClient:
    """Fetch an aperture (+ band) cutout of an ALMA cube product through SODA, trying the
    product's own mirror first and then the other mirrors."""

    def __init__(self, timeout: float = 300.0):
        self.timeout = timeout

    def fetch(
        self,
        product_url: str,
        ra: float,
        dec: float,
        radius_arcsec: float,
        band_ghz: tuple[float, float] | None = None,
    ) -> bytes:
        own = urlparse(product_url).hostname or ""
        hosts = [own] + [h for h in SODA_HOSTS if h != own]
        errors: list[str] = []
        attempts: list[tuple[str, tuple[float, float] | None]] = []
        for host in hosts:
            attempts.append((host, band_ghz))
            if band_ghz:  # a rejected BAND should not cost the whole cutout
                attempts.append((host, None))
        for host, band in attempts:
            url = alma_soda_url(product_url, ra, dec, radius_arcsec, band, host=host)
            t0 = time.monotonic()
            try:
                with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                    res = client.get(url, headers=USER_AGENT)
            except httpx.HTTPError as exc:
                errors.append(f"{host}{' (band)' if band else ''}: {type(exc).__name__}")
                continue
            if res.status_code == 204 or (res.status_code == 200 and not res.content):
                raise ValueError(
                    "ALMA SODA returned no data (HTTP 204): the position lies outside this "
                    "cube's footprint or the band is outside its spectral range. Extract at "
                    "the cube centre, widen the band, or pick another product."
                )
            if res.status_code != 200:
                snippet = res.content[:160].decode("utf-8", "replace").strip().replace("\n", " ")
                errors.append(
                    f"{host}{' (band)' if band else ''}: HTTP {res.status_code} {snippet}"
                )
                continue
            data = res.content
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            if not data.startswith(b"SIMPLE"):
                snippet = data[:160].decode("utf-8", "replace").strip().replace("\n", " ")
                errors.append(f"{host}{' (band)' if band else ''}: non-FITS reply {snippet}")
                continue
            log.info(
                "ALMA SODA cutout %s r=%.1f arcsec -> %d bytes in %.1fs",
                url,
                radius_arcsec,
                len(data),
                time.monotonic() - t0,
            )
            return data
        raise CutoutUnavailable("; ".join(errors) or "no SODA host answered")


def _plain_path(path: Path) -> Path:
    """Where the uncompressed copy of a .gz product lives (the product itself otherwise)."""
    return path.with_name(path.name[:-3]) if path.name.endswith(".gz") else path


class CubeStore:
    """Download cube products once, keep them on disk, evict oldest beyond the cache limit."""

    def __init__(
        self,
        cache_dir: Path = CACHE_DIR,
        max_mb: float = DEFAULT_MAX_MB,
        timeout: float = 600.0,
        limit_bytes: int = CACHE_LIMIT_BYTES,
    ):
        self.cache_dir = cache_dir
        self.max_mb = max_mb
        self.timeout = timeout
        self.limit_bytes = limit_bytes
        self._lock = threading.Lock()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        name = os.path.basename(urlparse(url).path) or "cube.fits"
        return self.cache_dir / f"{digest}-{name}"

    def cached(self, url: str) -> Path | None:
        """The product on disk, compressed or already unpacked, or None."""
        path = self.path_for(url)
        for candidate in (_plain_path(path), path):
            if candidate.exists() and candidate.stat().st_size > 0:
                os.utime(candidate, None)
                return candidate
        return None

    def usage(self) -> dict:
        files = [p for p in self.cache_dir.glob("*") if p.is_file()]
        return {
            "dir": str(self.cache_dir),
            "files": len(files),
            "bytes": sum(p.stat().st_size for p in files),
            "limit_bytes": self.limit_bytes,
        }

    def clear(self) -> dict:
        with self._lock:
            for p in self.cache_dir.glob("*"):
                if p.is_file():
                    p.unlink(missing_ok=True)
        return self.usage()

    def fetch(
        self, url: str, max_mb: float | None = None, progress: Progress | None = None
    ) -> Path:
        if not _allowed(url):
            raise ValueError("Product URL must point at the ALMA or ESO archive")
        limit = (max_mb or self.max_mb) * 1e6
        hit = self.cached(url)
        if hit is not None:
            if progress:
                progress(hit.stat().st_size, hit.stat().st_size)
            return hit
        path = self.path_for(url)
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
                total = int(length) if length else None
                written = 0
                if progress:
                    progress(0, total)
                with open(tmp, "wb") as fh:
                    for chunk in res.iter_bytes(chunk_size=1 << 20):
                        written += len(chunk)
                        if progress:
                            progress(written, total)
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
            while files and total > self.limit_bytes:
                victim = files.pop(0)
                total -= victim.stat().st_size
                victim.unlink(missing_ok=True)


def frequency_axis_ghz(wavelength_A: list[float]) -> list[float | None]:
    return [C_KM_S * 1e3 / (w * 1e-10) / 1e9 if w and w > 0 else None for w in wavelength_A]


def _plain_fits(path: Path) -> Path:
    """Path of an uncompressed copy of a product (gunzipped once, next to it in the cache)."""
    with open(path, "rb") as fh:
        magic = fh.read(2)
    if magic != b"\x1f\x8b":
        return path
    plain = _plain_path(path) if path.name.endswith(".gz") else path.with_name(path.name + ".fits")
    if plain.exists() and plain.stat().st_size > 0:
        return plain
    tmp = plain.with_suffix(plain.suffix + ".part")
    with gzip.open(path, "rb") as src, open(tmp, "wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    tmp.replace(plain)
    path.unlink(missing_ok=True)  # keep only the unpacked copy; the cache tracks either
    return plain


def extract_from_file(
    path: Path, ra: float, dec: float, radius_arcsec: float, method: str = "sum"
) -> Spectrum1D:
    """Aperture spectrum from a product on disk: memory-mapped, only the aperture box is read."""
    from astropy.io import fits

    plain = _plain_fits(path)
    with open(plain, "rb") as fh:
        if fh.read(6) != b"SIMPLE":
            raise ValueError(f"{path.name} is not a FITS file")
    with fits.open(plain, memmap=True) as hdul:
        spec = extract_from_hdul(hdul, ra, dec, radius_arcsec, method=method)
    spec.frequency_ghz = frequency_axis_ghz(spec.wavelength_A)
    spec.source["product"] = path.name.split("-", 1)[-1]
    return spec


@dataclass
class CubeJob:
    """A download-and-extract run in a background thread, polled by the client."""

    id: str
    state: str = "queued"  # queued | cutout | downloading | extracting | done | error
    downloaded: int = 0
    total: int | None = None
    message: str | None = None
    result: Spectrum1D | None = None
    created: float = field(default_factory=time.monotonic)
    finished: float | None = None


JOB_TTL_S = 900.0


class CubeSpectrumService:
    """Aperture spectra from ALMA cube products: a SODA cutout when the archive serves one
    (seconds, nothing on disk), else a full download kept in the cube store."""

    def __init__(
        self,
        store: CubeStore | None = None,
        cache_ttl: int = 3600,
        cutouts: AlmaCutoutClient | None = None,
    ):
        self.store = store or CubeStore()
        self.cutouts = cutouts
        self.cache_ttl = cache_ttl
        self._cache: dict[tuple, tuple[float, Spectrum1D]] = {}
        self._lock = threading.Lock()
        self._jobs: dict[str, CubeJob] = {}

    def spectrum(
        self,
        product_url: str,
        ra: float,
        dec: float,
        radius_arcsec: float,
        method: str = "sum",
        max_mb: float | None = None,
        progress: Progress | None = None,
        on_state: Callable[[str], None] | None = None,
        band_ghz: tuple[float, float] | None = None,
        mode: str = "auto",  # auto | cutout | download
    ) -> Spectrum1D:
        key = (
            product_url,
            round(ra, 6),
            round(dec, 6),
            round(radius_arcsec, 2),
            method,
            tuple(round(b, 4) for b in band_ghz) if band_ghz else None,
            mode,
        )
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return hit[1].model_copy(update={"cached": True})
        state = on_state or (lambda _s: None)
        name = os.path.basename(urlparse(product_url).path)
        fallback_note: str | None = None
        spec: Spectrum1D | None = None
        if mode in ("auto", "cutout") and self.cutouts is not None:
            state("cutout")
            try:
                data = self.cutouts.fetch(product_url, ra, dec, radius_arcsec, band_ghz)
            except CutoutUnavailable as exc:
                if mode == "cutout":
                    raise ValueError(f"ALMA SODA cutout failed: {exc}") from exc
                fallback_note = str(exc)
                state(f"downloading:{fallback_note}")
                log.warning("SODA cutout unavailable for %s (%s); downloading", name, exc)
            else:
                state("extracting")
                spec = extract_spectrum(data, ra, dec, radius_arcsec, method=method)
                spec.frequency_ghz = frequency_axis_ghz(spec.wavelength_A)
                spec.source["product"] = name
                spec.source["access"] = "SODA cutout"
                if band_ghz:
                    lo, hi = sorted(band_ghz)
                    spec.source["band_ghz"] = f"{lo:.4f}-{hi:.4f}"
        if spec is None:
            path = self.store.fetch(product_url, max_mb=max_mb, progress=progress)
            state("extracting")
            spec = extract_from_file(path, ra, dec, radius_arcsec, method=method)
            spec.source["access"] = "full download"
            if fallback_note:
                spec.warnings.append(
                    f"SODA cutout unavailable ({fallback_note}); the full product was downloaded"
                )
        spec.source["product_url"] = product_url
        with self._lock:
            self._cache[key] = (now + self.cache_ttl, spec)
        return spec

    # -- background jobs with progress -------------------------------------------------
    def start_job(
        self,
        product_url: str,
        ra: float,
        dec: float,
        radius_arcsec: float,
        method: str = "sum",
        max_mb: float | None = None,
        band_ghz: tuple[float, float] | None = None,
        mode: str = "auto",
    ) -> CubeJob:
        self._reap_jobs()
        job = CubeJob(id=uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.id] = job

        def progress(done: int, total: int | None) -> None:
            job.state = "downloading"
            job.downloaded, job.total = done, total

        def on_state(state: str) -> None:
            if state.startswith("downloading:"):
                job.state, job.message = "downloading", f"SODA cutout unavailable: {state[12:]}"
            else:
                job.state = state

        def run() -> None:
            try:
                job.result = self.spectrum(
                    product_url,
                    ra,
                    dec,
                    radius_arcsec,
                    method,
                    max_mb,
                    progress,
                    on_state,
                    band_ghz,
                    mode,
                )
                job.state = "done"
            except Exception as exc:  # noqa: BLE001
                job.state = "error"
                job.message = f"{type(exc).__name__}: {exc}"[:500]
                log.warning("Cube job %s failed: %s", job.id, job.message)
            finally:
                job.finished = time.monotonic()

        threading.Thread(target=run, name=f"cube-job-{job.id}", daemon=True).start()
        return job

    def job(self, job_id: str) -> CubeJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _reap_jobs(self) -> None:
        now = time.monotonic()
        with self._lock:
            for jid, j in list(self._jobs.items()):
                if j.finished is not None and now - j.finished > JOB_TTL_S:
                    del self._jobs[jid]


_service: CubeSpectrumService | None = None
_service_lock = threading.Lock()


def get_cube_service() -> CubeSpectrumService:
    global _service
    with _service_lock:
        if _service is None:
            from app.config import get_settings

            s = get_settings()
            _service = CubeSpectrumService(
                store=CubeStore(
                    cache_dir=Path(s.cube_cache_dir) if s.cube_cache_dir else CACHE_DIR,
                    max_mb=s.cube_download_max_mb,
                    timeout=s.remote_timeout * 30,
                    limit_bytes=int(s.cube_cache_gb * 1024**3),
                ),
                cutouts=AlmaCutoutClient(timeout=s.remote_timeout * 6),
            )
        return _service
