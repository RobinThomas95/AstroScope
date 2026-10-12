"""Optical / near-infrared spectral lines commonly seen in MUSE and other optical spectra.

Vacuum rest wavelengths in Angstrom.
"""

from __future__ import annotations

from app.schemas.spectra import SpectralLine

# (name, vacuum wavelength, kind, group)
_LINES: list[tuple[str, float, str, str]] = [
    ("Hδ", 4102.89, "both", "Balmer"),
    ("Hγ", 4341.68, "both", "Balmer"),
    ("[OIII] 4363", 4364.44, "emission", "[OIII]"),
    ("HeII 4686", 4687.02, "emission", "HeII"),
    ("Hβ", 4862.68, "both", "Balmer"),
    ("[OIII] 4959", 4960.30, "emission", "[OIII]"),
    ("[OIII] 5007", 5008.24, "emission", "[OIII]"),
    ("Mg b", 5176.70, "absorption", "stellar"),
    ("[NI] 5200", 5201.70, "emission", "[NI]"),
    ("Fe 5270", 5270.00, "absorption", "stellar"),
    ("HeI 5876", 5877.25, "emission", "HeI"),
    ("Na D", 5891.58, "absorption", "NaD"),
    ("Na D", 5897.56, "absorption", "NaD"),
    ("[OI] 6300", 6302.05, "emission", "[OI]"),
    ("[OI] 6364", 6365.54, "emission", "[OI]"),
    ("[NII] 6548", 6549.86, "emission", "[NII]"),
    ("Hα", 6564.61, "both", "Balmer"),
    ("[NII] 6583", 6585.27, "emission", "[NII]"),
    ("HeI 6678", 6679.99, "emission", "HeI"),
    ("[SII] 6716", 6718.29, "emission", "[SII]"),
    ("[SII] 6731", 6732.67, "emission", "[SII]"),
    ("[ArIII] 7136", 7137.77, "emission", "[ArIII]"),
    ("[OII] 7320", 7321.94, "emission", "[OII]"),
    ("[OII] 7330", 7332.21, "emission", "[OII]"),
    ("[SIII] 9069", 9071.10, "emission", "[SIII]"),
    ("Ca II 8498", 8500.36, "absorption", "CaT"),
    ("Ca II 8542", 8544.44, "absorption", "CaT"),
    ("Ca II 8662", 8664.52, "absorption", "CaT"),
    ("Paη", 9017.38, "emission", "Paschen"),
    ("Paζ", 9231.55, "emission", "Paschen"),
]


def spectral_lines(
    z: float | None = None, wmin: float | None = None, wmax: float | None = None
) -> list[SpectralLine]:
    """Line list, optionally redshifted and restricted to an observed wavelength window."""
    out: list[SpectralLine] = []
    for name, rest, kind, group in _LINES:
        obs = rest * (1.0 + z) if z is not None else None
        if obs is not None and wmin is not None and obs < wmin:
            continue
        if obs is not None and wmax is not None and obs > wmax:
            continue
        out.append(
            SpectralLine(
                name=name, rest_wavelength_A=rest, observed_wavelength_A=obs, kind=kind, group=group
            )
        )
    return out


# (name, rest frequency in GHz)
_RADIO_LINES: list[tuple[str, float]] = [
    ("HI 21 cm", 1.420405752),
    ("CO(1-0)", 115.2712018),
    ("13CO(1-0)", 110.2013543),
    ("HCN(1-0)", 88.6316023),
    ("HCO+(1-0)", 89.1885247),
    ("CS(2-1)", 97.9809533),
    ("CN(1-0)", 113.4909702),
    ("CO(2-1)", 230.5380000),
    ("13CO(2-1)", 220.3986842),
    ("C18O(2-1)", 219.5603541),
    ("CS(5-4)", 244.9355565),
    ("CO(3-2)", 345.7959899),
    ("HCN(4-3)", 354.5054779),
    ("HCO+(4-3)", 356.7342288),
    ("[CI](1-0)", 492.1606510),
    ("CO(4-3)", 461.0407682),
    ("CO(6-5)", 691.4730763),
    ("[CI](2-1)", 809.3419700),
]


def radio_lines(
    z: float | None = None, fmin_ghz: float | None = None, fmax_ghz: float | None = None
) -> list[dict]:
    """Common mm/radio lines, optionally redshifted and restricted to an observed window."""
    out = []
    for name, rest in _RADIO_LINES:
        obs = rest / (1.0 + z) if z is not None else None
        if obs is not None and fmin_ghz is not None and obs < fmin_ghz:
            continue
        if obs is not None and fmax_ghz is not None and obs > fmax_ghz:
            continue
        out.append({"name": name, "rest_frequency_ghz": rest, "observed_frequency_ghz": obs})
    return out
