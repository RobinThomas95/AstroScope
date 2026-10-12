"""Pydantic models describing a resolved astronomical target."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Coordinates(BaseModel):
    ra_deg: float = Field(description="Right ascension, ICRS/J2000, degrees")
    dec_deg: float = Field(description="Declination, ICRS/J2000, degrees")
    ra_hms: str = Field(description="RA as sexagesimal hours")
    dec_dms: str = Field(description="Dec as sexagesimal degrees")
    gal_l_deg: float | None = None
    gal_b_deg: float | None = None
    source: str | None = Field(default=None, description="Service that provided the position")


class Kinematics(BaseModel):
    redshift: float | None = Field(default=None, description="Systemic redshift z")
    redshift_err: float | None = None
    velocity_kms: float | None = Field(
        default=None, description="Recessional (radial) velocity, km/s"
    )
    velocity_err_kms: float | None = None
    measurement_type: str | None = Field(
        default=None, description="SIMBAD rvz_type: 'v' (velocity) or 'z' (redshift)"
    )
    quality: str | None = Field(default=None, description="SIMBAD rvz_qual (A best .. E)")
    ned_redshift: float | None = Field(default=None, description="NED redshift, for comparison")
    ned_velocity_kms: float | None = None
    source: str | None = None


class Morphology(BaseModel):
    type: str | None = Field(default=None, description="Morphological type, e.g. SB(s)b")
    quality: str | None = None
    object_type: str | None = Field(default=None, description="SIMBAD object type code, e.g. 'Sy1'")
    object_type_label: str | None = Field(default=None, description="Human readable object type")
    major_axis_arcmin: float | None = None
    minor_axis_arcmin: float | None = None
    position_angle_deg: float | None = None
    source: str | None = None


class Distance(BaseModel):
    value: float = Field(description="Distance value")
    unit: str = Field(description="Distance unit, e.g. Mpc")
    method: str | None = None
    bibcode: str | None = None
    source: str = Field(description="Where the distance came from (e.g. SIMBAD mesDistance)")


class GroupMembership(BaseModel):
    """A group/cluster the target is cataloged as belonging to, or that lies nearby."""

    name: str
    catalog: str | None = Field(default=None, description="Group/cluster catalog this came from")
    relation: Literal["member", "nearby"] = Field(
        description=(
            "'member' when the target carries the group identifier; 'nearby' for a cone-search hit"
        )
    )
    object_type: str | None = None
    separation_arcmin: float | None = None
    redshift: float | None = None


class Environment(BaseModel):
    memberships: list[GroupMembership] = Field(default_factory=list)
    nearby_groups: list[GroupMembership] = Field(default_factory=list)
    search_radius_arcmin: float | None = None
    note: str | None = None


class ResolvedTarget(BaseModel):
    query: str
    input_kind: Literal["name", "coordinates"]
    main_id: str | None = Field(default=None, description="Canonical SIMBAD identifier")
    ned_name: str | None = Field(default=None, description="Preferred NED object name")
    aliases: list[str] = Field(default_factory=list)
    coordinates: Coordinates
    kinematics: Kinematics = Field(default_factory=Kinematics)
    morphology: Morphology = Field(default_factory=Morphology)
    distances: list[Distance] = Field(default_factory=list)
    environment: Environment = Field(default_factory=Environment)
    suggested_fov_deg: float = Field(
        description="A sensible initial field of view for the sky viewer, degrees"
    )
    sources: list[str] = Field(default_factory=list, description="Services that returned data")
    warnings: list[str] = Field(default_factory=list)
    cached: bool = False


class ResolveError(BaseModel):
    detail: str
    query: str
