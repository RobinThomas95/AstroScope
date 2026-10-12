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
| 4 | ESO DataLink/SODA MUSE cutouts, 1D spectra, HI overplot | planned |
| 5 | ADS literature & bibliometrics | planned |
| 6 | LLM scientific synthesis | planned |

## Layout

```
backend/            FastAPI application (Python 3.11+)
  app/main.py       app factory, CORS, routers
  app/config.py     settings (loaded from the repo-root .env)
  app/schemas/      Pydantic response models
  app/services/     coords parsing, SIMBAD TAP client, NED client, resolver, survey catalog,
                    MOCServer client, ObsCore adapters (obscore.py), STC-S parser, footprint
                    MOC builder, HI cross-match (hi.py)
  app/routers/      /api/resolve, /api/surveys, /api/overlays, /api/archives, /api/hi, /api/health
  tests/            pytest suite (remote services are faked; no network needed)
frontend/           React 18 + TypeScript + Tailwind, Vite
  src/lib/viewSync.ts      pan/zoom/FOV synchronisation between Aladin instances
  src/components/          TargetSearch, TargetCard, AladinGrid, AladinPane, ArchivePanel
```

## Quick start

```bash
cp .env.example .env            # fill in ADS_DEV_KEY / LLM keys later (not needed for modules 1-2)
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

Cross-matches the position against ALFALFA α.100 (`J/ApJ/861/49`), HIPASS HICAT (`VIII/73`) and
the Springob+ 2005 HI archive (`J/ApJS/160/149`) on VizieR, within half the survey beam
(minimum 3 arcmin) unless a radius is given. Returns heliocentric velocity, W50, W20, integrated
21 cm flux, S/N, distance and log M_HI where the catalogue provides them.

## Viewer synchronisation

Aladin Lite v3 has no built-in view linking, so `frontend/src/lib/viewSync.ts` implements it on
top of the public callbacks: each pane reports `positionChanged` / `zoomChanged`, and the
controller pushes the new centre and field of view to the other panes with `gotoRaDec` /
`setFoV`. Programmatic writes echo back through the same callbacks (sometimes synchronously,
sometimes from the render loop), so the controller mutes a pane while writing to it and then
drops callbacks that merely repeat the last state it pushed. A toggle disables sync; re-enabling
snaps all panes to the pane that was navigated last.

## Configuration

See `.env.example`. Keys for ADS and the LLM provider are read but not used until modules 5-6.

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
4. `GET /api/hi?ra=53.4015&dec=-36.1404` lists HIPASS and Springob detections for NGC 1365
   (ALFALFA does not cover Dec −36).
