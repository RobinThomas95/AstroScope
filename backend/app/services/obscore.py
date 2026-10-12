"""ObsCore TAP adapters for ESO, MAST and ALMA.

Each adapter runs one ADQL query around a position and maps the archive's columns onto
``ArchiveObservation``. Archives differ in table names, spatial predicates and units, so the
quirks live in small subclasses; everything else is shared.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC
from typing import Any
from urllib.parse import quote

from app.schemas.archives import ArchiveObservation, ArchiveResult, ArchiveSearch
from app.services.simbad import _TimeoutSession
from app.services.tableutils import first, rows_to_dicts

log = logging.getLogger(__name__)

C_M_S = 299_792_458.0
MAX_ROWS = 500


# -- helpers ---------------------------------------------------------------------------------------
def _float(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _int(v: Any) -> int | None:
    f = _float(v)
    return int(f) if f is not None else None


def _str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def mjd_to_iso(mjd: float | None) -> str | None:
    if mjd is None:
        return None
    from astropy.time import Time

    try:
        return Time(mjd, format="mjd", scale="utc").isot[:19] + "Z"
    except Exception:  # noqa: BLE001
        return None


def normalise_em(v: Any) -> float | None:
    """Wavelength to metres. Values > 0.01 cannot be metres for EM data; treat them as nm."""
    f = _float(v)
    if f is None or f <= 0:
        return None
    return f * 1e-9 if f > 0.01 else f


def band_label(em_min: float | None, em_max: float | None) -> str | None:
    if em_min is None and em_max is None:
        return None
    lo, hi = em_min or em_max, em_max or em_min
    if hi is not None and hi >= 1e-4:  # radio / mm: show frequency
        f_hi = C_M_S / lo / 1e9 if lo else None
        f_lo = C_M_S / hi / 1e9
        if f_hi is None or abs(f_hi - f_lo) / max(f_lo, 1e-9) < 0.01:
            return f"{f_lo:.1f} GHz"
        return f"{f_lo:.1f}–{f_hi:.1f} GHz"
    if hi is not None and hi >= 3e-6:  # mid/far infrared: µm
        return f"{lo * 1e6:.1f}–{hi * 1e6:.1f} µm"
    if lo is not None and hi is not None and abs(hi - lo) / max(hi, 1e-12) < 0.01:
        return f"{lo * 1e9:.0f} nm"
    return f"{lo * 1e9:.0f}–{hi * 1e9:.0f} nm"


def angular_sep_arcmin(ra1, dec1, ra2, dec2) -> float | None:
    if None in (ra1, dec1, ra2, dec2):
        return None
    d = math.radians
    a = (
        math.sin(d(dec2 - dec1) / 2) ** 2
        + math.cos(d(dec1)) * math.cos(d(dec2)) * math.sin(d(ra2 - ra1) / 2) ** 2
    )
    return math.degrees(2 * math.asin(min(1.0, math.sqrt(a)))) * 60.0


# -- base adapter -------------------------------------------------------------------------------
class ObsCoreArchive:
    key = "base"
    label = "Archive"
    tap_url = ""
    table = "ivoa.obscore"
    spatial = "intersects"  # or "point"
    instruments: tuple[str, ...] = ()  # default instrument filter (empty = all)
    instrument_column = "instrument_name"

    def __init__(self, timeout: float = 60.0):
        self.timeout = timeout
        self._service = None
        self._lock = threading.Lock()

    # -- TAP ------------------------------------------------------------------------------
    @property
    def service(self):
        from pyvo.dal import TAPService

        with self._lock:
            if self._service is None:
                self._service = TAPService(self.tap_url, session=_TimeoutSession(self.timeout))
            return self._service

    def run(self, adql: str, maxrec: int = MAX_ROWS) -> list[dict[str, Any]]:
        log.debug("%s ADQL: %s", self.key, adql)
        return rows_to_dicts(self.service.run_sync(adql, maxrec=maxrec).to_table())

    # -- query building --------------------------------------------------------------------
    def spatial_predicate(self, ra: float, dec: float, radius_deg: float) -> str:
        circle = f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.6f})"
        if self.spatial == "intersects":
            return f"INTERSECTS({circle}, s_region) = 1"
        return f"CONTAINS(POINT('ICRS', s_ra, s_dec), {circle}) = 1"

    def instrument_predicate(self, instruments: list[str]) -> str:
        wanted = [i for i in (instruments or self.instruments) if i]
        if not wanted:
            return ""
        values = ", ".join(f"'{w.upper().replace(chr(39), chr(39) * 2)}'" for w in wanted)
        return f" AND {self.instrument_column} IN ({values})"

    def build_adql(self, ra, dec, radius_deg, instruments, maxrec) -> str:
        return (
            f"SELECT TOP {int(maxrec)} * FROM {self.table} WHERE "
            f"{self.spatial_predicate(ra, dec, radius_deg)}"
            f"{self.instrument_predicate(instruments)}"
        )

    # -- public ---------------------------------------------------------------------------
    def search(
        self,
        ra: float,
        dec: float,
        radius_deg: float,
        instruments: list[str] | None = None,
        maxrec: int = MAX_ROWS,
    ) -> ArchiveResult:
        t0 = time.monotonic()
        try:
            rows = self.run(self.build_adql(ra, dec, radius_deg, instruments or [], maxrec), maxrec)
        except Exception as exc:  # noqa: BLE001 - remote service
            if self.spatial == "intersects":
                # Some services reject INTERSECTS on s_region; retry with a point search.
                log.warning(
                    "%s INTERSECTS query failed (%s); retrying with point search", self.key, exc
                )
                try:
                    self.spatial = "point"
                    rows = self.run(
                        self.build_adql(ra, dec, radius_deg, instruments or [], maxrec), maxrec
                    )
                except Exception as exc2:  # noqa: BLE001
                    self.spatial = "intersects"
                    return self._error(exc2, t0)
                finally:
                    pass
            else:
                return self._error(exc, t0)
        obs = [self.map_row(r, ra, dec) for r in rows]
        obs = [o for o in obs if o is not None]
        obs.sort(key=lambda o: (o.mjd_obs is None, -(o.mjd_obs or 0)))
        return ArchiveResult(
            archive=self.key,
            label=self.label,
            status="ok",
            table=self.table,
            count=len(obs),
            truncated=len(rows) >= maxrec,
            elapsed_s=round(time.monotonic() - t0, 2),
            observations=obs,
        )

    def _error(self, exc: Exception, t0: float) -> ArchiveResult:
        log.warning("%s query failed: %s", self.key, exc)
        return ArchiveResult(
            archive=self.key,
            label=self.label,
            status="error",
            table=self.table,
            error=f"{type(exc).__name__}: {exc}"[:500],
            elapsed_s=round(time.monotonic() - t0, 2),
        )

    # -- row mapping -----------------------------------------------------------------------
    def map_row(self, r: dict[str, Any], ra: float, dec: float) -> ArchiveObservation | None:
        em_min = normalise_em(first(r, "em_min"))
        em_max = normalise_em(first(r, "em_max"))
        s_ra = _float(first(r, "s_ra", "ra", "targetRA"))
        s_dec = _float(first(r, "s_dec", "dec", "targetDec"))
        mjd = _float(first(r, "t_min"))
        release = _str(first(r, "obs_release_date", "t_obs_release"))
        obs = ArchiveObservation(
            archive=self.key,
            collection=_str(first(r, "obs_collection", "collection")),
            instrument=_str(first(r, "instrument_name", "instrument")),
            obs_id=_str(first(r, "obs_id", "obsid", "obs_publisher_did")),
            dataproduct_type=_str(first(r, "dataproduct_type")),
            calib_level=_int(first(r, "calib_level")),
            target_name=_str(first(r, "target_name")),
            date_obs=mjd_to_iso(mjd),
            mjd_obs=mjd,
            exptime_s=_float(first(r, "t_exptime")),
            band=band_label(em_min, em_max),
            em_min_m=em_min,
            em_max_m=em_max,
            resolution_arcsec=_float(first(r, "s_resolution", "spatial_resolution")),
            fov_deg=_float(first(r, "s_fov")),
            ra_deg=s_ra,
            dec_deg=s_dec,
            separation_arcmin=angular_sep_arcmin(ra, dec, s_ra, s_dec),
            s_region=_str(first(r, "s_region")),
            access_url=_str(first(r, "access_url", "dataURL", "datalink_url")),
            preview_url=_str(first(r, "preview_url", "preview_html", "jpegURL")),
            proposal_id=_str(first(r, "proposal_id", "proposal", "prog_id")),
            release_date=release,
            public=self.is_public(r, release),
        )
        self.decorate(obs, r)
        return obs

    @staticmethod
    def is_public(r: dict[str, Any], release: str | None) -> bool | None:
        rights = _str(first(r, "data_rights", "dataRights"))
        if rights:
            return rights.lower().startswith("pub")
        if release:
            from datetime import datetime

            try:
                dt = datetime.fromisoformat(release.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt <= datetime.now(UTC)
            except ValueError:
                return None
        return None

    def decorate(self, obs: ArchiveObservation, r: dict[str, Any]) -> None:
        """Archive-specific extras and links."""


# -- concrete adapters ---------------------------------------------------------------------------
class EsoArchive(ObsCoreArchive):
    key = "eso"
    label = "ESO Science Archive"
    tap_url = "https://archive.eso.org/tap_obs"
    table = "ivoa.ObsCore"
    spatial = "intersects"
    instruments = ("MUSE", "UVES", "XSHOOTER")

    def decorate(self, obs, r):
        if obs.instrument and obs.instrument.upper() == "XSHOOTER":
            obs.instrument = "X-Shooter"
        obs.extra["em_res_power"] = _float(first(r, "em_res_power"))
        obs.extra["pixel_scale_arcsec"] = _float(first(r, "s_pixel_scale"))
        if obs.obs_id:
            obs.archive_url = (
                "https://archive.eso.org/scienceportal/home?data_release_date=*&"
                f"dp_id={quote(obs.obs_id)}"
            )


class MastArchive(ObsCoreArchive):
    key = "mast"
    label = "MAST (HST / JWST)"
    # MAST has moved its TAP service over time; the first endpoint that answers a TAP_SCHEMA
    # query wins, and the observation table is discovered from that schema.
    tap_candidates = (
        "https://mast.stsci.edu/vo-tap/api/v0.1/caom/",
        "https://vao.stsci.edu/caomtap/CaomTapService.svc",
    )
    tap_url = tap_candidates[0]
    # dbo.ObsPointing is one row per observation with preview/data links, filters and release
    # dates; ivoa.obscore is one row per data product (duplicates, and much slower at MAST).
    table_candidates = ("dbo.ObsPointing", "ivoa.obscore", "dbo.CaomObservation")
    table = table_candidates[0]
    spatial = "point"
    instruments = ()
    collections = ("HST", "JWST")

    def __init__(self, timeout: float = 60.0, tap_url: str | None = None):
        super().__init__(timeout=timeout)
        if tap_url:
            self.tap_candidates = (tap_url,)
            self.tap_url = tap_url
        self._discovered = False

    # Column names differ between MAST tables; discovered from TAP_SCHEMA.columns.
    ra_column = "s_ra"
    dec_column = "s_dec"
    collection_column = "obs_collection"
    columns: list[str] = []

    def list_tables(self) -> list[str]:
        rows = self.run("SELECT table_name FROM TAP_SCHEMA.tables", maxrec=2000)
        return [str(r.get("table_name")) for r in rows if r.get("table_name")]

    def list_columns(self, table: str) -> list[str]:
        rows = self.run(
            f"SELECT column_name FROM TAP_SCHEMA.columns WHERE table_name = '{table}'",
            maxrec=2000,
        )
        return [str(r.get("column_name")) for r in rows if r.get("column_name")]

    @staticmethod
    def _pick(columns: list[str], *candidates: str) -> str | None:
        lower = {c.lower(): c for c in columns}
        for cand in candidates:
            if cand.lower() in lower:
                return lower[cand.lower()]
        return None

    def discover(self) -> None:
        """Pick the first working endpoint, the observation table it exposes and the names of
        the position / collection columns in that table."""
        if self._discovered:
            return
        errors = []
        for url in self.tap_candidates:
            self.tap_url = url
            self._service = None
            try:
                tables = self.list_tables()
            except Exception as exc:  # noqa: BLE001 - try the next endpoint
                errors.append(f"{url}: {exc}")
                continue
            lower = {t.lower(): t for t in tables}
            chosen = None
            for cand in self.table_candidates:
                if cand.lower() in lower:
                    table = lower[cand.lower()]
                    try:
                        cols = self.list_columns(table)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{url} {table}: {exc}")
                        continue
                    ra = self._pick(cols, "s_ra", "ra", "targetRA", "ra_targ")
                    dec = self._pick(cols, "s_dec", "dec", "targetDec", "dec_targ")
                    if ra and dec:
                        chosen = (table, cols, ra, dec)
                        break
                    errors.append(f"{url} {table}: no position columns in {cols[:20]}")
            if chosen is None:
                errors.append(f"{url}: none of {self.table_candidates} usable in {tables[:30]}")
                continue
            self.table, self.columns, self.ra_column, self.dec_column = chosen
            self.collection_column = (
                self._pick(self.columns, "obs_collection", "collection", "obsCollection")
                or "obs_collection"
            )
            log.info(
                "MAST TAP: using %s table %s (%s, %s, %s)",
                url,
                self.table,
                self.ra_column,
                self.dec_column,
                self.collection_column,
            )
            self._discovered = True
            return
        raise ConnectionError("No usable MAST TAP endpoint: " + "; ".join(errors))

    def spatial_predicate(self, ra, dec, radius_deg):
        circle = f"CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius_deg:.6f})"
        return f"CONTAINS(POINT('ICRS', {self.ra_column}, {self.dec_column}), {circle}) = 1"

    def search(self, ra, dec, radius_deg, instruments=None, maxrec=MAX_ROWS):
        t0 = time.monotonic()
        try:
            self.discover()
        except Exception as exc:  # noqa: BLE001
            return self._error(exc, t0)
        return super().search(ra, dec, radius_deg, instruments, maxrec)

    def build_adql(self, ra, dec, radius_deg, instruments, maxrec):
        coll = ", ".join(f"'{c}'" for c in self.collections)
        intent = self._pick(self.columns, "intentType", "intent")
        science = f" AND {intent} = 'science'" if intent else ""
        return (
            f"SELECT TOP {int(maxrec)} * FROM {self.table} WHERE "
            f"{self.spatial_predicate(ra, dec, radius_deg)} "
            f"AND {self.collection_column} IN ({coll}){science}"
            f"{self.instrument_predicate(instruments)}"
        )

    MAST_DOWNLOAD = "https://mast.stsci.edu/api/v0.1/Download/file?uri="

    @classmethod
    def mast_url(cls, value: str | None) -> str | None:
        """MAST lists products as 'mast:JWST/product/...' URIs; the download API serves them."""
        if value and value.startswith("mast:"):
            return cls.MAST_DOWNLOAD + quote(value, safe=":/")
        return value

    def decorate(self, obs, r):
        obs.access_url = self.mast_url(obs.access_url)
        obs.preview_url = self.mast_url(obs.preview_url)
        obs.extra["filters"] = _str(first(r, "filters"))
        obs.extra["obs_title"] = _str(first(r, "obs_title"))
        obs.extra["proposal_pi"] = _str(first(r, "proposal_pi"))
        if obs.extra["filters"] and obs.band:
            obs.band = f"{obs.extra['filters']} ({obs.band})"
        elif obs.extra["filters"]:
            obs.band = obs.extra["filters"]
        if obs.obs_id:
            obs.archive_url = (
                "https://mast.stsci.edu/portal/Mashup/Clients/Mast/Portal.html?"
                f"searchQuery={quote(obs.obs_id)}"
            )


_SPW_SUFFIX = re.compile(r"\.spw\.\d+$", re.IGNORECASE)


class AlmaArchive(ObsCoreArchive):
    key = "alma"
    label = "ALMA Science Archive"
    tap_url = "https://almascience.eso.org/tap"
    table = "ivoa.obscore"
    spatial = "intersects"
    instruments = ()

    def search(self, ra, dec, radius_deg, instruments=None, maxrec=MAX_ROWS):
        res = super().search(ra, dec, radius_deg, instruments, maxrec)
        if res.status == "ok":
            res.observations = self.merge_spectral_windows(res.observations)
            res.count = len(res.observations)
        return res

    @staticmethod
    def merge_spectral_windows(obs: list[ArchiveObservation]) -> list[ArchiveObservation]:
        """ALMA's ObsCore lists one row per spectral window; collapse them per observation."""
        merged: dict[str, ArchiveObservation] = {}
        order: list[str] = []
        for o in obs:
            base = _SPW_SUFFIX.sub("", o.obs_id or "")
            key = base or f"{o.extra.get('member_ous_uid')}|{o.target_name}|{o.mjd_obs}"
            if key not in merged:
                copy = o.model_copy(deep=True)
                copy.obs_id = base or o.obs_id
                copy.extra["n_spw"] = 1
                merged[key] = copy
                order.append(key)
                continue
            m = merged[key]
            m.extra["n_spw"] = int(m.extra.get("n_spw") or 1) + 1
            if o.em_min_m is not None:
                m.em_min_m = min(m.em_min_m or o.em_min_m, o.em_min_m)
            if o.em_max_m is not None:
                m.em_max_m = max(m.em_max_m or o.em_max_m, o.em_max_m)
            if o.resolution_arcsec is not None and (
                m.resolution_arcsec is None or o.resolution_arcsec < m.resolution_arcsec
            ):
                m.resolution_arcsec = o.resolution_arcsec
        out = []
        for key in order:
            m = merged[key]
            band = band_label(m.em_min_m, m.em_max_m)
            bands = m.extra.get("band_list")
            m.band = f"Band {bands} ({band})" if bands and band else band or m.band
            if int(m.extra.get("n_spw") or 1) > 1:
                m.band = (
                    f"{m.band}, {m.extra['n_spw']} spw" if m.band else f"{m.extra['n_spw']} spw"
                )
            out.append(m)
        return out

    def build_adql(self, ra, dec, radius_deg, instruments, maxrec):
        return (
            f"SELECT TOP {int(maxrec)} * FROM {self.table} WHERE "
            f"{self.spatial_predicate(ra, dec, radius_deg)} AND science_observation = 'T'"
        )

    def decorate(self, obs, r):
        obs.instrument = "ALMA"
        obs.collection = "ALMA"
        obs.extra["band_list"] = _str(first(r, "band_list"))
        obs.extra["frequency_ghz"] = _float(first(r, "frequency"))
        obs.extra["bandwidth_ghz"] = _float(first(r, "bandwidth"))
        obs.extra["velocity_resolution_kms"] = _float(first(r, "velocity_resolution"))
        obs.extra["member_ous_uid"] = _str(first(r, "member_ous_uid"))
        obs.extra["pub_title"] = _str(first(r, "pub_title"))
        if obs.extra.get("band_list") and obs.band:
            obs.band = f"Band {obs.extra['band_list']} ({obs.band})"
        mous = obs.extra.get("member_ous_uid")
        if mous:
            obs.archive_url = f"https://almascience.org/aq/?mous={quote(str(mous), safe='')}"


