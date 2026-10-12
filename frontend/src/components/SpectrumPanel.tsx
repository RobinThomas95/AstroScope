import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Plotly from "plotly.js-basic-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { Annotation as PlotlyAnnotation, Data, Layout, Shape } from "plotly.js";
import {
  ApiError,
  fetchCubeProducts,
  fetchCubeSpectrum,
  fetchHIProfile,
  fetchLines,
  fetchMuseSpectrum,
  fetchRadioLines,
} from "../lib/api";
import type {
  CubeProduct,
  HIProfile,
  RadioLine,
  ResolvedTarget,
  SpectralLine,
  Spectrum1D,
  SpectrumRequest,
} from "../types";

const Plot = createPlotlyComponent(Plotly);
const C_KMS = 299792.458;
const COLORS = ["#7dd3fc", "#fb923c", "#a3e635", "#f472b6", "#facc15", "#c084fc"];

interface Props {
  target: ResolvedTarget | null;
  request: SpectrumRequest | null;
  /** Plots are only rendered while the tab is visible. */
  active: boolean;
  /** Velocity from an HI catalogue detection, used when the resolver has no redshift. */
  hiVelocityKms: number | null;
}

interface Trace {
  id: string;
  label: string;
  kind: "optical" | "radio";
  spectrum: Spectrum1D;
  color: string;
  position: "target" | "cube";
}

interface UploadedHI {
  name: string;
  velocity: number[];
  flux: number[];
}

type XMode = "wavelength" | "velocity";

/** Parse a two-column ASCII HI spectrum (velocity km/s, flux). Lines starting with # are skipped. */
export function parseHIText(text: string, name: string): UploadedHI {
  const velocity: number[] = [];
  const flux: number[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || line.startsWith("!")) continue;
    const parts = line.split(/[\s,;]+/).map(Number);
    if (parts.length < 2 || !Number.isFinite(parts[0]) || !Number.isFinite(parts[1])) continue;
    velocity.push(parts[0]);
    flux.push(parts[1]);
  }
  if (velocity.length < 3) throw new Error("Could not find two numeric columns (velocity, flux)");
  return { name, velocity, flux };
}

/** Scale a series so its largest |value| inside the window becomes 1. */
function normalise(x: number[], y: (number | null)[], lo: number, hi: number): (number | null)[] {
  let peak = 0;
  for (let i = 0; i < x.length; i++) {
    const v = y[i];
    if (v !== null && x[i] >= lo && x[i] <= hi && Math.abs(v) > peak) peak = Math.abs(v);
  }
  if (!peak) for (const v of y) if (v !== null && Math.abs(v) > peak) peak = Math.abs(v);
  return peak ? y.map((v) => (v === null ? null : v / peak)) : y;
}

function productLabel(base: string, p: CubeProduct): string {
  const win = p.freq_center_ghz ? `${p.freq_center_ghz.toFixed(2)} GHz` : `spw${p.spw ?? "?"}`;
  return `${base} ${win}`;
}

const PRODUCT_KIND_LABEL: Record<string, string> = {
  cube: "Science cubes",
  auxiliary: "Auxiliary cubes (mask, primary beam, psf, residual…)",
  calibrator: "Calibrator cubes (check source, phase, bandpass…)",
};

