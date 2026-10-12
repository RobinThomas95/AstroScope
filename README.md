# AstroScope

Astronomical target reconnaissance and multi-wavelength analysis. Type an object name or a
coordinate pair, get its basic properties from SIMBAD / NED, and inspect it in four synchronised
Aladin Lite v3 sky views across different surveys.

## Status

| Module | Description | State |
| --- | --- | --- |
| 1 | Target resolver & basic metadata (SIMBAD TAP, NED, Sesame) | implemented |
| 2 | Synced 2x2 Aladin Lite viewer with per-pane surveys and MOC overlays | implemented |
| 3 | Archive cross-matching via ObsCore TAP (ESO, MAST, ALMA) + HI surveys | implemented |
| 4 | ESO DataLink/SODA MUSE cutouts, 1D spectra, HI overplot | implemented |
| 5 | ADS literature & bibliometrics | implemented |
| 6 | Scientific synthesis with Google Gemini | implemented |

## Layout

```
backend/            FastAPI application (Python 3.11+)
  app/main.py       app factory, CORS, routers
  app/config.py     settings (loaded from the repo-root .env)
  app/schemas/      Pydantic response models
  app/services/     coords parsing, SIMBAD TAP client, NED client, resolver, survey catalog,
                    MOCServer client, ObsCore adapters (obscore.py), STC-S parser, footprint
                    MOC builder, HI cross-match (hi.py), SODA cutouts + spectrum extraction
                    (muse.py), spectral lines (lines.py), HI profile template (hiprofile.py)
  app/routers/      /api/resolve, /api/surveys, /api/overlays, /api/archives, /api/hi,
                    /api/spectrum/muse, /api/lines, /api/hi/profile, /api/health
  tests/            pytest suite (remote services are faked; no network needed)
frontend/           React 18 + TypeScript + Tailwind, Vite
  src/lib/viewSync.ts      pan/zoom/FOV synchronisation between Aladin instances
  src/components/          TargetSearch, TargetCard, AladinGrid, AladinPane, ArchivePanel,
                           SpectrumPanel (Plotly)
```

## Quick start

```bash
cp .env.example .env            # optional server-side ADS / Gemini keys (see "API keys" below)
make setup                      # python venv + npm install (picks the first Python 3.11+ on PATH)
# make setup PY=python3.12      # or name the interpreter explicitly, e.g. inside a conda env
make backend                    # http://localhost:8000  (OpenAPI docs at /docs)
make frontend                   # http://localhost:5173  (proxies /api to the backend)
```

Then open http://localhost:5173 and resolve e.g. `NGC 1365`, `M 87`, `03h33m36.4s -36d08m25s`
or `187.706, 12.391`.

Run the tests with `make test` (backend: pytest, frontend: vitest) and `make lint`.

## API

### `GET /api/resolve?q=<name or coordinates>[&refresh=true]`

Resolves the target and returns (see `backend/app/schemas/target.py`):

- `coordinates`: ICRS RA/Dec in degrees and sexagesimal, galactic l/b, and which service provided them
- `kinematics`: redshift `z`, recessional velocity `cz` in km/s, errors, measurement type
- `morphology`: morphological type (e.g. `SB(s)b`), SIMBAD object type and label, major/minor axis, PA
- `distances`: redshift-independent distances from SIMBAD's `mesDistance` table
- `environment`: cataloged group/cluster memberships (identifiers from FCC, VCC, LGG, HCG, Abell, ...)
  and galaxy groups/clusters/pairs found by a 1 degree SIMBAD cone search
- `aliases`, `sources`, `warnings`, `suggested_fov_deg`

Accepted inputs: any SIMBAD/NED identifier, decimal degrees (`53.40 -36.14`), sexagesimal
(`03h33m36.4s -36d08m25s`, `03:33:36.4 -36:08:25`, `03 33 36.4 -36 08 25`) or compact IAU form
(`J033336.4-360825`). Coordinates are matched to the nearest SIMBAD object within 30 arcsec.

Resolution strategy: SIMBAD TAP (explicit ADQL via pyvo) is the primary source; NED provides an
independent name/redshift and is the position fallback; CDS Sesame is the last-resort position
fallback. Every remote call is isolated, so an outage of one service appears in `warnings` instead
of failing the request. Results are cached in memory for `ASTROSCOPE_CACHE_TTL` seconds.

### `GET /api/surveys`

The HiPS surveys offered per pane, the coverage (MOC) overlays, and the default 2x2 layout.
Edit `backend/app/services/surveys.py` to change them.

### Coverage overlays: `GET /api/overlays`, `GET /api/overlays/{id}/moc`

