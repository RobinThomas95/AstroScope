"""Catalog of HiPS surveys and coverage overlays offered to the Aladin Lite viewer.

HiPS identifiers are the CDS ``P/...`` short IDs understood by Aladin Lite v3's
``setBaseImageLayer``. Overlays are resolved against the CDS MOCServer at request time (see
``mocserver.py``): each overlay lists search expressions tried in order, and the record with the
widest sky coverage wins. The browser then fetches the MOC through ``/api/overlays/{id}/moc``
(same origin, FITS format) which is what Aladin Lite's ``A.MOCFromURL`` needs.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Survey(BaseModel):
    id: str = Field(description="HiPS identifier accepted by Aladin Lite")
    label: str
    band: str = Field(description="Broad wavelength regime")
    description: str = ""
    cmap: str | None = Field(default=None, description="Suggested Aladin colormap for mono surveys")


class Overlay(BaseModel):
    id: str
    label: str
    color: str
    description: str = ""
    expressions: list[str] = Field(
        description="CDS MOCServer search expressions, tried in order, to find the coverage record"
    )

    @property
    def moc_path(self) -> str:
        return f"/api/overlays/{self.id}/moc"


SURVEYS: list[Survey] = [
    Survey(
        id="CDS/P/Rubin/FirstLook",
        label="Rubin First Look",
        band="optical",
        description="Vera C. Rubin Observatory first-look imaging (partial sky)",
    ),
    Survey(
        id="P/DSS2/color",
        label="DSS2 colour",
        band="optical",
        description="Digitized Sky Survey 2, RGB composite",
    ),
    Survey(id="P/DSS2/red", label="DSS2 red", band="optical", cmap="grayscale"),
    Survey(
        id="P/PanSTARRS/DR1/color-z-zg-g",
        label="Pan-STARRS DR1 colour",
        band="optical",
        description="z / zg / g composite (Dec > -30)",
    ),
    Survey(id="P/PanSTARRS/DR1/g", label="Pan-STARRS DR1 g", band="optical", cmap="grayscale"),
    Survey(id="P/SDSS9/color", label="SDSS DR9 colour", band="optical"),
    Survey(id="P/DECaLS/DR5/color", label="DECaLS DR5 colour", band="optical"),
    Survey(id="P/DES-DR2/ColorIRG", label="DES DR2 colour", band="optical"),
    Survey(id="P/GALEXGR6/AIS/color", label="GALEX GR6 AIS (FUV+NUV)", band="ultraviolet"),
    Survey(id="P/GALEXGR6_7/NUV", label="GALEX GR6/7 NUV", band="ultraviolet", cmap="grayscale"),
    Survey(id="P/2MASS/color", label="2MASS J/H/K", band="near-infrared"),
    Survey(id="P/allWISE/color", label="AllWISE colour", band="mid-infrared"),
    Survey(id="P/unWISE/color-W2-W1W2-W1", label="unWISE W1/W2", band="mid-infrared"),
    Survey(
        id="P/Finkbeiner",
        label="Finkbeiner H-alpha",
        band="optical (narrow-band)",
        cmap="grayscale",
    ),
    Survey(id="P/XMM/EPIC-RGB", label="XMM-Newton EPIC", band="X-ray"),
    Survey(id="P/Fermi/color", label="Fermi LAT", band="gamma-ray"),
]

# Expressions were chosen from a live listing of the MOCServer (Oct 2026). The server has no
# single "all observations" record for HST or JWST, so those overlays union ESA's
# per-instrument image coverage maps. MUSE and ALMA are deliberately absent: the server lists
# CDS cube collections for them but holds no downloadable MOC ("MOC not found"), so their
# footprints are derived per target from the ESO and ALMA ObsCore services in the archive module.
OVERLAYS: list[Overlay] = [
    Overlay(
        id="hst",
        label="HST imaging coverage",
        color="#38bdf8",
        description="Union of the ESA HST per-instrument image coverage maps",
        expressions=["ID=ESAVO/P/HST/*", "ID=CDS/B/hst/*"],
    ),
    Overlay(
        id="jwst",
        label="JWST imaging coverage",
        color="#f472b6",
        description="Union of the ESA JWST NIRCam / MIRI / NIRISS image coverage maps",
        expressions=["ID=ESAVO/P/JWST/*"],
    ),
    Overlay(
        id="chandra",
        label="Chandra observations",
        color="#c084fc",
        description="Sky coverage of the Chandra observation log",
        expressions=["ID=CDS/B/chandra/*", "ID=*chandra*&&dataproduct_type=catalog"],
    ),
    Overlay(
        id="xmm",
        label="XMM-Newton observations",
        color="#facc15",
        description="Sky coverage of the XMM-Newton observation log",
        expressions=["ID=CDS/B/xmm/*", "ID=*xmm*&&dataproduct_type=catalog"],
    ),
]

OVERLAY_BY_ID: dict[str, Overlay] = {o.id: o for o in OVERLAYS}


class TargetOverlay(BaseModel):
    """A footprint overlay computed around the current target from an archive's ObsCore table."""

    id: str
    label: str
    color: str
    description: str = ""
    moc_url_template: str = Field(
        description="API path with {ra}, {dec} and {radius} placeholders (degrees, arcmin)"
    )


TARGET_OVERLAYS: list[TargetOverlay] = [
    TargetOverlay(
        id="muse",
        label="MUSE footprints (ESO archive)",
        color="#a3e635",
        description="Exact footprints of public MUSE cubes near the target",
        moc_url_template="/api/archives/eso/footprints/moc?ra={ra}&dec={dec}&radius_arcmin={radius}&instrument=MUSE",
    ),
    TargetOverlay(
        id="alma",
        label="ALMA footprints (ALMA archive)",
        color="#fb923c",
        description="Footprints of ALMA science observations near the target",
        moc_url_template="/api/archives/alma/footprints/moc?ra={ra}&dec={dec}&radius_arcmin={radius}",
    ),
    TargetOverlay(
        id="jwst-fp",
        label="JWST footprints (MAST)",
        color="#e879f9",
        description="Exact JWST observation footprints near the target",
        moc_url_template="/api/archives/mast/footprints/moc?ra={ra}&dec={dec}&radius_arcmin={radius}&collection=JWST",
    ),
    TargetOverlay(
        id="hst-fp",
        label="HST footprints (MAST)",
        color="#0ea5e9",
        description="Exact HST observation footprints near the target",
        moc_url_template="/api/archives/mast/footprints/moc?ra={ra}&dec={dec}&radius_arcmin={radius}&collection=HST",
    ),
]

DEFAULT_GRID: list[str] = [
    "P/DSS2/color",
    "P/PanSTARRS/DR1/color-z-zg-g",
    "P/GALEXGR6/AIS/color",
    "P/2MASS/color",
]
