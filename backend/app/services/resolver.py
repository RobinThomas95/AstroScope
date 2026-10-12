"""Target resolver: name or coordinates -> position, kinematics, morphology, environment.

Strategy
--------
1. Decide whether the input is a coordinate pair or a name.
2. Names: SIMBAD (TAP) for the canonical record, NED for an independent redshift / name,
   Sesame (``SkyCoord.from_name``) as a coordinate fallback when SIMBAD has no record.
   Coordinates: SIMBAD cone search picks the nearest cataloged object, then same as above.
3. Environment: identifiers that come from group/cluster catalogs (FCC, VCC, LGG, HCG, ...)
   mark cataloged membership; a SIMBAD cone search for group/cluster objects lists neighbours.

Every remote call is isolated so a failing service degrades to a warning instead of an error.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import astropy.units as u
from astropy.coordinates import SkyCoord

from app.config import Settings, get_settings
from app.schemas.target import (
    Coordinates,
    Distance,
    Environment,
    GroupMembership,
    Kinematics,
    Morphology,
    ResolvedTarget,
)
from app.services.coords import format_coordinates, parse_coordinates
from app.services.ned import NedClient
from app.services.simbad import GROUP_OTYPES, SimbadClient

log = logging.getLogger(__name__)

C_KMS = 299792.458

# Identifier prefixes that imply membership of a cataloged group / cluster.
# (regex on the SIMBAD identifier, human-readable catalog name)
MEMBERSHIP_CATALOGS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^FCC\s+\d+"), "Fornax Cluster Catalog (Ferguson 1989)"),
    (re.compile(r"^VCC\s+\d+"), "Virgo Cluster Catalog (Binggeli+ 1985)"),
    (re.compile(r"^EVCC\s+\d+"), "Extended Virgo Cluster Catalog (Kim+ 2014)"),
    (re.compile(r"^LGG\s+\d+"), "Lyon Groups of Galaxies (Garcia 1993)"),
    (re.compile(r"^HDCE\s+\d+"), "High Density Contrast groups (Crook+ 2007)"),
    (re.compile(r"^LDCE\s+\d+"), "Low Density Contrast groups (Crook+ 2007)"),
    (re.compile(r"^USGC\s+"), "UZC-SSRS2 Group Catalog (Ramella+ 2002)"),
    (re.compile(r"^NOGG\s+"), "Nearby Optical Galaxy Groups (Giuricin+ 2000)"),
    (re.compile(r"^HCG\s+\d+"), "Hickson Compact Groups"),
    (re.compile(r"^KPG\s+\d+"), "Karachentsev Isolated Pairs of Galaxies"),
    (re.compile(r"^KTG\s+\d+"), "Karachentseva Isolated Triplets of Galaxies"),
    (re.compile(r"^SDSSCGB\s+"), "SDSS Compact Groups (McConnachie+ 2009)"),
    (re.compile(r"^\[T2015\]\s+"), "Tully 2015 group catalog"),
    (re.compile(r"^\[TSK2008\]\s+"), "Tully+ 2008 group catalog"),
    (re.compile(r"^\[KK2004\]\s+"), "Karachentsev & Karachentseva 2004"),
    (re.compile(r"^MKW\s+\d+"), "Morgan-Kayser-White poor clusters"),
    (re.compile(r"^AWM\s+\d+"), "Albert-White-Morgan poor clusters"),
    (re.compile(r"^ACO\s+\d+"), "Abell cluster"),
    (re.compile(r"^NGC\s+\d+\s+GROUP", re.I), "NGC group designation"),
]

DEFAULT_FOV_DEG = 0.25
ENVIRONMENT_RADIUS_DEG = 1.0
COORD_MATCH_RADIUS_ARCSEC = 30.0


@dataclass
class _CacheEntry:
    value: ResolvedTarget
    expires_at: float


class TargetResolver:
    def __init__(
        self,
        settings: Settings | None = None,
        simbad: SimbadClient | None = None,
        ned: NedClient | None = None,
    ):
        self.settings = settings or get_settings()
        self.simbad = simbad or SimbadClient(
            self.settings.simbad_tap_url, timeout=self.settings.remote_timeout
        )
        self.ned = ned or NedClient(timeout=self.settings.remote_timeout)
        self._cache: dict[str, _CacheEntry] = {}
        self._lock = threading.Lock()

    # -- public API --------------------------------------------------------------------------
    def resolve(self, query: str, use_cache: bool = True) -> ResolvedTarget:
        key = " ".join(query.split()).lower()
        if not key:
            raise ValueError("Empty target query")

        if use_cache:
            with self._lock:
                entry = self._cache.get(key)
                if entry and entry.expires_at > time.monotonic():
                    return entry.value.model_copy(update={"cached": True})

        result = self._resolve_uncached(query.strip())

        with self._lock:
            self._cache[key] = _CacheEntry(
                value=result, expires_at=time.monotonic() + self.settings.cache_ttl
            )
        return result

    # -- orchestration -----------------------------------------------------------------------
    def _resolve_uncached(self, query: str) -> ResolvedTarget:
        warnings: list[str] = []
        sources: list[str] = []

        coord_input = parse_coordinates(query)  # may raise ValueError on malformed coords
        input_kind = "coordinates" if coord_input is not None else "name"

        simbad_row: dict[str, Any] | None = None
        if coord_input is not None:
            simbad_row = self._simbad_nearest(coord_input, warnings)
        else:
            simbad_row = self._guard("SIMBAD", warnings, self.simbad.basic_by_name, query)

        # Name to use for NED: the SIMBAD main_id if we have it, otherwise the raw query.
        ned_name_query = (simbad_row or {}).get("main_id") or (
            query if coord_input is None else None
        )

        with ThreadPoolExecutor(max_workers=3) as pool:
            ned_future = (
                pool.submit(self._guard, "NED", warnings, self.ned.query_object, ned_name_query)
                if ned_name_query
                else None
            )
            ids_future = (
                pool.submit(
                    self._guard,
                    "SIMBAD identifiers",
                    warnings,
                    self.simbad.identifiers,
                    simbad_row["oid"],
                )
                if simbad_row and simbad_row.get("oid") is not None
                else None
            )
            dist_future = (
                pool.submit(
                    self._guard,
                    "SIMBAD distances",
                    warnings,
                    self.simbad.distances,
                    simbad_row["oid"],
                )
                if simbad_row and simbad_row.get("oid") is not None
                else None
            )
            ned_row = ned_future.result() if ned_future else None
            aliases = (ids_future.result() if ids_future else None) or []
            dist_rows = (dist_future.result() if dist_future else None) or []

        if simbad_row:
            sources.append("SIMBAD")
        if ned_row:
            sources.append("NED")

        # ---- position ----
        coord, coord_source = self._pick_position(query, coord_input, simbad_row, ned_row, warnings)
        if coord is None:
            raise LookupError(f"Could not resolve '{query}' with SIMBAD, NED or Sesame")
        if coord_source == "Sesame":
            sources.append("Sesame")

        # ---- kinematics ----
        kinematics = self._kinematics(simbad_row, ned_row)

        # ---- morphology ----
        morphology = self._morphology(simbad_row, ned_row, warnings)

        # ---- distances ----
        distances = [
            Distance(
                value=float(r["dist"]),
                unit=str(r.get("unit") or ""),
                method=r.get("method"),
                bibcode=r.get("bibcode"),
                source="SIMBAD mesDistance",
            )
            for r in dist_rows
            if r.get("dist") is not None
        ]

        # ---- environment ----
        environment = self._environment(coord, aliases, simbad_row, warnings)

        ra_hms, dec_dms = format_coordinates(coord)
        gal = coord.galactic
        coordinates = Coordinates(
            ra_deg=float(coord.icrs.ra.deg),
            dec_deg=float(coord.icrs.dec.deg),
            ra_hms=ra_hms,
            dec_dms=dec_dms,
            gal_l_deg=float(gal.l.deg),
            gal_b_deg=float(gal.b.deg),
            source=coord_source,
        )

        main_id = (simbad_row or {}).get("main_id")
        if main_id and main_id not in aliases:
            aliases.insert(0, main_id)

        return ResolvedTarget(
            query=query,
            input_kind=input_kind,
            main_id=main_id,
            ned_name=(ned_row or {}).get("Object Name"),
            aliases=aliases,
            coordinates=coordinates,
            kinematics=kinematics,
            morphology=morphology,
            distances=distances,
            environment=environment,
            suggested_fov_deg=self._suggested_fov(morphology),
            sources=sources,
            warnings=warnings,
        )

    # -- helpers -----------------------------------------------------------------------------
    @staticmethod
    def _guard(label: str, warnings: list[str], fn, *args):
        """Call ``fn`` and convert any exception into a warning + ``None``."""
        try:
            return fn(*args)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: remote services
            log.warning("%s query failed: %s", label, exc)
            warnings.append(f"{label} unavailable: {type(exc).__name__}: {exc}")
            return None

    def _simbad_nearest(self, coord: SkyCoord, warnings: list[str]) -> dict[str, Any] | None:
        rows = self._guard(
            "SIMBAD cone search",
            warnings,
            self.simbad.basic_near,
            float(coord.icrs.ra.deg),
            float(coord.icrs.dec.deg),
            COORD_MATCH_RADIUS_ARCSEC / 3600.0,
            5,
        )
        if not rows:
            warnings.append(
                f"No SIMBAD object within {COORD_MATCH_RADIUS_ARCSEC:.0f} arcsec "
                "of the given position"
            )
            return None
        return rows[0]

    def _pick_position(self, query, coord_input, simbad_row, ned_row, warnings):
        if simbad_row and simbad_row.get("ra") is not None and simbad_row.get("dec") is not None:
            return (
                SkyCoord(ra=simbad_row["ra"] * u.deg, dec=simbad_row["dec"] * u.deg, frame="icrs"),
                "SIMBAD",
            )
        if ned_row and ned_row.get("RA") is not None and ned_row.get("DEC") is not None:
            return (
                SkyCoord(ra=ned_row["RA"] * u.deg, dec=ned_row["DEC"] * u.deg, frame="icrs"),
                "NED",
            )
        if coord_input is not None:
            return coord_input.icrs, "user input"
        # Last resort: CDS Sesame name resolver (SIMBAD -> NED -> VizieR).
        try:
            return SkyCoord.from_name(query).icrs, "Sesame"
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Sesame unavailable: {type(exc).__name__}: {exc}")
            return None, None

    @staticmethod
    def _kinematics(simbad_row, ned_row) -> Kinematics:
        k = Kinematics()
        simbad_ok = bool(
            simbad_row
            and (
                simbad_row.get("rvz_redshift") is not None
                or simbad_row.get("rvz_radvel") is not None
            )
        )
        ned_ok = bool(
            ned_row and (ned_row.get("Redshift") is not None or ned_row.get("Velocity") is not None)
        )
        # SIMBAD quality flags run A (best) to E; D/E values can be hundreds of km/s off.
        simbad_poor = simbad_ok and str(simbad_row.get("rvz_qual") or "").upper() in ("D", "E")

        if ned_ok:
            z = ned_row.get("Redshift")
            v = ned_row.get("Velocity")
            k.ned_redshift = float(z) if z is not None else float(v) / C_KMS
            k.ned_velocity_kms = float(v) if v is not None else float(z) * C_KMS

        if simbad_ok and not (simbad_poor and ned_ok):
            z = simbad_row.get("rvz_redshift")
            v = simbad_row.get("rvz_radvel")
            err = simbad_row.get("rvz_err")
            rtype = simbad_row.get("rvz_type")
            k.redshift = float(z) if z is not None else (v / C_KMS if v is not None else None)
            k.velocity_kms = float(v) if v is not None else (z * C_KMS if z is not None else None)
            k.measurement_type = rtype
            k.quality = simbad_row.get("rvz_qual")
            if err is not None:
                if rtype == "z":
                    k.redshift_err = float(err)
                    k.velocity_err_kms = float(err) * C_KMS
                else:
                    k.velocity_err_kms = float(err)
                    k.redshift_err = float(err) / C_KMS
            k.source = "SIMBAD"
            return k
        if ned_ok:
            k.redshift = k.ned_redshift
            k.velocity_kms = k.ned_velocity_kms
            k.source = "NED" + (" (SIMBAD value has quality D/E)" if simbad_poor else "")
            k.quality = simbad_row.get("rvz_qual") if simbad_poor else None
        return k

    def _morphology(self, simbad_row, ned_row, warnings) -> Morphology:
        m = Morphology()
        if simbad_row:
            m.type = simbad_row.get("morph_type")
            m.quality = simbad_row.get("morph_qual")
            m.object_type = simbad_row.get("otype")
            if m.object_type:
                m.object_type_label = self._guard(
                    "SIMBAD otypedef", warnings, self.simbad.otype_label, m.object_type
                )
            maj = simbad_row.get("galdim_majaxis")
            mnr = simbad_row.get("galdim_minaxis")
            ang = simbad_row.get("galdim_angle")
            m.major_axis_arcmin = float(maj) if maj is not None else None
            m.minor_axis_arcmin = float(mnr) if mnr is not None else None
            m.position_angle_deg = float(ang) if ang is not None else None
            m.source = "SIMBAD"
        if m.object_type is None and ned_row and ned_row.get("Type"):
            m.object_type = str(ned_row["Type"])
            m.object_type_label = f"NED type {ned_row['Type']}"
            m.source = "NED"
        return m

    def _environment(self, coord, aliases, simbad_row, warnings) -> Environment:
        env = Environment(search_radius_arcmin=ENVIRONMENT_RADIUS_DEG * 60.0)

        for ident in aliases:
            for pattern, catalog in MEMBERSHIP_CATALOGS:
                if pattern.match(ident):
                    env.memberships.append(
                        GroupMembership(name=ident, catalog=catalog, relation="member")
                    )
                    break

        otype = (simbad_row or {}).get("otype")
        if otype in ("GiG", "GiC", "GiP"):
            env.note = {
                "GiG": "SIMBAD classifies this object as a galaxy in a group of galaxies.",
                "GiC": "SIMBAD classifies this object as a galaxy in a cluster of galaxies.",
                "GiP": "SIMBAD classifies this object as a galaxy in a pair of galaxies.",
            }[otype]

        rows = self._guard(
            "SIMBAD environment search",
            warnings,
            self.simbad.groups_near,
            float(coord.icrs.ra.deg),
            float(coord.icrs.dec.deg),
            ENVIRONMENT_RADIUS_DEG,
            10,
        )
        main_id = (simbad_row or {}).get("main_id")
        for r in rows or []:
            if not r.get("main_id") or r["main_id"] == main_id:
                continue
            sep = r.get("sep_deg")
            env.nearby_groups.append(
                GroupMembership(
                    name=r["main_id"],
                    relation="nearby",
                    object_type=r.get("otype"),
                    separation_arcmin=float(sep) * 60.0 if sep is not None else None,
                    redshift=float(r["rvz_redshift"])
                    if r.get("rvz_redshift") is not None
                    else None,
                )
            )
        if otype in GROUP_OTYPES and env.note is None:
            env.note = "The target itself is a group/cluster/pair of galaxies."
        return env

    @staticmethod
    def _suggested_fov(morphology: Morphology) -> float:
        if morphology.major_axis_arcmin:
            fov = 4.0 * morphology.major_axis_arcmin / 60.0
            return float(min(max(fov, 0.05), 3.0))
        if morphology.object_type in GROUP_OTYPES:
            return 1.5
        return DEFAULT_FOV_DEG


_resolver: TargetResolver | None = None
_resolver_lock = threading.Lock()


def get_resolver() -> TargetResolver:
    global _resolver
    with _resolver_lock:
        if _resolver is None:
            _resolver = TargetResolver()
        return _resolver