Aladin Lite's `A.MOCFromURL` needs a FITS MOC, so overlays are served by the backend rather than
fetched from CDS directly. For each overlay the backend searches the CDS MOCServer with the
expressions listed in `surveys.py` (tried in order, e.g. `ID=ESAVO/P/JWST/*`), takes every
matching record with non-zero coverage, downloads each MOC as FITS, unions them with mocpy and
caches the result for a day. `GET /api/overlays` shows which records each overlay resolved to,
which is the first thing to check when an overlay shows "failed" in a pane's MOC menu.

The MOCServer has no single all-observations record for HST or JWST, so those overlays union
ESA's per-instrument image coverage maps. It holds no downloadable coverage at all for the MUSE
and ALMA cube collections, so MUSE and ALMA footprints are derived per target from the ESO and
ALMA ObsCore services in the archive module instead.

### `GET /api/archives?ra=&dec=&radius_arcmin=[&archives=eso,mast,alma]`

Queries the ESO (`ivoa.ObsCore`, MUSE / UVES / X-Shooter), MAST (`dbo.CaomObservation`, HST and
JWST) and ALMA (`ivoa.obscore`, science observations) TAP services in parallel and normalises
the rows: instrument, observation date, integration time, spectral band, spatial resolution,
footprint (`s_region`), data / preview / archive links, proposal and release status. Each
archive reports its own status so one outage does not hide the others. ESO and ALMA are searched
with `INTERSECTS` on the footprint (falling back to a centre-point search if a service rejects
it); MAST with a centre-point search.

### `GET /api/archives/{archive}/footprints/moc?ra=&dec=&radius_arcmin=[&instrument=|&collection=]`

The union of the matching observations' footprints as a FITS MOC, built from the STC-S regions
with mocpy. The viewer's "footprints around the target" overlays (MUSE, ALMA, HST, JWST) use this.

### `GET /api/hi?ra=&dec=[&radius_arcmin=]`

Cross-matches the position against ALFALFA α.100 (`J/ApJ/861/49`), HIPASS HICAT (`VIII/73`),
the northern HIPASS catalogue NHICAT (`VIII/89`) and the HIPASS Bright Galaxy Catalog
(`J/AJ/128/16`) on VizieR, within half the survey beam (minimum 3 arcmin) unless a radius is
given. (The Springob+ 2005 digital HI archive is not hosted on VizieR.) Returns heliocentric velocity, W50, W20, integrated
21 cm flux, S/N, distance and log M_HI where the catalogue provides them.

### `GET /api/spectrum/muse?datalink_url=&ra=&dec=[&radius_arcsec=3&method=sum|mean&band_min_A=&band_max_A=]`

Requests a spatial (and optionally spectral) cutout of a public MUSE cube through the cube's ESO
DataLink document and its SODA service (pyvo), then integrates the cube over a circular aperture
at the target position: wavelength array (Å), flux, 1σ error from the STAT extension, flux unit
from `BUNIT`, number of spaxels, pixel scale, and cube provenance. Only ESO DataLink URLs are
accepted, the aperture is capped at 30″, datasets without a SODA service are refused (no
full-cube downloads), and cutouts/spectra are cached in memory.

### `GET /api/lines?z=&wmin=&wmax=` and `GET /api/hi/profile?ra=&dec=`

`/api/lines` returns common optical lines (Balmer, [OIII], [NII], [SII], Na D, Ca triplet, ...)
redshifted to `z` and restricted to an observed window. `/api/hi/profile` builds a symmetric
double-horn 21 cm profile from the nearest catalogued HI detection (systemic velocity, W50, W20,
integrated flux). It is a template for overplotting, clearly labelled as such; an observed
profile can be uploaded in the UI as a two-column (velocity, flux) text file.

### `GET /api/cubes/products` and `GET /api/spectrum/cube` (ALMA)

ALMA has no SODA cutout service, so for an ALMA observation the backend lists the FITS cube
products in its DataLink document (one per spectral window), marks the window that contains the
chosen line at the target's redshift, downloads the selected product under a size cap
(`ASTROSCOPE_CUBE_MAX_MB`, default 600 MB, cached on disk under the system temp directory) and
extracts the same aperture spectrum, returning a frequency axis as well. Frequency and velocity
cube axes (FREQ, VRAD, VOPT) are handled; 4-D cubes use the first Stokes plane.
`GET /api/lines/radio` lists common mm lines (CO, 13CO, HCN, HCO+, [CI], ...) redshifted.

### Spectrum tab

