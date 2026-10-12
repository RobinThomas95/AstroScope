"""Models for Module 4 (MUSE cutouts, 1D spectra, spectral lines, HI profiles)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SpectralLine(BaseModel):
    name: str
    rest_wavelength_A: float = Field(description="Rest wavelength (vacuum), Angstrom")
    observed_wavelength_A: float | None = Field(default=None, description="(1+z) * rest")
    kind: str = Field(description="emission | absorption | both")
    group: str = Field(default="", description="e.g. Balmer, [OIII], NaD")


class Spectrum1D(BaseModel):
    wavelength_A: list[float]
    flux: list[float]
    error: list[float | None] | None = None
    flux_unit: str
    wavelength_unit: str = "Angstrom"
    method: str = Field(description="sum | mean over the aperture")
    n_spaxels: int
    aperture_radius_arcsec: float
    center_ra_deg: float
    center_dec_deg: float
    pixel_scale_arcsec: float | None = None
    n_channels: int
    source: dict[str, str | float | int | None] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    cached: bool = False


class HIProfile(BaseModel):
    velocity_kms: list[float] = Field(description="Heliocentric velocity (cz)")
    flux_jy: list[float]
    model: str
    source_id: str | None = None
    survey: str | None = None
    velocity_sys_kms: float | None = None
    w50_kms: float | None = None
    w20_kms: float | None = None
    integrated_flux_jy_kms: float | None = None
    peak_jy: float | None = None
    note: str
