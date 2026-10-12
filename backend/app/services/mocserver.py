"""Client for the CDS MOCServer (https://alasky.cds.unistra.fr/MocServer/query).

The MOCServer knows the sky coverage (MOC) of every HiPS, VizieR table and many mission
observation logs. We use it to find the coverage record for a mission/instrument and to fetch
that coverage as a FITS MOC, which is the format Aladin Lite's ``A.MOCFromURL`` expects.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

MOCSERVER_URL = "https://alasky.cds.unistra.fr/MocServer/query"
FITS_MAGIC = b"SIMPLE  ="
# Orders to fall back to when a MOC is too large (413). Order 11 cells are ~1.7 arcmin,
# order 8 cells ~13.7 arcmin.
DEGRADE_ORDERS = (12, 11, 10, 9, 8)


@dataclass
class MocRecord:
    id: str
    title: str | None
    sky_fraction: float | None
    dataproduct_type: str | None
    expression: str
    moc_access_url: str | None = None

    @classmethod
    def from_json(cls, rec: dict[str, Any], expression: str) -> MocRecord:
        frac = rec.get("moc_sky_fraction")
        try:
            frac_f = float(frac) if frac is not None else None
        except (TypeError, ValueError):
            frac_f = None
        return cls(
            id=str(rec.get("ID", "")),
            title=rec.get("obs_title") or rec.get("obs_collection"),
            sky_fraction=frac_f,
            dataproduct_type=rec.get("dataproduct_type"),
            expression=expression,
            moc_access_url=rec.get("moc_access_url") or None,
        )


class MocServerClient:
    def __init__(
        self, base_url: str = MOCSERVER_URL, timeout: float = 30.0, cache_ttl: int = 86400
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._record_cache: dict[str, tuple[float, list[MocRecord]]] = {}
        self._moc_cache: dict[str, tuple[float, bytes]] = {}
        self._lock = threading.Lock()

    # -- HTTP ----------------------------------------------------------------------------------
    def _get(
        self, params: dict[str, str | int], ok_statuses: tuple[int, ...] = ()
    ) -> httpx.Response:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            res = client.get(self.base_url, params=params, headers={"User-Agent": "AstroScope/0.1"})
            if res.status_code not in ok_statuses:
                res.raise_for_status()
            return res

    def search(self, expression: str) -> list[MocRecord]:
        """Records matching a MOCServer expression, e.g. ``ID=CDS/B/hst/*``."""
        res = self._get(
            {
                "expr": expression,
                "get": "record",
                "fmt": "json",
                "fields": "ID,obs_title,obs_collection,moc_sky_fraction,dataproduct_type,"
                "moc_access_url",
            }
        )
        data = res.json()
        if not isinstance(data, list):
            raise ValueError(f"Unexpected MOCServer response for {expression!r}")
        return [MocRecord.from_json(r, expression) for r in data if isinstance(r, dict)]

    def fetch_moc_fits(self, record_id: str, moc_access_url: str | None = None) -> bytes:
        """The coverage of one record as a spatial FITS MOC (bytes).

        Tries the MOCServer ``get=moc`` endpoint first (stepping down the ``order`` when the
        server answers 413), then the record's own ``moc_access_url``. Space-time and
        space-frequency MOCs are projected onto the sky.
        """
        now = time.monotonic()
        with self._lock:
            hit = self._moc_cache.get(record_id)
            if hit and hit[0] > now:
                return hit[1]
        errors: list[str] = []
        data = self._fetch_from_server(record_id, errors)
        if data is None and moc_access_url:
            data = self._fetch_from_url(record_id, moc_access_url, errors)
        if data is None:
            raise ValueError(f"Could not obtain a MOC for {record_id!r}: " + "; ".join(errors))
        data = to_spatial_fits(data)
        with self._lock:
            self._moc_cache[record_id] = (now + self.cache_ttl, data)
        return data

    def _fetch_from_server(self, record_id: str, errors: list[str]) -> bytes | None:
        # The server answers 413 when it will not deliver a MOC at that resolution (or at all,
        # for some collection records); the ``order`` parameter asks for a coarser version.
        attempts: list[dict[str, str | int]] = [{"ID": record_id, "get": "moc"}]
        attempts += [{"ID": record_id, "get": "moc", "order": o} for o in DEGRADE_ORDERS]
        for params in attempts:
            try:
                res = self._get(params, ok_statuses=(413,))
            except Exception as exc:  # noqa: BLE001 - remote service
                errors.append(f"get=moc failed: {exc}")
                return None
            if res.status_code == 413:
                continue
            if res.content.startswith(FITS_MAGIC):
                if "order" in params:
                    log.info(
                        "MOCServer: %s delivered degraded to order %s", record_id, params["order"]
                    )
                return res.content
            errors.append(
                f"get=moc returned {res.headers.get('content-type')} instead of a FITS MOC"
            )
            return None
        errors.append(f"get=moc refused (413) at every order down to {DEGRADE_ORDERS[-1]}")
        return None

    def _fetch_from_url(self, record_id: str, url: str, errors: list[str]) -> bytes | None:
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                res = client.get(url, headers={"User-Agent": "AstroScope/0.1"})
                res.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - remote service
            errors.append(f"moc_access_url {url} failed: {exc}")
            return None
        if not res.content.startswith(FITS_MAGIC):
            errors.append(f"moc_access_url {url} did not return FITS")
            return None
        log.info("MOCServer: %s fetched from moc_access_url %s", record_id, url)
        return res.content

    # -- resolution ----------------------------------------------------------------------------
    def resolve(self, key: str, expressions: list[str], max_records: int = 25) -> list[MocRecord]:
        """Try each expression in turn; return every usable record matched by the first one
        that yields any, widest coverage first (capped at ``max_records``)."""
        now = time.monotonic()
        with self._lock:
            hit = self._record_cache.get(key)
            if hit and hit[0] > now and hit[1]:
                return list(hit[1])
        chosen: list[MocRecord] = []
        for expr in expressions:
            try:
                records = self.search(expr)
            except Exception as exc:  # noqa: BLE001 - remote service
                log.warning("MOCServer search failed for %r: %s", expr, exc)
                continue
            usable = [r for r in records if r.id and (r.sky_fraction or 0) > 0]
            if not usable:
                continue
            usable.sort(key=lambda r: r.sky_fraction or 0, reverse=True)
            chosen = usable[:max_records]
            log.info(
                "Overlay %s resolved via %r to %d record(s): %s",
                key,
                expr,
                len(chosen),
                ", ".join(r.id for r in chosen),
            )
            break
        with self._lock:
            self._record_cache[key] = (now + self.cache_ttl, chosen)
        return list(chosen)

    def fetch_union_fits(self, records: list[MocRecord]) -> bytes:
        """The union of several records' coverages as one FITS MOC."""
        if not records:
            raise ValueError("No records to fetch")
        record_ids = [r.id for r in records]
        key = "union:" + "|".join(sorted(record_ids))
        now = time.monotonic()
        with self._lock:
            hit = self._moc_cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        blobs: list[bytes] = []
        for rec in records:
            try:
                blobs.append(self.fetch_moc_fits(rec.id, rec.moc_access_url))
            except Exception as exc:  # noqa: BLE001 - skip a broken member, keep the rest
                log.warning("Skipping MOC %s: %s", rec.id, exc)
        if not blobs:
            raise ValueError("None of the MOCs could be fetched")
        if len(blobs) == 1:
            data = blobs[0]
        else:
            try:
                data = union_fits_mocs(blobs)
            except Exception as exc:  # noqa: BLE001 - serve the largest member rather than nothing
                log.error(
                    "Union of %s failed (%s: %s); serving the largest single MOC instead",
                    ", ".join(record_ids),
                    type(exc).__name__,
                    exc,
                )
                data = max(blobs, key=len)
        with self._lock:
            self._moc_cache[key] = (now + self.cache_ttl, data)
        return data


def _with_temp_fits(blob: bytes, fn):
    fd, path = tempfile.mkstemp(suffix=".fits")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
        return fn(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _moc_to_bytes(moc) -> bytes:
    import io

    buf = io.BytesIO()
    moc.serialize(format="fits").writeto(buf)
    return buf.getvalue()


def moc_dimension(blob: bytes) -> str:
    """The MOCDIM header value of a FITS MOC ('SPACE', 'TIME.SPACE', 'FREQUENCY.SPACE', ...)."""
    from astropy.io import fits

    def read(path):
        with fits.open(path) as hdul:
            for hdu in hdul[1:]:
                dim = hdu.header.get("MOCDIM")
                if dim:
                    return str(dim).upper()
        return "SPACE"

    return _with_temp_fits(blob, read)


def to_spatial_fits(blob: bytes) -> bytes:
    """Project a space-time or space-frequency FITS MOC onto the sky; pass spatial MOCs through."""
    dim = moc_dimension(blob)
    if dim == "SPACE":
        return blob
    if dim == "TIME.SPACE":
        from astropy.time import Time
        from mocpy import STMOC, TimeMOC

        def project(path):
            st = STMOC.load(path, format="fits")
            tmoc = TimeMOC.from_time_ranges(Time([st.min_time]), Time([st.max_time]))
            return _moc_to_bytes(st.query_by_time(tmoc))

        return _with_temp_fits(blob, project)
    if dim == "FREQUENCY.SPACE":
        import astropy.units as u
        from mocpy import SFMOC, FrequencyMOC

        def project(path):
            sf = SFMOC.load(path, format="fits")
            fmoc = FrequencyMOC.from_frequency_ranges(
                FrequencyMOC.MAX_ORDER,
                u.Quantity([sf.min_frequency]),
                u.Quantity([sf.max_frequency]),
            )
            return _moc_to_bytes(sf.query_by_frequency(fmoc))

        return _with_temp_fits(blob, project)
    raise ValueError(f"Unsupported MOC dimension {dim!r}")


def union_fits_mocs(blobs: list[bytes]) -> bytes:
    """Union FITS MOCs (as bytes) into a single FITS MOC using mocpy."""
    from mocpy import MOC

    mocs = [_with_temp_fits(blob, lambda path: MOC.load(path, format="fits")) for blob in blobs]
    result = mocs[0]
    for m in mocs[1:]:
        result = result.union(m)
    return _moc_to_bytes(result)


_client: MocServerClient | None = None
_client_lock = threading.Lock()


def get_mocserver() -> MocServerClient:
    global _client
    with _client_lock:
        if _client is None:
            from app.config import get_settings

            settings = get_settings()
            _client = MocServerClient(
                base_url=settings.mocserver_url, timeout=settings.remote_timeout * 1.5
            )
        return _client
