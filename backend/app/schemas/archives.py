"""Models for archive cross-matching (Module 3)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ArchiveObservation(BaseModel):
    archive: str = Field(description="Archive key: eso, mast, alma")
    collection: str | None = Field(default=None, description="Mission / programme (HST, JWST, ...)")
    instrument: str | None = None
    obs_id: str | None = None
    dataproduct_type: str | None = None
    calib_level: int | None = None
    target_name: str | None = None
    date_obs: str | None = Field(default=None, description="Start of observation, ISO 8601 UTC")
    mjd_obs: float | None = None
    exptime_s: float | None = Field(default=None, description="Integration time, seconds")
    band: str | None = Field(default=None, description="Human readable spectral band")
    em_min_m: float | None = Field(default=None, description="Shortest wavelength, metres")
    em_max_m: float | None = Field(default=None, description="Longest wavelength, metres")
    resolution_arcsec: float | None = Field(default=None, description="Spatial resolution")
    fov_deg: float | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    separation_arcmin: float | None = None
    s_region: str | None = Field(default=None, description="Footprint as STC-S")
    access_url: str | None = Field(default=None, description="Data access / DataLink URL")
    preview_url: str | None = None
    archive_url: str | None = Field(default=None, description="Human-facing archive page")
    proposal_id: str | None = None
    release_date: str | None = None
    public: bool | None = None
    extra: dict[str, str | float | int | None] = Field(default_factory=dict)


class ArchiveResult(BaseModel):
    archive: str
    label: str
    status: str = Field(description="ok | error | skipped")
    error: str | None = None
    table: str | None = None
    count: int = 0
    truncated: bool = False
    elapsed_s: float | None = None
    observations: list[ArchiveObservation] = Field(default_factory=list)


class ArchiveSearch(BaseModel):
    ra_deg: float
    dec_deg: float
    radius_arcmin: float
    results: list[ArchiveResult]
    instrument_counts: dict[str, int] = Field(default_factory=dict)


class HIDetection(BaseModel):
    survey: str
    catalog: str = Field(description="VizieR table")
    source_id: str | None = None
    name: str | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    separation_arcmin: float | None = None
    velocity_kms: float | None = Field(default=None, description="Heliocentric cz")
    velocity_err_kms: float | None = None
    w50_kms: float | None = None
    w50_err_kms: float | None = None
    w20_kms: float | None = None
    flux_jy_kms: float | None = Field(default=None, description="Integrated 21 cm flux")
    flux_err_jy_kms: float | None = None
    snr: float | None = None
    rms_mjy: float | None = None
    distance_mpc: float | None = None
    log_mhi: float | None = None
    vizier_url: str | None = None


class HISurveyResult(BaseModel):
    survey: str
    catalog: str
    status: str
    error: str | None = None
    detections: list[HIDetection] = Field(default_factory=list)


class HISearch(BaseModel):
    ra_deg: float
    dec_deg: float
    radius_arcmin: float
    results: list[HISurveyResult]
