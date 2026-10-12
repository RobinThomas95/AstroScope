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
                "fields": "ID,obs_title,obs_collection,moc_sky_fraction,dataproduct_type",
            }
        )
        data = res.json()
        if not isinstance(data, list):
            raise ValueError(f"Unexpected MOCServer response for {expression!r}")
        return [MocRecord.from_json(r, expression) for r in data if isinstance(r, dict)]

    def fetch_moc_fits(self, record_id: str) -> bytes:
        """The coverage of one record as a FITS MOC (bytes)."""
        now = time.monotonic()
        with self._lock:
            hit = self._moc_cache.get(record_id)
            if hit and hit[0] > now:
                return hit[1]
        data: bytes | None = None
        # The server answers 413 when a MOC is too large to deliver at full resolution; the
        # ``order`` parameter asks for a degraded (coarser) version, so step down until it fits.
        attempts: list[dict[str, str | int]] = [{"ID": record_id, "get": "moc"}]
        attempts += [{"ID": record_id, "get": "moc", "order": o} for o in DEGRADE_ORDERS]
        for params in attempts:
            res = self._get(params, ok_statuses=(413,))
            if res.status_code == 413:
                log.info("MOCServer: %s too large at %s", record_id, params.get("order", "full"))
                continue
            if res.content.startswith(FITS_MAGIC):
                data = res.content
                if "order" in params:
                    log.info(
                        "MOCServer: %s delivered degraded to order %s", record_id, params["order"]
                    )
                break
            raise ValueError(
                f"MOCServer returned {res.headers.get('content-type')} instead of a FITS MOC "
                f"for {record_id!r}"
            )
        if data is None:
            raise ValueError(
                f"MOCServer refused to deliver {record_id!r} even at order {DEGRADE_ORDERS[-1]}"
            )
        with self._lock:
            self._moc_cache[record_id] = (now + self.cache_ttl, data)
        return data

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

    def fetch_union_fits(self, record_ids: list[str]) -> bytes:
        """The union of several records' coverages as one FITS MOC."""
        if not record_ids:
            raise ValueError("No records to fetch")
        key = "union:" + "|".join(sorted(record_ids))
        now = time.monotonic()
        with self._lock:
            hit = self._moc_cache.get(key)
            if hit and hit[0] > now:
                return hit[1]
        blobs: list[bytes] = []
        for rid in record_ids:
            try:
                blobs.append(self.fetch_moc_fits(rid))
            except Exception as exc:  # noqa: BLE001 - skip a broken member, keep the rest
                log.warning("Skipping MOC %s: %s", rid, exc)
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


def union_fits_mocs(blobs: list[bytes]) -> bytes:
    """Union FITS MOCs (as bytes) into a single FITS MOC using mocpy."""
    import io

    from mocpy import MOC

    mocs = []
    tmp_paths: list[str] = []
    try:
        for blob in blobs:
            fd, path = tempfile.mkstemp(suffix=".fits")
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
            tmp_paths.append(path)
            mocs.append(MOC.load(path, format="fits"))
    finally:
        for path in tmp_paths:
            try:
                os.unlink(path)
            except OSError:
                pass
    result = mocs[0]
    for m in mocs[1:]:
        result = result.union(m)
    buf = io.BytesIO()
    result.serialize(format="fits").writeto(buf)
    return buf.getvalue()


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
