"""HI 21 cm cross-match against VizieR catalogues (ALFALFA, HIPASS, Springob+ 2005 archive)."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from app.schemas.archives import HIDetection, HISearch, HISurveyResult
from app.services.obscore import _float, _str, angular_sep_arcmin
from app.services.tableutils import first, rows_to_dicts

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HISurvey:
    key: str
    label: str
    catalog: str  # VizieR catalogue (prefix); all its tables are searched
    beam_arcmin: float


HI_SURVEYS: tuple[HISurvey, ...] = (
    HISurvey("alfalfa", "ALFALFA α.100 (Haynes+ 2018)", "J/ApJ/861/49", 3.5),
    HISurvey("hipass", "HIPASS HICAT (Meyer+ 2004)", "VIII/73", 15.5),
    HISurvey("nhicat", "Northern HIPASS NHICAT (Wong+ 2006)", "VIII/89", 15.5),
    HISurvey("springob", "HI archive (Springob+ 2005)", "J/ApJS/160/149", 3.5),
)

# Column aliases across catalogues (matched case-insensitively).
_ID = ("AGC", "HIPASS", "Name", "ID", "UGC", "NGC", "Seq", "recno")
_NAME = ("Name", "OName", "OtherName", "SimbadName", "HIPASS", "Nname")
_RA = ("RAJ2000", "_RAJ2000", "RAdeg", "RA_ICRS", "RA")
_DEC = ("DEJ2000", "_DEJ2000", "DEdeg", "DE_ICRS", "DEC", "DE")
# HIPASS HICAT (VIII/73) reports the systemic velocity as RV50max (midpoint of the W50max
# measurement) and the widths as W50max / W20max; RVsp is the velocity of the spectral peak.
_VEL = ("Vhel", "Vhelio", "V21", "RV", "RV50max", "RVmom", "cz", "Vh", "HV", "Vopt", "Vsys", "V")
_VEL_ERR = ("e_Vhel", "e_Vhelio", "e_V21", "e_RV", "e_cz", "e_V")
_W50 = ("W50", "W50max", "W50c", "Wc50", "W50h", "W50m")
_W50_ERR = ("e_W50", "e_W50c")
_W20 = ("W20", "W20max", "W20c", "Wc20", "W20h", "W20m")
_FLUX = ("FHI", "HIflux", "Sint", "S21", "SHI", "Fc", "Stot", "F21", "FI", "Sdv", "Flux")
_FLUX_ERR = ("e_FHI", "e_HIflux", "e_Sint", "e_S21", "e_SHI", "e_Fc", "e_Flux")
_SNR = ("SNR", "S/N", "snr")
_RMS = ("rms", "RMS", "Srms")
_DIST = ("Dist", "D", "Dmpc")
_LOGMHI = ("logMHI", "logM(HI)", "logMH", "logM")


def _position(ra_val: Any, dec_val: Any) -> tuple[float | None, float | None]:
    """Coordinates in degrees from decimal values or sexagesimal strings ('03 33 33.4')."""
    ra, dec = _float(ra_val), _float(dec_val)
    if ra is not None and dec is not None:
        return ra, dec
    if isinstance(ra_val, str) and isinstance(dec_val, str):
        try:
            import astropy.units as u
            from astropy.coordinates import SkyCoord

            c = SkyCoord(ra_val, dec_val, unit=(u.hourangle, u.deg), frame="icrs")
            return float(c.ra.deg), float(c.dec.deg)
        except Exception:  # noqa: BLE001
            return None, None
    return ra, dec


class HICrossMatcher:
    def __init__(self, timeout: float = 60.0, row_limit: int = 50):
        self.timeout = timeout
        self.row_limit = row_limit

    def query_catalog(self, catalog: str, ra: float, dec: float, radius_arcmin: float):
        """Return {table_name: rows} for every table of a VizieR catalogue within the radius."""
        import astropy.units as u
        from astropy.coordinates import SkyCoord
        from astroquery.vizier import Vizier

        v = Vizier(columns=["**", "+_r"], row_limit=self.row_limit, timeout=self.timeout)
        coord = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, frame="icrs")
        tables = v.query_region(coord, radius=radius_arcmin * u.arcmin, catalog=catalog)
        out: dict[str, list[dict[str, Any]]] = {}
        for name, table in zip(tables.keys(), tables.values(), strict=True):
            out[name] = rows_to_dicts(table)
        return out

    def search_survey(
        self, survey: HISurvey, ra: float, dec: float, radius_arcmin: float
    ) -> HISurveyResult:
        try:
            tables = self.query_catalog(survey.catalog, ra, dec, radius_arcmin)
        except Exception as exc:  # noqa: BLE001 - remote service
            log.warning("HI survey %s failed: %s", survey.key, exc)
            return HISurveyResult(
                survey=survey.label,
                catalog=survey.catalog,
                status="error",
                error=f"{type(exc).__name__}: {exc}"[:300],
            )
        detections: list[HIDetection] = []
        for table_name, rows in tables.items():
            for r in rows:
                d = self.map_row(survey, table_name, r, ra, dec)
                if d is not None:
                    detections.append(d)
        detections.sort(key=lambda d: (d.separation_arcmin is None, d.separation_arcmin or 0))
        return HISurveyResult(
            survey=survey.label, catalog=survey.catalog, status="ok", detections=detections
        )

    @staticmethod
    def map_row(
        survey: HISurvey, table: str, r: dict[str, Any], ra: float, dec: float
    ) -> HIDetection | None:
        vel = _float(first(r, *_VEL))
        w50 = _float(first(r, *_W50))
        flux = _float(first(r, *_FLUX))
        if vel is None and w50 is None and flux is None:
            return None  # not a spectral-line row (e.g. a notes table)
        s_ra, s_dec = _position(first(r, *_RA), first(r, *_DEC))
        sep = _float(first(r, "_r"))
        if sep is None:
            sep = angular_sep_arcmin(ra, dec, s_ra, s_dec)
        source_id = first(r, *_ID)
        return HIDetection(
            survey=survey.label,
            catalog=table,
            source_id=_str(source_id),
            name=_str(first(r, *_NAME)),
            ra_deg=s_ra,
            dec_deg=s_dec,
            separation_arcmin=sep,
            velocity_kms=vel,
            velocity_err_kms=_float(first(r, *_VEL_ERR)),
            w50_kms=w50,
            w50_err_kms=_float(first(r, *_W50_ERR)),
            w20_kms=_float(first(r, *_W20)),
            flux_jy_kms=flux,
            flux_err_jy_kms=_float(first(r, *_FLUX_ERR)),
            snr=_float(first(r, *_SNR)),
            rms_mjy=_float(first(r, *_RMS)),
            distance_mpc=_float(first(r, *_DIST)),
            log_mhi=_float(first(r, *_LOGMHI)),
            vizier_url=f"https://vizier.cds.unistra.fr/viz-bin/VizieR-3?-source={table}",
        )

    def search(
        self,
        ra: float,
        dec: float,
        radius_arcmin: float | None = None,
        surveys: tuple[HISurvey, ...] = HI_SURVEYS,
    ) -> HISearch:
        results: list[HISurveyResult] = []
        with ThreadPoolExecutor(max_workers=len(surveys)) as pool:
            futures = [
                pool.submit(
                    self.search_survey,
                    s,
                    ra,
                    dec,
                    radius_arcmin if radius_arcmin else max(3.0, s.beam_arcmin / 2),
                )
                for s in surveys
            ]
            results = [f.result() for f in futures]
        return HISearch(
            ra_deg=ra,
            dec_deg=dec,
            radius_arcmin=radius_arcmin or max(max(3.0, s.beam_arcmin / 2) for s in surveys),
            results=results,
        )


_matcher: HICrossMatcher | None = None
_matcher_lock = threading.Lock()


def get_hi_matcher() -> HICrossMatcher:
    global _matcher
    with _matcher_lock:
        if _matcher is None:
            from app.config import get_settings

            _matcher = HICrossMatcher(timeout=get_settings().remote_timeout * 2)
        return _matcher
