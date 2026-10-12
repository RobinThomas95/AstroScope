"""A catalogue-based 21 cm profile model.

Real HI spectra are rarely available through VO services, so we synthesise a symmetric
double-horn profile from the catalogue measurements (systemic velocity, W50, W20, integrated
flux). It is a *template* for overplotting on the optical spectrum, not an observed spectrum;
an observed profile can be uploaded in the UI instead.
"""

from __future__ import annotations

import math

import numpy as np

from app.schemas.archives import HIDetection
from app.schemas.spectra import HIProfile

# Width at 20 % versus 50 % for an erf-shaped edge: W20 - W50 = 2 * 0.8416 * sigma.
_EDGE_FACTOR = 2 * 0.8416


def double_horn(
    v: np.ndarray, vsys: float, w50: float, w20: float | None, horn_strength: float = 0.4
) -> np.ndarray:
    """Unit-peak-ish double-horn profile sampled at velocities ``v`` (km/s)."""
    if w20 is None or w20 <= w50:
        w20 = w50 + max(20.0, 0.1 * w50)
    sigma = max((w20 - w50) / _EDGE_FACTOR, 2.0)
    lo, hi = vsys - w50 / 2, vsys + w50 / 2
    sq2 = math.sqrt(2)
    erf = np.vectorize(math.erf)
    edges = 0.5 * (erf((v - lo) / (sq2 * sigma)) - erf((v - hi) / (sq2 * sigma)))
    x = np.clip((v - vsys) / (w50 / 2), -1, 1)
    horns = 1.0 + horn_strength * x**2
    return edges * horns


def profile_from_detection(det: HIDetection, n: int = 400, pad: float = 1.5) -> HIProfile:
    vsys = det.velocity_kms
    w50 = det.w50_kms
    if vsys is None or w50 is None or w50 <= 0:
        raise ValueError("HI detection lacks a systemic velocity or W50")
    w20 = det.w20_kms if det.w20_kms and det.w20_kms > w50 else None
    half = pad * (w20 or w50) / 2 + 100.0
    v = np.linspace(vsys - half, vsys + half, n)
    shape = double_horn(v, vsys, w50, w20)
    area = float(np.trapezoid(shape, v)) if hasattr(np, "trapezoid") else float(np.trapz(shape, v))
    if det.flux_jy_kms and area > 0:
        flux = shape * (det.flux_jy_kms / area)
        scaling = f"scaled to the catalogue integrated flux {det.flux_jy_kms:.1f} Jy km/s"
    else:
        flux = shape * 1.0
        scaling = "no integrated flux in the catalogue; arbitrary amplitude"
    return HIProfile(
        velocity_kms=[round(float(x), 3) for x in v],
        flux_jy=[round(float(x), 6) for x in flux],
        model="double-horn template",
        source_id=det.source_id,
        survey=det.survey,
        velocity_sys_kms=vsys,
        w50_kms=w50,
        w20_kms=w20 or det.w20_kms,
        integrated_flux_jy_kms=det.flux_jy_kms,
        peak_jy=float(flux.max()) if len(flux) else None,
        note=(
            f"Synthetic profile from {det.survey} catalogue values (Vsys={vsys:.0f} km/s, "
            f"W50={w50:.0f}, W20={w20 or det.w20_kms or 'n/a'}), {scaling}. "
            "Not an observed spectrum."
        ),
    )