ARCHIVES: dict[str, type[ObsCoreArchive]] = {
    EsoArchive.key: EsoArchive,
    MastArchive.key: MastArchive,
    AlmaArchive.key: AlmaArchive,
}


class ArchiveSearcher:
    def __init__(self, adapters: dict[str, ObsCoreArchive] | None = None, timeout: float = 60.0):
        self.adapters = adapters or {k: cls(timeout=timeout) for k, cls in ARCHIVES.items()}

    def search(
        self,
        ra: float,
        dec: float,
        radius_arcmin: float,
        archives: list[str] | None = None,
        maxrec: int = MAX_ROWS,
    ) -> ArchiveSearch:
        radius_deg = radius_arcmin / 60.0
        keys = [k for k in (archives or list(self.adapters)) if k in self.adapters]
        with ThreadPoolExecutor(max_workers=max(1, len(keys))) as pool:
            futures = {
                k: pool.submit(self.adapters[k].search, ra, dec, radius_deg, None, maxrec)
                for k in keys
            }
            results = [futures[k].result() for k in keys]
        counts: dict[str, int] = {}
        for res in results:
            for o in res.observations:
                name = o.instrument or o.collection or res.label
                counts[name] = counts.get(name, 0) + 1
        return ArchiveSearch(
            ra_deg=ra,
            dec_deg=dec,
            radius_arcmin=radius_arcmin,
            results=results,
            instrument_counts=dict(sorted(counts.items())),
        )


_searcher: ArchiveSearcher | None = None
_searcher_lock = threading.Lock()


def get_searcher() -> ArchiveSearcher:
    global _searcher
    with _searcher_lock:
        if _searcher is None:
            from app.config import get_settings

            settings = get_settings()
            timeout = settings.remote_timeout * 3
            adapters: dict[str, ObsCoreArchive] = {
                EsoArchive.key: EsoArchive(timeout=timeout),
                MastArchive.key: MastArchive(timeout=timeout, tap_url=settings.mast_tap_url),
                AlmaArchive.key: AlmaArchive(timeout=timeout),
            }
            _searcher = ArchiveSearcher(adapters=adapters)
        return _searcher