function ProductList({ products, request, onExtract }: { products: CubeProduct[]; request: SpectrumRequest; onExtract: (p: CubeProduct) => void }) {
  const [showAll, setShowAll] = useState(false);
  const science = products.filter((p) => p.kind === "cube");
  const auxiliary = products.filter((p) => p.kind === "auxiliary");
  const calibrator = products.filter((p) => p.kind === "calibrator");
  const packages = products.filter((p) => p.kind === "package");
  const others = products.length - science.length - auxiliary.length - calibrator.length - packages.length;
  const groups: [string, CubeProduct[]][] = [["cube", science]];
  if (showAll || science.length === 0) groups.push(["auxiliary", auxiliary], ["calibrator", calibrator]);
  const hidden = auxiliary.length + calibrator.length;
  return (
    <div className="space-y-1">
      {science.length === 0 && (
        <p className="text-amber-300">
          No science cube (pbcor/image) is listed for {request.label}.
          {hidden > 0 && ` ${hidden} auxiliary or calibrator cube${hidden === 1 ? "" : "s"} listed below can still be extracted but are not the target's flux.`}
          {packages.length > 0 && ` ${packages.length} packaged tar product${packages.length === 1 ? "" : "s"} listed (not extractable here; download from the ALMA archive).`}
          {others > 0 && ` ${others} other FITS file${others === 1 ? "" : "s"}.`}
        </p>
      )}
      {groups.map(([kind, list]) =>
        list.length === 0 ? null : (
          <div key={kind}>
            <p className="text-[11px] uppercase tracking-wide text-slate-500">{PRODUCT_KIND_LABEL[kind]}</p>
            <ul className="space-y-0.5">
              {list.slice(0, 24).map((p) => (
                <li key={p.url} className="flex flex-wrap items-center gap-2">
                  <span className={`rounded px-1 ${p.contains_line ? "bg-emerald-900/50 text-emerald-300" : "bg-slate-800 text-slate-400"}`} title={p.freq_approx ? "window estimated from the centre frequency in the file name" : undefined}>
                    {p.contains_line ? "contains line" : p.contains_line === false ? "other window" : "window ?"}
                    {p.freq_approx && p.contains_line !== null ? " ~" : ""}
                  </span>
                  <span className="font-mono text-[11px] text-slate-200">{p.name}</span>
                  <span className="text-slate-500">
                    spw {p.spw ?? "?"}
                    {p.freq_center_ghz ? ` · ${p.freq_center_ghz.toFixed(3)} GHz` : ""}
                    {p.freq_min_ghz ? ` · ${p.freq_min_ghz.toFixed(2)}–${p.freq_max_ghz?.toFixed(2)} GHz${p.freq_approx ? " (est.)" : ""}` : ""} · {p.size_mb ?? "?"} MB
                  </span>
                  <button type="button" onClick={() => onExtract(p)} className="rounded border border-emerald-700 px-1.5 py-0.5 text-emerald-300 hover:bg-emerald-900/40">
                    extract
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ),
      )}
      {science.length > 0 && hidden > 0 && (
        <button type="button" onClick={() => setShowAll((v) => !v)} className="text-slate-400 underline hover:text-slate-200">
          {showAll ? "hide" : "show"} {hidden} auxiliary/calibrator cube{hidden === 1 ? "" : "s"}
        </button>
      )}
    </div>
  );
}

export default function SpectrumPanel({ target, request, active, hiVelocityKms }: Props) {
  const [radius, setRadius] = useState(3);
  const [radiusInput, setRadiusInput] = useState("3");
  const [method, setMethod] = useState<"sum" | "mean">("sum");
  const [position, setPosition] = useState<"target" | "cube">("target");
  const [traces, setTraces] = useState<Trace[]>([]);
  const [lines, setLines] = useState<SpectralLine[]>([]);
  const [radioLines, setRadioLines] = useState<RadioLine[]>([]);
  const [showLines, setShowLines] = useState(true);
  const [lineKinds, setLineKinds] = useState<"all" | "emission" | "absorption">("all");
  const [xMode, setXMode] = useState<XMode>("wavelength");
  const [refLine, setRefLine] = useState("Hα");
  const [refRadio, setRefRadio] = useState("CO(2-1)");
  const [relative, setRelative] = useState(true);
  const [normalised, setNormalised] = useState(false);
  const [window, setWindow] = useState(1500);
  const [showHI, setShowHI] = useState(false);
  const [hiProfile, setHIProfile] = useState<HIProfile | null>(null);
  const [hiError, setHIError] = useState<string | null>(null);
  const [uploaded, setUploaded] = useState<UploadedHI | null>(null);
  const [products, setProducts] = useState<CubeProduct[] | null>(null);
  const [productsFor, setProductsFor] = useState<SpectrumRequest | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [manualCz, setManualCz] = useState("");
  const manual = parseFloat(manualCz);
  const czSource = Number.isFinite(manual)
    ? "manual"
    : target?.kinematics.velocity_kms !== null && target?.kinematics.velocity_kms !== undefined
      ? "resolver"
      : hiVelocityKms !== null
        ? "HI catalogue"
        : null;
  const cz =
    czSource === "manual"
      ? manual
      : czSource === "resolver"
        ? (target!.kinematics.velocity_kms as number)
        : czSource === "HI catalogue"
          ? hiVelocityKms
          : null;
  const z = cz !== null ? cz / C_KMS : null;

  // Line lists (optical: windowed to MUSE; radio: all, observed frequencies at z).
  useEffect(() => {
    const ac = new AbortController();
    fetchLines(z, 3000, 25000, ac.signal).then((l) => !ac.signal.aborted && setLines(l)).catch(() => undefined);
    fetchRadioLines(z, undefined, undefined, ac.signal).then((l) => !ac.signal.aborted && setRadioLines(l)).catch(() => undefined);
    return () => ac.abort();
  }, [z]);

  // Reset everything when the target changes.
  useEffect(() => {
    setTraces([]);
    setProducts(null);
    setProductsFor(null);
    setHIProfile(null);
    setUploaded(null);
    setError(null);
  }, [target]);

  const reference = useMemo(
    () => lines.find((l) => l.name === refLine) ?? lines.find((l) => l.name === "Hα") ?? null,
    [lines, refLine],
  );
  const referenceRadio = useMemo(
    () => radioLines.find((l) => l.name === refRadio) ?? radioLines.find((l) => l.name === "CO(2-1)") ?? null,
    [radioLines, refRadio],
  );

  const extractPosition = useCallback(
    (req: SpectrumRequest, pos: "target" | "cube"): { ra: number; dec: number } | null => {
      if (pos === "cube" && req.cubeRa !== null && req.cubeDec !== null) return { ra: req.cubeRa, dec: req.cubeDec };
      if (!target) return null;
      return { ra: target.coordinates.ra_deg, dec: target.coordinates.dec_deg };
    },
    [target],
  );

  const addTrace = useCallback((t: Trace) => {
    setTraces((prev) => {
      const others = prev.filter((x) => x.id !== t.id);
      const color = COLORS[others.length % COLORS.length];
      return [...others, { ...t, color }];
    });
  }, []);

  const extractMuse = useCallback(
    async (req: SpectrumRequest, pos: "target" | "cube") => {
      const at = extractPosition(req, pos);
      if (!at) return;
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      setLoading(`Requesting a SODA cutout of ${req.label}…`);
      setError(null);
      try {
        const spectrum = await fetchMuseSpectrum(
          { datalinkUrl: req.datalinkUrl, ra: at.ra, dec: at.dec, radiusArcsec: radius, method },
          ac.signal,
        );
        if (!ac.signal.aborted) addTrace({ id: req.datalinkUrl, label: req.label, kind: "optical", spectrum, color: "", position: pos });
      } catch (e: unknown) {
        if (!ac.signal.aborted) setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        if (!ac.signal.aborted) setLoading(null);
      }
    },
    [extractPosition, radius, method, addTrace],
  );

  const listAlmaProducts = useCallback(
    async (req: SpectrumRequest) => {
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      setLoading(`Listing the products of ${req.label}…`);
      setError(null);
      try {
        const lineGhz = referenceRadio?.observed_frequency_ghz ?? null;
        const list = await fetchCubeProducts(req.datalinkUrl, req.spwRanges, lineGhz, ac.signal);
        if (!ac.signal.aborted) {
          setProducts(list);
          setProductsFor(req);
        }
      } catch (e: unknown) {
        if (!ac.signal.aborted) setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        if (!ac.signal.aborted) setLoading(null);
      }
    },
    [referenceRadio],
  );

  const extractAlma = useCallback(
    async (req: SpectrumRequest, product: CubeProduct, pos: "target" | "cube") => {
      const at = extractPosition(req, pos);
      if (!at) return;
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      setLoading(`Downloading ${product.name} (${product.size_mb ?? "?"} MB) and extracting… this can take a few minutes`);
      setError(null);
      try {
        const spectrum = await fetchCubeSpectrum(
          { productUrl: product.url, ra: at.ra, dec: at.dec, radiusArcsec: radius, method, maxMb: product.size_mb ? Math.ceil(product.size_mb * 1.2) : undefined },
          ac.signal,
        );
        if (!ac.signal.aborted) {
          addTrace({ id: product.url, label: productLabel(req.label, product), kind: "radio", spectrum, color: "", position: pos });
          setXMode("velocity");
          setNormalised(true);
        }
      } catch (e: unknown) {
        if (!ac.signal.aborted) setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        if (!ac.signal.aborted) setLoading(null);
      }
    },
    [extractPosition, radius, method, addTrace],
  );

  // React to a new request from the observation table.
  useEffect(() => {
    if (!request || !target) return;
    const pos: "target" | "cube" = request.coversTarget === false && request.cubeRa !== null ? "cube" : "target";
    setPosition(pos);
    if (request.kind === "muse") void extractMuse(request, pos);
    else void listAlmaProducts(request);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, target]);

  // HI template on demand.
  useEffect(() => {
    if (!showHI || !target || hiProfile) return;
    const ac = new AbortController();
    setHIError(null);
    fetchHIProfile(target.coordinates.ra_deg, target.coordinates.dec_deg, undefined, ac.signal)
      .then((p) => !ac.signal.aborted && setHIProfile(p))
      .catch((e: unknown) => {
        if (ac.signal.aborted) return;
        setHIError(e instanceof ApiError ? e.message : String(e));
      });
    return () => ac.abort();
  }, [showHI, target, hiProfile]);

  const velocityOf = useCallback(
    (t: Trace, i: number): number | null => {
      if (t.kind === "optical") {
        if (!reference) return null;
        const v = C_KMS * (t.spectrum.wavelength_A[i] / reference.rest_wavelength_A - 1);
        return relative && cz !== null ? v - cz : v;
      }
      const f = t.spectrum.frequency_ghz?.[i];
      if (!referenceRadio || f === null || f === undefined) return null;
      const v = C_KMS * (1 - f / referenceRadio.rest_frequency_ghz); // radio convention
      return relative && cz !== null ? v - cz : v;
    },
    [reference, referenceRadio, relative, cz],
  );

  const figure = useMemo(() => {
    if (!traces.length && !(xMode === "velocity" && showHI)) return null;
    const centre = relative || cz === null ? 0 : cz;
    const lo = centre - window;
    const hi = centre + window;
    const data: Data[] = [];
    const visible = xMode === "wavelength" ? traces.filter((t) => t.kind === "optical") : traces;
    for (const t of visible) {
      const x = xMode === "wavelength" ? t.spectrum.wavelength_A : t.spectrum.wavelength_A.map((_, i) => velocityOf(t, i));
      const xs = x.map((v) => (v === null ? NaN : v));
      let y: (number | null)[] = t.spectrum.flux;
      if (xMode === "velocity" && normalised) y = normalise(xs, y, lo, hi);
      const radioAxis = xMode === "velocity" && t.kind === "radio" && !normalised;
      data.push({
        x: xs,
        y,
        type: "scatter",
        mode: "lines",
        name: `${t.label} (r=${t.spectrum.aperture_radius_arcsec}″${t.position === "cube" ? ", cube centre" : ""})`,
        line: { color: t.color, width: 1 },
        yaxis: radioAxis ? "y2" : "y",
        hovertemplate: xMode === "wavelength" ? "%{x:.1f} Å<br>%{y:.3g}<extra></extra>" : "%{x:.0f} km/s<br>%{y:.3g}<extra></extra>",
      });
      if (t.spectrum.error && !normalised && xMode === "wavelength") {
        const upper = t.spectrum.flux.map((f, i) => (f === null || t.spectrum.error![i] === null ? null : f + (t.spectrum.error![i] as number)));
        const lower = t.spectrum.flux.map((f, i) => (f === null || t.spectrum.error![i] === null ? null : f - (t.spectrum.error![i] as number)));
        data.push(
          { x: xs, y: upper, type: "scatter", mode: "lines", line: { width: 0 }, showlegend: false, hoverinfo: "skip" },
          { x: xs, y: lower, type: "scatter", mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: "rgba(125,211,252,0.15)", name: "±1σ", hoverinfo: "skip" },
        );
      }
    }
    const shapes: Partial<Shape>[] = [];
    const annotations: Partial<PlotlyAnnotation>[] = [];
    if (showLines && xMode === "wavelength") {
      for (const l of lines) {
        if (l.observed_wavelength_A === null) continue;
        if (lineKinds !== "all" && l.kind !== "both" && l.kind !== lineKinds) continue;
        const color = l.kind === "absorption" ? "#f59e0b" : l.kind === "emission" ? "#f472b6" : "#a3e635";
        shapes.push({ type: "line", xref: "x", yref: "paper", x0: l.observed_wavelength_A, x1: l.observed_wavelength_A, y0: 0, y1: 1, line: { color, width: 1, dash: "dot" } });
        annotations.push({ x: l.observed_wavelength_A, y: 1, xref: "x", yref: "paper", text: l.name, showarrow: false, textangle: -90, yanchor: "top", xanchor: "left", font: { size: 9, color } });
      }
    }
    if (xMode === "velocity" && cz !== null) {
      shapes.push({ type: "line", xref: "x", yref: "paper", x0: centre, x1: centre, y0: 0, y1: 1, line: { color: "#fde047", width: 1 } });
    }
    const hiTraces: Data[] = [];
    if (xMode === "velocity" && showHI) {
      const hiX = (v: number) => (relative && cz !== null ? v - cz : v);
      const pushHI = (xv: number[], yv: number[], name: string, color: string, dash?: "dash") => {
        const y = normalised ? normalise(xv, yv, lo, hi) : yv;
        hiTraces.push({ x: xv, y, type: "scatter", mode: "lines", name, yaxis: normalised ? "y" : "y2", line: { color, width: 1.5, dash }, hovertemplate: "%{x:.0f} km/s<br>%{y:.3f}<extra>" + name + "</extra>" });
      };
      if (hiProfile) pushHI(hiProfile.velocity_kms.map(hiX), hiProfile.flux_jy, `HI template (${hiProfile.survey ?? "catalogue"})`, "#fbbf24", "dash");
      if (uploaded) pushHI(uploaded.velocity.map(hiX), uploaded.flux, `HI ${uploaded.name}`, "#f97316");
    }
    const hasY2 = [...data, ...hiTraces].some((d) => (d as { yaxis?: string }).yaxis === "y2");
    const opticalUnit = traces.find((t) => t.kind === "optical")?.spectrum.flux_unit ?? "flux";
    const layout: Partial<Layout> = {
      autosize: true,
      height: 360,
      margin: { l: 60, r: hasY2 ? 60 : 20, t: 30, b: 45 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(2,6,23,0.6)",
      font: { color: "#cbd5e1", size: 11 },
      xaxis: {
        title: {
          text:
            xMode === "wavelength"
              ? "Observed wavelength (Å)"
              : `Velocity (km/s)${relative ? " relative to systemic" : ""} · optical: ${reference?.name ?? "Hα"} · radio: ${referenceRadio?.name ?? "CO(2-1)"}`,
        },
        gridcolor: "#1e293b",
        zeroline: false,
        ...(xMode === "velocity" ? { range: [lo, hi] } : {}),
      },
      yaxis: { title: { text: normalised && xMode === "velocity" ? "normalised amplitude" : opticalUnit }, gridcolor: "#1e293b", zeroline: false },
      legend: { orientation: "h", y: -0.22, font: { size: 10 } },
      showlegend: true,
      shapes,
      annotations,
      hovermode: "x unified",
    };
    if (hasY2) {
      const radioUnit = traces.find((t) => t.kind === "radio")?.spectrum.flux_unit ?? "Jy";
      layout.yaxis2 = { title: { text: radioUnit }, overlaying: "y", side: "right", showgrid: false, zeroline: false };
    }
    return { data: [...data, ...hiTraces], layout };
  }, [traces, lines, showLines, lineKinds, xMode, reference, referenceRadio, relative, cz, showHI, hiProfile, uploaded, normalised, window, velocityOf]);

  const onUpload = async (file: File | undefined) => {
    if (!file) return;
    try {
      setUploaded(parseHIText(await file.text(), file.name));
      setShowHI(true);
      setXMode("velocity");
    } catch (e: unknown) {
      setHIError(e instanceof Error ? e.message : String(e));
    }
  };

  const applyRadius = () => {
    const r = parseFloat(radiusInput);
    if (Number.isFinite(r) && r > 0 && r <= 30) setRadius(r);
  };

  if (!request && !traces.length) {
    return (
      <p className="px-3 py-3 text-xs text-slate-500">
        Press “spectrum” on a public MUSE cube or an ALMA cube in the observation list. MUSE
        spectra come from an ESO SODA cutout; ALMA spectra from the spectral-window cube product
        that contains the chosen line. Traces can be overplotted on a common velocity axis with
        independently normalised amplitudes.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        {request && (
          <>
            <span className="font-medium text-slate-100">{request.label}</span>
            <select value={position} onChange={(e) => setPosition(e.target.value as "target" | "cube")} className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200" aria-label="Extraction position">
              <option value="target">at target{request.coversTarget === false ? " (outside footprint!)" : ""}</option>
              <option value="cube" disabled={request.cubeRa === null}>
                at cube centre{request.cubeRa !== null ? ` (${request.cubeRa.toFixed(4)}, ${request.cubeDec?.toFixed(4)})` : ""}
              </option>
            </select>
            {request.kind === "muse" && (
              <button type="button" onClick={() => void extractMuse(request, position)} className="rounded border border-emerald-700 px-2 py-0.5 text-emerald-300 hover:bg-emerald-900/40">
                extract
              </button>
            )}
            {request.kind === "alma" && (
              <button type="button" onClick={() => void listAlmaProducts(request)} className="rounded border border-emerald-700 px-2 py-0.5 text-emerald-300 hover:bg-emerald-900/40">
                list products
              </button>
            )}
          </>
        )}
        <label className="flex items-center gap-1 text-slate-400">
          aperture r
          <input type="number" min="0.2" max="30" step="0.5" value={radiusInput} onChange={(e) => setRadiusInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && applyRadius()} className="w-14 rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-100" aria-label="Aperture radius in arcseconds" />
          ″
          <button type="button" onClick={applyRadius} className="rounded border border-slate-700 px-1.5 py-0.5 text-slate-200 hover:bg-slate-800">
            apply
          </button>
        </label>
        <select value={method} onChange={(e) => setMethod(e.target.value as "sum" | "mean")} className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200" aria-label="Aperture combination">
          <option value="sum">sum over aperture</option>
          <option value="mean">mean per spaxel</option>
        </select>
      </div>

      {products && productsFor && (
        <div className="rounded border border-slate-800 bg-slate-950/60 p-2">
          <p className="mb-1 text-slate-300">
            Cube products of {productsFor.label}. Line: {referenceRadio?.name ?? "CO(2-1)"}
            {referenceRadio?.observed_frequency_ghz ? ` at ${referenceRadio.observed_frequency_ghz.toFixed(3)} GHz observed` : ""}.
            <select value={refRadio} onChange={(e) => setRefRadio(e.target.value)} className="ml-2 rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200" aria-label="Radio line">
              {radioLines.map((l) => (
                <option key={l.name} value={l.name}>
                  {l.name}
                </option>
              ))}
            </select>
          </p>
          {cz === null && <p className="text-amber-300">Systemic velocity unknown (resolver and HI catalogues gave none): enter cz below to mark the window containing the line.</p>}
          <ProductList products={products} request={productsFor} onExtract={(p) => void extractAlma(productsFor, p, position)} />
        </div>
      )}

      {traces.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-slate-500">traces:</span>
          {traces.map((t) => (
            <span key={t.id} className="flex items-center gap-1 rounded border border-slate-700 px-1.5 py-0.5">
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: t.color }} />
              <span className="text-slate-200">{t.label}</span>
              <span className="text-slate-500">{t.kind}</span>
              <button type="button" onClick={() => setTraces((prev) => prev.filter((x) => x.id !== t.id))} className="text-slate-500 hover:text-rose-300" aria-label={`Remove ${t.label}`}>
                ✕
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-1 text-slate-300">
          <input type="radio" name="xmode" checked={xMode === "wavelength"} onChange={() => setXMode("wavelength")} /> wavelength
        </label>
        <label className="flex items-center gap-1 text-slate-300">
          <input type="radio" name="xmode" checked={xMode === "velocity"} onChange={() => setXMode("velocity")} /> velocity
        </label>
        <span className="text-slate-500">optical line</span>
        <select value={refLine} onChange={(e) => setRefLine(e.target.value)} disabled={xMode !== "velocity"} className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200 disabled:opacity-50" aria-label="Reference line">
          {[...new Map(lines.map((l) => [l.name, l])).keys()].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
          {!lines.length && <option value="Hα">Hα</option>}
        </select>
        <span className="text-slate-500">radio line</span>
        <select value={refRadio} onChange={(e) => setRefRadio(e.target.value)} disabled={xMode !== "velocity"} className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200 disabled:opacity-50" aria-label="Radio reference line">
          {radioLines.map((l) => (
            <option key={l.name} value={l.name}>
              {l.name}
            </option>
          ))}
          {!radioLines.length && <option value="CO(2-1)">CO(2-1)</option>}
        </select>
        <label className="flex items-center gap-1 text-slate-300">
          <input type="checkbox" checked={relative} onChange={(e) => setRelative(e.target.checked)} disabled={xMode !== "velocity" || cz === null} /> relative to cz
        </label>
        <label className="flex items-center gap-1 text-slate-400" title="Systemic velocity used for the redshift of lines and the relative velocity axis">
          cz
          <input type="number" step="1" value={manualCz} onChange={(e) => setManualCz(e.target.value)} placeholder={cz !== null ? cz.toFixed(0) : "unknown"} className="w-20 rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-100" aria-label="Systemic velocity in km/s" />
          km/s{czSource && czSource !== "manual" ? <span className="text-slate-600">({czSource})</span> : null}
        </label>
        <label className="flex items-center gap-1 text-slate-300" title="Scale each trace to its own peak inside the window: shapes compare, fluxes do not">
          <input type="checkbox" checked={normalised} onChange={(e) => setNormalised(e.target.checked)} disabled={xMode !== "velocity"} /> normalise amplitudes
        </label>
        <label className="flex items-center gap-1 text-slate-400">
          ± window
          <input type="number" min="100" max="20000" step="100" value={window} onChange={(e) => setWindow(Number(e.target.value) || 1500)} disabled={xMode !== "velocity"} className="w-16 rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-100" aria-label="Velocity window in km/s" />
          km/s
        </label>
        <span className="text-slate-600">|</span>
        <label className="flex items-center gap-1 text-slate-300">
          <input type="checkbox" checked={showLines} onChange={(e) => setShowLines(e.target.checked)} /> lines
        </label>
        <select value={lineKinds} onChange={(e) => setLineKinds(e.target.value as typeof lineKinds)} className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200" aria-label="Line kinds">
          <option value="all">emission + absorption</option>
          <option value="emission">emission only</option>
          <option value="absorption">absorption only</option>
        </select>
        <span className="text-slate-600">|</span>
        <label className="flex items-center gap-1 text-slate-300" title="Overplot a 21 cm profile (velocity mode)">
          <input type="checkbox" checked={showHI} onChange={(e) => { setShowHI(e.target.checked); if (e.target.checked) setXMode("velocity"); }} /> HI profile
        </label>
        <label className="cursor-pointer rounded border border-slate-700 px-1.5 py-0.5 text-slate-200 hover:bg-slate-800">
          upload HI (v, S)
          <input type="file" accept=".txt,.dat,.csv,.asc,.tsv" className="hidden" onChange={(e) => onUpload(e.target.files?.[0])} />
        </label>
      </div>

      {loading && <p className="text-slate-400">{loading}</p>}
      {error && <p className="text-rose-300">{error}</p>}
      {hiError && showHI && <p className="text-amber-300">HI profile: {hiError}</p>}
      {traces.flatMap((t) => t.spectrum.warnings.map((w, i) => (
        <p key={`${t.id}-${i}`} className="text-amber-300">{t.label}: {w}</p>
      )))}

      {figure && active && (
        <Plot
          data={figure.data}
          layout={figure.layout}
          config={{ displaylogo: false, responsive: true }}
          style={{ width: "100%" }}
          useResizeHandler
          onError={(e) => {
            console.error("Plotly render error", e);
            setError(`Plot error: ${e instanceof Error ? e.message : String(e)}`);
          }}
        />
      )}
      {xMode === "velocity" && normalised && traces.length > 1 && (
        <p className="text-[11px] text-slate-500">
          Amplitudes are normalised independently for a shape comparison: fluxes, angular
          resolutions and calibrations differ between traces. Optical velocities use the optical
          convention about {reference?.name ?? "Hα"}; radio velocities the radio convention about{" "}
          {referenceRadio?.name ?? "CO(2-1)"}.
        </p>
      )}
      {traces.length > 0 && (
        <p className="text-[11px] text-slate-500">
          {traces.map((t) => `${t.label}: ${t.spectrum.n_channels} ch, ${t.spectrum.n_spaxels} spx, pixel ${t.spectrum.pixel_scale_arcsec?.toFixed(2)}″${t.spectrum.cached ? ", cached" : ""}`).join(" · ")}
          {showHI && hiProfile && xMode === "velocity" ? ` · ${hiProfile.note}` : ""}
        </p>
      )}
    </div>
  );
}