Any number of traces (MUSE via SODA, ALMA via product cubes) can be overplotted. In velocity
mode each optical trace is placed on a velocity axis about the chosen optical line (Hα by
default, optical convention) and each radio trace about the chosen mm line (CO(2–1) by default,
radio convention), optionally relative to the systemic velocity, within an adjustable window.
"Normalise amplitudes" scales every trace to its own peak inside the window so line shapes can
be compared directly; the fluxes, angular resolutions and calibrations of the traces are not
comparable and the panel says so. The HI profile (catalogue template or an uploaded two-column
file) joins the same axis. The Spectrum and Literature panels keep their state across tab
switches.

## Viewer synchronisation

Aladin Lite v3 has no built-in view linking, so `frontend/src/lib/viewSync.ts` implements it on
top of the public callbacks: each pane reports `positionChanged` / `zoomChanged`, and the
controller pushes the new centre and field of view to the other panes with `gotoRaDec` /
`setFoV`. Programmatic writes echo back through the same callbacks (sometimes synchronously,
sometimes from the render loop), so the controller mutes a pane while writing to it and then
drops callbacks that merely repeat the last state it pushed. A toggle disables sync; re-enabling
snaps all panes to the pane that was navigated last.

## API keys (Modules 5 and 6)

Modules 1–4 need no keys. The literature module needs a NASA ADS token (free, from
https://ui.adsabs.harvard.edu/user/settings/token) and the synthesis module a Google Gemini API
key (free tier, from https://aistudio.google.com/apikey). There are two ways to provide them:

- **Per user, in the browser**: the "keys" button in the header opens a dialog; keys are kept in
  the browser's local storage and sent as request headers (`X-ADS-Token`, `X-Gemini-Key`). The
  server never stores them. This is the mode for a public instance: every visitor uses their own
  free keys and their own ADS quota.
- **Server defaults** in `.env` (`ADS_DEV_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL`): convenient
  for a private instance. A key pasted in the browser overrides the server default.

`GET /api/keys/status` reports which server defaults exist; `GET /api/llm/models` validates a
Gemini key and lists the models it can use.

### `GET /api/literature?q=&names=&refereed=&max_rows=`

Searches ADS for the target and its catalogue aliases (`object:` and `abs:` fields, astronomy
collection, optionally refereed only), pages through up to 2000 records, and computes the
bibliometrics server-side: papers per year (total and refereed), facilities mentioned (ADS
`facility` field plus pattern matching of ~70 telescopes/instruments in titles, abstracts and
keywords), keyword frequencies, citation distribution, total citations, h-index, most cited and
most recent papers. The Literature tab plots the year histogram with facility chips that isolate
e.g. HST, MUSE, ALMA, GMRT or MeerKAT, the citation distribution, and the paper table with ADS
links.

### `POST /api/synthesis`

Selects abstracts (top cited plus most recent refereed, up to `max_abstracts`), sends them with
the computed statistics to Gemini in JSON mode with a fixed response schema, and returns: a
summary; consensus / established properties; peculiarities and open questions; key papers with
reasons; keyword clusters by theme; facilities and their role; a reading of the publication trend.
Bibcodes cited by the model that were not among the supplied abstracts are flagged in
`warnings`. Results are cached per target, model and abstract set.

## Notes for verification

The development sandbox used to build this could not reach CDS, SIMBAD or NED, so the resolver
is tested against faked service responses. The first live run should confirm:

1. `GET /api/resolve?q=NGC%201365` returns `main_id = "NGC  1365"`, z ~ 0.0055, type `SB(s)b`,
   and `FCC 121` under environment memberships.
2. `GET /api/overlays` reports `resolved: true` for each overlay and ticking one in a pane draws
   coverage (verified live for HST, JWST, Chandra and XMM-Newton).
3. `GET /api/archives?ra=53.4015&dec=-36.1404&radius_arcmin=5` returns MUSE, HST, JWST and ALMA
   observations of NGC 1365 with `status: ok` for all three archives, and the MUSE footprint
   overlay draws the PHANGS-MUSE mosaic. The adapters were built against the public column
   names of each service and tested with canned rows; the MAST table name
   (`dbo.CaomObservation`) and the ALMA/ESO `INTERSECTS` support are the parts to confirm live.
4. `GET /api/hi?ra=53.4015&dec=-36.1404` lists HIPASS detections for NGC 1365 (ALFALFA does
   not cover Dec −36).
5. In the observation list, press "spectrum" on a public MUSE cube of NGC 1365: the Spectrum tab
   should show a 4750–9350 Å spectrum with Hα, [NII] and [SII] marked near 6600 Å (verified live).
6. With an ADS token set, the Literature tab lists several hundred refereed papers on NGC 1365
   with a year histogram; with a Gemini key, "synthesise" returns the structured briefing.
   Both services were exercised offline with canned responses; the first live calls confirm the
   ADS query syntax and the Gemini JSON-schema mode for the configured model.
