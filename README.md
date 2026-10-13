# AstroScope

AstroScope is a browser application for reconnaissance of one astronomical target at a time.
Type a galaxy name or a coordinate pair and it gathers, in one page, what the Virtual
Observatory knows about that object: its basic properties, synchronised sky views across
surveys, every public observation of it in the ESO, MAST and ALMA archives, its 21 cm detections,
aperture spectra extracted on demand from MUSE and ALMA cubes and overplotted on one velocity
axis, its literature with bibliometrics, and an AI-written briefing of that literature.

It runs locally: a Python backend talks to the archives, a React frontend shows the results.
Nothing is uploaded anywhere, and the only things written to your disk are the optional
full-cube downloads described under [Disk use](#disk-use-and-the-cube-cache).

## Contents

- [What AstroScope does](#what-astroscope-does)
- [Acknowledgements and relation to the AstroCC notebook](#acknowledgements-and-relation-to-the-astrocc-notebook)
- [Requirements](#requirements)
- [Installation](#installation)
- [API keys](#api-keys)
- [Using AstroScope](#using-astroscope)
- [Things to keep in mind](#things-to-keep-in-mind)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Data and service credits](#data-and-service-credits)

## What AstroScope does

**Target card.** Resolves any SIMBAD or NED identifier, or coordinates in decimal, sexagesimal
or compact IAU form, and shows ICRS and galactic coordinates, redshift and recession velocity
with their quality, morphological type, object type, angular size and position angle,
redshift-independent distances, group and cluster memberships (FCC, VCC, LGG, HCG, Abell and
others, plus groups found within one degree), and aliases. SIMBAD is the primary source, NED the
second opinion and position fallback, CDS Sesame the last resort. When SIMBAD's redshift is of
low quality the NED value is preferred and both are shown.

**Synchronised sky viewer.** Four Aladin Lite v3 panes pan and zoom together. Each pane picks
its own survey (Rubin First Look, DSS2, Pan-STARRS, SDSS, DECaLS, DES, GALEX, 2MASS, WISE,
Finkbeiner Hα, XMM-Newton EPIC, Fermi LAT) and can draw coverage overlays: the all-sky coverage
of HST, JWST, Chandra and XMM-Newton, and the footprints of the MUSE, ALMA, HST and JWST
observations found around the current target.

**Archive observations.** One search queries the ESO (MUSE, UVES, X-Shooter), MAST (HST, JWST)
and ALMA ObsCore services in parallel and lists every public observation within the chosen
radius, grouped by telescope and instrument, with date, exposure time, band, resolution,
separation, release status and links to data, previews and the archive pages. ALMA spectral
windows are merged per observation with their frequency ranges. Each archive reports its own
status, so one slow service does not hide the others.

**HI 21 cm.** Cross-matches the position against ALFALFA, HIPASS (HICAT, NHICAT) and the HIPASS
Bright Galaxy Catalog on VizieR and reports systemic velocity, W50, W20, integrated flux, S/N,
distance and HI mass where available.

**Spectrum.** The Spectrum tab lists the cubes of the current search by source. A MUSE cube is
cut out server-side by ESO's SODA service around a circular aperture at the target and
integrated into a 1-D spectrum with its error. An ALMA observation is opened to its
spectral-window products, the window that holds the chosen line at the target's velocity is
marked, and an aperture cutout spanning the line is fetched from ALMA's SODA service; only when
no ALMA mirror serves a cutout can the full product be downloaded, with a progress bar. Any
number of traces are overplotted: in wavelength, or on a common velocity axis about Hα (optical
convention) and CO(2–1) or another mm line (radio convention), relative to the systemic
velocity, with independently normalised amplitudes for shape comparison. Redshifted optical and
mm line markers, a catalogue-derived HI double-horn template or an uploaded HI profile join the
same axis. Every plotted item can be hidden or removed.

**Literature.** With a NASA ADS token, finds the papers on the target and its aliases and
computes papers per year, facilities used (ADS facility field plus about seventy telescope and
instrument patterns), keyword frequencies, citation distribution, total citations and h-index.
Facility chips isolate, say, the ALMA or MUSE papers in the histogram, and the table links to ADS.

**Synthesis.** With a Google Gemini key, sends the abstracts of the most cited and most recent
papers plus the statistics to Gemini and returns a structured briefing: summary, established
properties, peculiarities and open questions, key papers with reasons, keyword clusters,
facilities and their role, and the publication trend. Citations the model invents are flagged.

## Acknowledgements and relation to the AstroCC notebook

AstroScope grew out of the ESO Archive tutorial notebook *"ESO and ALMA Archives: A Science Case
Using VO Services"* by Ashley Barnes and Juan M. Carmona Loaiza (ESO Archive Science Group),
presented at the **Astro-CC Scientific Training event** at the Observatoire astronomique de
Strasbourg in October 2026. That notebook demonstrated the workflow this application
generalises: resolve a galaxy, find its PHANGS MUSE and ALMA data, request **ESO SODA** and
**ALMA SODA** cutouts around one region, and compare MUSE Hα with ALMA CO(2–1) on a common
velocity axis. The SODA request forms AstroScope sends to ESO (`dataportal.eso.org/dataPortal/
soda/sync`) and ALMA (`almascience.eso.org/soda/sync`, `REQUEST=queryData` with `POS=CIRCLE` and
`BAND`) follow the notebook's helpers, and the ALMA product selection, which matches the window
by frequency and the ARI-L file name, follows its `select_alma_science_cube`.

What AstroScope adds to that workflow:

- Any target, not one hard-coded galaxy: identifiers or coordinates, with SIMBAD, NED and Sesame
  fallbacks and a quality-aware choice of redshift.
- A four-pane synchronised Aladin viewer with survey and coverage overlays, instead of static
  linked panels.
- Archive discovery across ESO, MAST and ALMA at once through ObsCore, grouped by telescope and
  instrument, with footprints drawn on the sky, plus HI catalogue cross-matching.
- Automatic selection of the ALMA spectral window holding the chosen line, with mask, primary
  beam and calibrator cubes recognised and kept out of the way, and tar packages expanded.
- Aperture spectra from any number of MUSE and ALMA cubes, with error propagation from the MUSE
  STAT extension, both velocity conventions, redshifted line markers, HI templates and uploads,
  and interactive Plotly plots.
- Bibliometrics and an AI synthesis of the literature.
- A full-download fallback with progress reporting, memory-mapped extraction and a managed disk
  cache for the rare products without a SODA cutout.

Things the notebook does that AstroScope does not yet: band-only MUSE cutouts collapsed into Hα
and continuum maps, the PHANGS nebula catalogue and its segmentation mask to pick an HII region
as the aperture, and Splatalogue look-ups for rest frequencies (AstroScope ships a fixed list of
common mm lines).

## Requirements

- **Python 3.11 or newer.** The backend uses FastAPI, Astropy, Astroquery, PyVO, mocpy, NumPy
  and Pandas; they are installed into a virtual environment by the setup step.
- **Node.js 18 or newer** with npm, for the frontend (React, Vite, Aladin Lite, Plotly).
- **make** and **git**. On Windows use WSL, or run the commands listed under
  [Without make](#without-make) in PowerShell.
- **Internet access** to CDS (SIMBAD, VizieR, Aladin HiPS, MOCServer), NED, ESO, ALMA, MAST and,
  optionally, ADS and Google. All archive services are used anonymously; no account is needed.
- Any modern browser. The viewer uses WebGL.

The machine does not need to be powerful: spectra are cut out server-side at the archives and
arrive as a few megabytes.

## Installation

### 1. Get the code

```bash
git clone https://github.com/sardarkagrandson/know-your-object.git
cd know-your-object
```

### 2. Provide Python 3.11+ and Node 18+

If `python3 --version` and `node --version` already satisfy the requirements, skip ahead. With
conda, the simplest route is one environment holding both:

```bash
conda create -n astroscope python=3.12 nodejs=22 -c conda-forge
conda activate astroscope
```

### 3. Install the dependencies

```bash
make setup
```

This creates `.venv` with the backend and its development tools, and runs `npm install` in
`frontend/`. The Makefile picks the first Python 3.11+ interpreter on your PATH; to name one
explicitly use `make setup PY=python3.12`. If `.venv` already exists but was created with an
older Python, it is replaced.

### 4. Optional: a `.env` file

```bash
cp .env.example .env
```

Everything runs without it. Edit it to set server-side API keys (see [API keys](#api-keys)),
change ports, timeouts or the cube cache location. The backend reads it on start-up.

### 5. Run

In one terminal:

```bash
make backend        # FastAPI on http://localhost:8000, interactive API docs at /docs
```

In another:

```bash
make frontend       # Vite dev server on http://localhost:5173, proxies /api to the backend
```

Open http://localhost:5173 and resolve `NGC 1365`, `M 87`, `03h33m36.4s -36d08m25s` or
`187.706, 12.391`.

### Updating

```bash
git pull
make setup          # only needed when dependencies changed; harmless otherwise
```

then restart both servers. The frontend dev server reloads code changes by itself; the backend
restarts itself on code changes but not on `.env` changes.

### Without make

```bash
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e "backend[dev]"
cd frontend && npm install && cd ..
# run (two terminals)
cd backend && ../.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
cd frontend && npm run dev
```

On Windows replace `.venv/bin/` with `.venv\Scripts\`.

### Production build

`make build` writes a static frontend into `frontend/dist`. Serve it with any web server and
point it at the backend with `VITE_API_BASE_URL` at build time, and list the frontend's origin in
`ASTROSCOPE_CORS_ORIGINS` on the backend.

## API keys

The target card, viewer, archives, HI and spectrum features need no keys. Two features do:

| Feature | Key | Where to get it |
| --- | --- | --- |
| Literature | NASA ADS API token | https://ui.adsabs.harvard.edu/user/settings/token (free, with a daily query quota) |
| Synthesis | Google Gemini API key | https://aistudio.google.com/apikey (free tier) |

There are two ways to provide them:

- **In the browser.** Press **keys** in the page header and paste them. They are kept in your
  browser's local storage and sent with each request as headers; the server never stores them.
  This is how a shared or public instance should run: every user brings their own keys and uses
  their own quota.
- **On the server.** Set `ADS_DEV_KEY` and `GEMINI_API_KEY` in `.env` as defaults for every user
  of that instance. A key pasted in the browser overrides the server default.

The Gemini model is chosen automatically (`GEMINI_MODEL=auto` picks the newest stable Flash model
the key can use); the keys dialog lists the models available to your key if you want another.

## Using AstroScope

1. **Resolve a target** in the search box. The card on the left fills with its properties and
   the four panes centre on it at a field of view suited to its size. "Recentre on target" and
   "Set FOV" bring the view back after you have wandered.
2. **Pick surveys** per pane from the drop-down above each one. The **MOC** menu of a pane
   draws coverage overlays; the "footprints around the target" entries appear once the archive
   search has run.
3. **Archive observations** runs automatically for the resolved target with the radius shown at
   the right of the panel (change it and press Search). Expand telescopes and instruments, sort
   by date, exposure or separation, filter by instrument, and use the links. "spectrum" on a
   MUSE or ALMA cube row jumps to the Spectrum tab with that cube selected.
4. **HI 21 cm** lists the catalogue detections near the target.
5. **Spectrum.** The left column groups what can be plotted:
   - **ALMA cubes**: open an observation to see its spectral-window products. "line" marks the
     window holding the chosen line at the target's velocity (change the line in the group
     header); "other" windows can still be extracted. Auxiliary (mask, primary beam, psf) and
     calibrator cubes sit behind a link. Press **extract**.
   - **MUSE cubes**: press **extract**. If the target lies outside a cube's footprint the panel
     says so and offers extraction at the cube centre.
   - **HI 21 cm**: **template** draws a double-horn profile from the catalogue values;
     **upload HI spectrum** takes your own two-column (velocity km/s, flux) text file.
   - **Plotted** lists every trace with a checkbox to hide it and a cross to remove it; the same
     controls repeat under the plot.
   The aperture radius and sum-or-mean choice sit in the header. The row of controls switches
   between wavelength and velocity axes, sets the reference lines, the systemic velocity (from
   the resolver, an HI catalogue, or typed in), the velocity window and the amplitude
   normalisation, and toggles line markers.
6. **Literature** needs an ADS token. The chips above the histogram isolate facilities; the
   table lists the papers with ADS links.
7. **Synthesise**, in the Literature tab, needs a Gemini key and uses the papers already found.

## Things to keep in mind

- **Search radius and speed.** ESO and ALMA answer in seconds; MAST can take one to two minutes
  for well-studied galaxies and its chip shows "querying…" meanwhile. A 5′ radius is a good
  start; large radii around famous targets return hundreds of HST rows.
- **Systemic velocity.** Line markers, "contains line" and the velocity axis depend on cz. The
  control row shows where it came from (resolver, HI catalogue, manual). SIMBAD sometimes carries
  a low-quality redshift; the card shows NED's value next to it, and you can type any cz.
- **SODA cutouts and the band.** An ALMA cutout spans the chosen line ± twice the velocity
  window (at least ±2000 km/s). If you later widen the window beyond the cutout, extract again.
  The footnote under the plot says whether a trace came from a SODA cutout or a full download,
  and which band.
- **Normalised amplitudes compare shapes only.** Fluxes, beam sizes, pixel scales and
  calibrations differ between MUSE, ALMA and HI; the panel says so when normalisation is on.
  Optical traces use the optical Doppler convention about the chosen optical line, radio traces
  the radio convention about the mm line.
- **The HI template is synthetic.** It is a symmetric double-horn profile built from the
  catalogue's Vsys, W50, W20 and integrated flux, labelled as such. Upload an observed profile
  for real comparisons.
- **Apertures on bright nuclei.** An ALMA spectrum that dips below zero at the systemic velocity
  in a small aperture on a galaxy nucleus is usually the interferometric negative bowl or real
  absorption against the continuum, not an extraction fault. Try "mean per spaxel" or an offset
  position.
- **Interferometric cubes are large.** When no ALMA mirror serves a cutout, products above
  500 MB are not downloaded without your say-so: the error names each mirror's reply and offers
  the full download as a button.
- **Keys in the browser** stay on that machine and browser profile. Clearing site data removes
  them.

### Disk use and the cube cache

MUSE cutouts, ALMA cutouts and all spectra are held in memory only. The one thing AstroScope
writes to disk is an ALMA product downloaded in full when no SODA cutout was available. Those
files go to `astroscope-cubes` under your system temp directory (or `ASTROSCOPE_CUBE_CACHE_DIR`),
are capped at `ASTROSCOPE_CUBE_CACHE_GB` (default 4 GB, oldest deleted first), and are listed in
the Spectrum tab header with a **clear cache** button. Gzipped products keep only the unpacked
copy.

## Configuration

All settings live in `.env` at the repository root (see `.env.example`). The main ones:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ADS_DEV_KEY` | empty | Server-side ADS token |
| `GEMINI_API_KEY` | empty | Server-side Gemini key |
| `GEMINI_MODEL` | `auto` | Gemini model, or `auto` for the newest stable Flash |
| `ASTROSCOPE_PORT` | `8000` | Backend port |
| `ASTROSCOPE_CORS_ORIGINS` | the Vite dev origins | Origins allowed to call the API |
| `ASTROSCOPE_REMOTE_TIMEOUT` | `45` | Seconds allowed per remote call (MAST gets more) |
| `ASTROSCOPE_CACHE_TTL` | `3600` | Seconds to keep resolver and archive answers |
| `ASTROSCOPE_SIMBAD_TAP_URL` | CDS | SIMBAD TAP endpoint (a mirror can be set) |
| `ASTROSCOPE_MAST_TAP_URL` | auto | Force a MAST TAP endpoint |
| `ASTROSCOPE_CUBE_CACHE_DIR` | system temp | Where full ALMA downloads are kept |
| `ASTROSCOPE_CUBE_CACHE_GB` | `4` | Cache size before the oldest files are deleted |
| `ASTROSCOPE_CUBE_MAX_MB` | `600` | Default download cap; the app passes the product's own size |
| `VITE_API_BASE_URL` | empty | Backend URL for a production frontend build |

The surveys and overlays offered in the viewer are listed in `backend/app/services/surveys.py`.

## Troubleshooting

**`make setup` complains about Python, or installs into the wrong one.** It needs 3.11+. Create
one (`conda create -n astroscope python=3.12`) and run `make setup PY=python3.12`, or activate
the environment first. A `.venv` built with an old Python is replaced automatically.

**`npm: command not found`.** Install Node 18+ (`conda install -c conda-forge nodejs=22`, or from
nodejs.org) and rerun `make setup`.

**The page loads but nothing resolves, or "catalog" errors appear.** The backend is not running
or not on port 8000. Start `make backend` and check http://localhost:8000/api/health, which also
reports whether server-side keys are set.

**Port already in use.** Change `ASTROSCOPE_PORT` in `.env` and start the frontend with
`ASTROSCOPE_API_URL=http://localhost:<port> make frontend`, or stop the other process.

**An overlay shows "failed" in a pane's MOC menu.** Open http://localhost:8000/api/overlays: each
overlay lists the CDS MOCServer records it resolved to. Coverage is fetched from CDS and cached
for a day, so a transient CDS outage clears itself. The MUSE and ALMA footprint overlays need
the archive search to have run first.

**An archive reports an error or times out.** The chip on the Archive tab names the archive and
the message. MAST is slow for famous targets; raise `ASTROSCOPE_REMOTE_TIMEOUT` if it times out
regularly. `backend/scripts/archive_diag.py` runs the three queries from the command line and
prints what each service returns.

**"SODA returned no data (HTTP 204)".** The aperture lies outside that cube's footprint or, for
ALMA, outside its spectral range. Extract at the cube centre, choose a cube marked "covers
target", or pick the window marked "line".

**"ALMA SODA cutout failed" with HTTP codes per mirror.** ALMA refused the cutout for that
product. Run the command printed under the error, `backend/scripts/alma_soda_diag.py`, to see
each mirror's reply, or press the button to download the product in full.

**An ALMA observation lists no science cube.** Some observations only expose mask, primary-beam
or calibrator cubes, or tar packages; the panel says which. Those cubes can still be extracted
but are not the target's flux.

**The literature tab says 401 or 429.** 401: the ADS token is wrong or expired, paste a new one in
the keys dialog. 429: the daily ADS quota of that token is used up.

**Synthesis says the model is unavailable.** Gemini retires model names from time to time.
Leave the model on `auto`, or pick one from the list in the keys dialog.

**ESO answers 502 or 503 on a MUSE cutout.** ESO's data portal does this when a cutout job times
out behind its proxy; AstroScope retries a few times. Try again in a minute, or a smaller
aperture.

**Plots look empty or overlap after resizing.** Switch tabs once; Plotly re-measures the panel.

Diagnostic scripts in `backend/scripts/` (run them with `.venv/bin/python`): `archive_diag.py`
(ObsCore queries), `hi_diag.py` (VizieR HI catalogues), `moc_diag.py` (coverage records),
`soda_diag.py` (an ESO DataLink document and its SODA request), `alma_soda_diag.py` (ALMA SODA
replies per mirror).

## Development

```
backend/app/        FastAPI application: routers (/api/...), services (one per remote
                    service), schemas (Pydantic models), config
backend/tests/      pytest suite; remote services are faked, no network needed
backend/scripts/    diagnostic scripts
frontend/src/       React + TypeScript + Tailwind; lib/ holds the API client, key storage
                    and the Aladin view synchroniser; components/ the panels
```

- `make test` runs the backend (pytest) and frontend (vitest) suites; `make lint` runs ruff and
  the TypeScript compiler.
- The backend serves interactive API documentation at http://localhost:8000/docs.
- Aladin Lite v3 has no built-in view linking; `frontend/src/lib/viewSync.ts` implements it on
  the public callbacks with echo suppression.

## Data and service credits

AstroScope only works because these services exist and are open:

- **CDS, Strasbourg**: SIMBAD, VizieR, Sesame, the MOCServer, HiPS surveys and Aladin Lite.
- **NASA/IPAC Extragalactic Database (NED)**.
- **ESO Science Archive**: the ObsCore TAP service, DataLink and the **SODA** cutout service for
  MUSE cubes, and the AstroCC tutorial that showed how to use them.
- **ALMA Science Archive** and its mirrors at ESO, NRAO and NAOJ: ObsCore, DataLink and the
  **SODA** cutout service; ARI-L products.
- **MAST** (STScI): the CAOM TAP service for HST and JWST.
- **NASA ADS** for the literature, **Google Gemini** for the synthesis.
- ALFALFA, HIPASS and the HIPASS Bright Galaxy Catalog teams for the HI catalogues.

Please acknowledge these services in any work that uses what AstroScope shows you, following
each service's own acknowledgement guidelines.
