"""Detect telescopes / instruments mentioned in paper titles, abstracts and keywords."""

# ruff: noqa: E501  (regex table reads better on single lines)

from __future__ import annotations

import re

# canonical name -> regex (case-insensitive, word-bounded where sensible)
FACILITY_PATTERNS: dict[str, str] = {
    "HST": r"\bHST\b|Hubble Space Telescope|\bHubble\b|\bWFC3\b|\bACS/WFC\b|\bWFPC2\b|\bCOS\b(?= spectr)|\bSTIS\b",
    "JWST": r"\bJWST\b|James Webb|\bWebb\b|\bNIRCam\b|\bMIRI\b|\bNIRSpec\b|\bNIRISS\b",
    "MUSE": r"\bMUSE\b",
    "VLT": r"\bVLT\b|Very Large Telescope|\bX-?Shooter\b|\bUVES\b|\bSINFONI\b|\bKMOS\b|\bFORS[12]?\b|\bERIS\b|\bSPHERE\b|\bNACO\b|\bHAWK-I\b",
    "ALMA": r"\bALMA\b|Atacama Large Millimeter",
    "NOEMA/IRAM": r"\bNOEMA\b|\bIRAM\b|Plateau de Bure|\bPdBI\b",
    "SMA": r"\bSMA\b|Submillimeter Array",
    "Chandra": r"\bChandra\b|\bACIS\b",
    "XMM-Newton": r"\bXMM\b|XMM-Newton|\bEPIC\b(?= cam| pn| MOS)",
    "NuSTAR": r"\bNuSTAR\b",
    "Swift": r"\bSwift\b/?(?:XRT|BAT|UVOT)?",
    "eROSITA": r"\beROSITA\b",
    "Fermi": r"\bFermi\b|\bLAT\b(?= gamma| data)",
    "Spitzer": r"\bSpitzer\b|\bIRAC\b|\bMIPS\b",
    "Herschel": r"\bHerschel\b|\bPACS\b|\bSPIRE\b",
    "WISE": r"\bWISE\b|\bNEOWISE\b|\bunWISE\b|\bAllWISE\b",
    "2MASS": r"\b2MASS\b",
    "GALEX": r"\bGALEX\b",
    "SDSS": r"\bSDSS\b|Sloan Digital Sky Survey|\bMaNGA\b",
    "Gaia": r"\bGaia\b",
    "Euclid": r"\bEuclid\b",
    "Keck": r"\bKeck\b|\bKCWI\b|\bOSIRIS\b|\bDEIMOS\b|\bLRIS\b|\bMOSFIRE\b",
    "Gemini": r"\bGemini\b(?!-)|\bGMOS\b|\bNIFS\b|\bGNIRS\b",
    "Subaru": r"\bSubaru\b|Hyper Suprime-Cam|\bHSC\b",
    "LBT": r"\bLBT\b|Large Binocular",
    "Magellan": r"\bMagellan\b|\bIMACS\b|\bFIRE\b(?= spectr)",
    "VLA": r"\bVLA\b|Very Large Array|\bJVLA\b|\bEVLA\b|\bVLASS\b|\bNVSS\b|\bFIRST\b(?= survey| radio)",
    "MeerKAT": r"\bMeerKAT\b",
    "GMRT": r"\bu?GMRT\b|Giant Metrewave",
    "ATCA": r"\bATCA\b|Australia Telescope Compact Array",
    "ASKAP": r"\bASKAP\b|\bWALLABY\b|\bEMU\b(?= survey)|\bRACS\b",
    "Parkes": r"\bParkes\b|\bHIPASS\b|\bMurriyang\b",
    "Arecibo": r"\bArecibo\b|\bALFALFA\b",
    "FAST": r"\bFAST\b(?= telescope| radio| HI)|Five-hundred-meter",
    "GBT": r"\bGBT\b|Green Bank",
    "Effelsberg": r"\bEffelsberg\b",
    "WSRT/Apertif": r"\bWSRT\b|Westerbork|\bApertif\b",
    "LOFAR": r"\bLOFAR\b|\bLoTSS\b",
    "MWA": r"\bMWA\b|Murchison Widefield",
    "VLBI": r"\bVLBI\b|\bVLBA\b|\bEVN\b|\bEHT\b|Event Horizon Telescope",
    "Planck": r"\bPlanck\b",
    "SOFIA": r"\bSOFIA\b",
    "IRAS": r"\bIRAS\b",
    "ISO": r"\bISO\b(?= satellite| observ| spectr)|Infrared Space Observatory",
    "AKARI": r"\bAKARI\b",
    "TESS": r"\bTESS\b",
    "Kepler": r"\bKepler\b(?! mission\b)",
    "ROSAT": r"\bROSAT\b",
    "Suzaku": r"\bSuzaku\b",
    "INTEGRAL": r"\bINTEGRAL\b",
    "AstroSat": r"\bAstroSat\b",
    "HESS/MAGIC/VERITAS": r"\bH\.E\.S\.S\.|\bHESS\b|\bMAGIC\b|\bVERITAS\b|\bCTA\b(?= observ)",
    "IceCube": r"\bIceCube\b",
    "LIGO/Virgo": r"\bLIGO\b|\bVirgo\b(?= detector| collaboration| interferometer)",
    "DES/DECam": r"\bDES\b(?= survey| data)|\bDECam\b|\bDECaLS\b|Dark Energy Survey",
    "Pan-STARRS": r"Pan-STARRS|\bPS1\b",
    "ZTF": r"\bZTF\b|Zwicky Transient",
    "LSST/Rubin": r"\bLSST\b|\bRubin\b",
    "SKA": r"\bSKA\b(?!O)|Square Kilometre",
    "CFHT": r"\bCFHT\b|Canada-France-Hawaii|\bMegaCam\b",
    "WIYN/KPNO": r"\bWIYN\b|\bKPNO\b|Kitt Peak|\bMayall\b|\bDESI\b",
    "CTIO/Blanco": r"\bCTIO\b|\bBlanco\b",
    "SALT": r"\bSALT\b(?= telescope| spectr| observ)",
    "NTT/La Silla": r"\bNTT\b|La Silla|\bEFOSC\b|\bSOFI\b",
    "APEX": r"\bAPEX\b",
    "JCMT": r"\bJCMT\b|\bSCUBA-?2?\b",
    "Nobeyama": r"\bNobeyama\b",
    "Arecibo/GBT HI": r"\bHI 21\s?cm\b",  # not a facility; kept as a topic hint, filtered below
}

_TOPIC_ONLY = {"Arecibo/GBT HI"}

_COMPILED = {name: re.compile(pat, re.IGNORECASE) for name, pat in FACILITY_PATTERNS.items()}


def detect_facilities(*texts: str | None) -> list[str]:
    """Facilities mentioned in any of the given texts, in canonical order."""
    blob = " ".join(t for t in texts if t)
    if not blob:
        return []
    found = [name for name, rx in _COMPILED.items() if name not in _TOPIC_ONLY and rx.search(blob)]
    return found
