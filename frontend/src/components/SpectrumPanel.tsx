import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Plotly from "plotly.js-basic-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type {
  Annotation as PlotlyAnnotation,
  Data,
  Layout,
  Shape,
} from "plotly.js";
import {
  ApiError,
  clearCubeCache,
  fetchCubeCache,
  fetchCubeJob,
  fetchCubeProducts,
  startCubeSpectrumJob,
  fetchHIProfile,
  fetchLines,
  fetchMuseSpectrum,
  fetchRadioLines,
} from "../lib/api";
import { observationLabel, toSpectrumRequest } from "../lib/spectrumRequest";
import type {
  ArchiveObservation,
  CubeCacheStatus,
  CubeProduct,
  HIDetection,
  HIProfile,
  RadioLine,
  ResolvedTarget,
  SpectralLine,
  Spectrum1D,
  SpectrumRequest,
} from "../types";

const Plot = createPlotlyComponent(Plotly);
const C_KMS = 299792.458;
/** Above this size a failed SODA cutout is reported instead of silently downloading the cube. */
const AUTO_DOWNLOAD_MAX_MB = 500;
const COLORS = [
  "#7dd3fc",
  "#fb923c",
  "#a3e635",
  "#f472b6",
  "#facc15",
  "#c084fc",
];

interface Props {
  target: ResolvedTarget | null;
  request: SpectrumRequest | null;
  /** Plots are only rendered while the tab is visible. */
  active: boolean;
  /** Velocity from an HI catalogue detection, used when the resolver has no redshift. */
  hiVelocityKms: number | null;
  /** Archive search results: the source column lists the extractable cubes among them. */
  observations: ArchiveObservation[];
  hiDetections: HIDetection[];
  searched: boolean;
  searching: boolean;
  onSearch: () => void;
}

interface Trace {
  id: string;
  label: string;
  kind: "optical" | "radio";
  spectrum: Spectrum1D;
  color: string;
  position: "target" | "cube";
  visible: boolean;
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
    if (
      parts.length < 2 ||
      !Number.isFinite(parts[0]) ||
      !Number.isFinite(parts[1])
    )
      continue;
    velocity.push(parts[0]);
    flux.push(parts[1]);
  }
  if (velocity.length < 3)
    throw new Error("Could not find two numeric columns (velocity, flux)");
  return { name, velocity, flux };
}

/** Scale a series so its largest |value| inside the window becomes 1. */
function normalise(
  x: number[],
  y: (number | null)[],
  lo: number,
  hi: number,
): (number | null)[] {
  let peak = 0;
  for (let i = 0; i < x.length; i++) {
    const v = y[i];
    if (v !== null && x[i] >= lo && x[i] <= hi && Math.abs(v) > peak)
      peak = Math.abs(v);
  }
  if (!peak)
    for (const v of y) if (v !== null && Math.abs(v) > peak) peak = Math.abs(v);
  return peak ? y.map((v) => (v === null ? null : v / peak)) : y;
}

function productLabel(base: string, p: CubeProduct): string {
  const win = p.freq_center_ghz
    ? `${p.freq_center_ghz.toFixed(2)} GHz`
    : `spw${p.spw ?? "?"}`;
  return `${base} ${win}`;
}

export type ProductState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ok"; products: CubeProduct[] };

function sourceKey(d: HIDetection): string {
  return d.source_id ?? `${d.survey}:${d.name ?? ""}:${d.velocity_kms ?? ""}`;
}

/** "contains line" from the product's window and the chosen line, recomputed client-side so a
 * change of line does not need a new DataLink call. */
function containsLine(p: CubeProduct, lineGhz: number | null): boolean | null {
  if (lineGhz !== null && p.freq_min_ghz !== null && p.freq_max_ghz !== null)
    return p.freq_min_ghz <= lineGhz && p.freq_max_ghz >= lineGhz;
  return p.contains_line;
}

const PRODUCT_KIND_LABEL: Record<string, string> = {
  cube: "science cubes",
  auxiliary: "auxiliary (mask, pb, psf, residual…)",
  calibrator: "calibrator (check, phase, bandpass…)",
};

/** Compact product rows for the source column. */
function ProductRows({
  state,
  lineGhz,
  onExtract,
}: {
  state: ProductState;
  lineGhz: number | null;
  onExtract: (p: CubeProduct) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  if (state.status === "loading")
    return <p className="pl-4 text-slate-500">listing products…</p>;
  if (state.status === "error")
    return <p className="pl-4 text-rose-300">{state.message}</p>;
  const products = state.products;
  const science = products.filter((p) => p.kind === "cube");
  const auxiliary = products.filter((p) => p.kind === "auxiliary");
  const calibrator = products.filter((p) => p.kind === "calibrator");
  const packages = products.filter((p) => p.kind === "package");
  const hidden = auxiliary.length + calibrator.length;
  const groups: [string, CubeProduct[]][] = [["cube", science]];
  if (showAll || science.length === 0)
    groups.push(["auxiliary", auxiliary], ["calibrator", calibrator]);
  return (
    <div className="space-y-0.5 pl-4">
      {science.length === 0 && (
        <p className="text-amber-300">
          No science cube listed.
          {hidden > 0 &&
            ` ${hidden} auxiliary/calibrator cube${hidden === 1 ? "" : "s"} below (not the target's flux).`}
          {packages.length > 0 &&
            ` ${packages.length} tar package${packages.length === 1 ? "" : "s"} only downloadable from the archive.`}
          {products.length === 0 && " DataLink listed no files."}
        </p>
      )}
      {groups.map(([kind, list]) =>
        list.length === 0 ? null : (
          <div key={kind}>
            <p className="text-[10px] uppercase tracking-wide text-slate-600">
              {PRODUCT_KIND_LABEL[kind]}
            </p>
            <ul className="space-y-0.5">
              {list.slice(0, 24).map((p) => {
                const has = containsLine(p, lineGhz);
                return (
                  <li
                    key={p.url}
                    className="flex items-center gap-1"
                    title={`${p.name}${p.freq_min_ghz ? ` · ${p.freq_min_ghz.toFixed(2)}–${p.freq_max_ghz?.toFixed(2)} GHz${p.freq_approx ? " (estimated window)" : ""}` : ""}`}
                  >
                    <span
                      className={`shrink-0 rounded px-1 text-[10px] ${has ? "bg-emerald-900/50 text-emerald-300" : "bg-slate-800 text-slate-400"}`}
                    >
                      {has ? "line" : has === false ? "other" : "?"}
                      {p.freq_approx && has !== null ? "~" : ""}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-slate-300">
                      spw{p.spw ?? "?"}
                      {p.freq_center_ghz
                        ? ` · ${p.freq_center_ghz.toFixed(2)} GHz`
                        : p.freq_min_ghz
                          ? ` · ${p.freq_min_ghz.toFixed(1)}–${p.freq_max_ghz?.toFixed(1)}`
                          : ""}{" "}
                      ·{" "}
                      {p.size_mb
                        ? p.size_mb >= 1000
                          ? `${(p.size_mb / 1000).toFixed(1)} GB`
                          : `${Math.round(p.size_mb)} MB`
                        : "? MB"}
                    </span>
                    <button
                      type="button"
                      onClick={() => onExtract(p)}
                      className="shrink-0 rounded border border-emerald-700 px-1 py-px text-[10px] text-emerald-300 hover:bg-emerald-900/40"
                    >
                      extract
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ),
      )}
      {science.length > 0 && hidden > 0 && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="text-[10px] text-slate-500 underline hover:text-slate-300"
        >
          {showAll ? "hide" : "show"} {hidden} auxiliary/calibrator
        </button>
      )}
    </div>
  );
}

/** One entry of the "plotted" lists: a cube trace or an HI profile. */
export interface PlottedItem {
  id: string;
  label: string;
  color: string;
  visible: boolean;
  kind: "optical" | "radio" | "hi";
}

function PlottedList({
  items,
  onToggle,
  onRemove,
  compact,
}: {
  items: PlottedItem[];
  onToggle: (id: string, visible: boolean) => void;
  onRemove: (id: string) => void;
  compact?: boolean;
}) {
  if (!items.length) return null;
  const row = (t: PlottedItem) => (
    <>
      <input
        type="checkbox"
        checked={t.visible}
        onChange={(e) => onToggle(t.id, e.target.checked)}
        aria-label={`Show ${t.label}`}
      />
      <span
        className="inline-block h-2 w-2 shrink-0 rounded-sm"
        style={{ background: t.color }}
      />
      <span
        className={`min-w-0 truncate ${t.visible ? "text-slate-200" : "text-slate-500 line-through"} ${compact ? "max-w-[18rem]" : "flex-1"}`}
        title={t.label}
      >
        {t.label}
      </span>
      <button
        type="button"
        onClick={() => onRemove(t.id)}
        className="text-slate-500 hover:text-rose-300"
        aria-label={`Remove ${t.label}`}
      >
        ✕
      </button>
    </>
  );
  if (compact)
    return (
      <div
        className="flex flex-wrap items-center gap-2"
        data-testid="plotted-row"
      >
        <span className="text-slate-500">plotted:</span>
        {items.map((t) => (
          <span
            key={t.id}
            className="flex items-center gap-1 rounded border border-slate-700 px-1.5 py-0.5"
          >
            {row(t)}
          </span>
        ))}
      </div>
    );
  return (
    <ul className="space-y-0.5">
      {items.map((t) => (
        <li
          key={t.id}
          className="flex items-center gap-1 rounded px-1 py-0.5 hover:bg-slate-800/40"
        >
          {row(t)}
        </li>
      ))}
    </ul>
  );
}

type Group = "alma" | "muse" | "hi";

interface SourceColumnProps {
  observations: ArchiveObservation[];
  hiDetections: HIDetection[];
  searched: boolean;
  searching: boolean;
  onSearch: () => void;
  current: SpectrumRequest | null;
  onSelect: (req: SpectrumRequest) => void;
  plotted: PlottedItem[];
  onToggleTrace: (id: string, visible: boolean) => void;
  onRemoveTrace: (id: string) => void;
  hiSource: string | null;
  onHITemplate: (d: HIDetection) => void;
  onUpload: (file: File | undefined) => void;
  productsByObs: Record<string, ProductState>;
  expandedAlma: Set<string>;
  onToggleAlma: (req: SpectrumRequest) => void;
  onExtractProduct: (req: SpectrumRequest, p: CubeProduct) => void;
  lineGhz: number | null;
  lineName: string;
  radioLines: RadioLine[];
  refRadio: string;
  onRefRadio: (name: string) => void;
}

/** Left column of the Spectrum tab: everything that can be plotted, grouped by source like the
 * observation table. Groups open on click; an ALMA cube opens to its spectral-window products. */
function SourceColumn(p: SourceColumnProps) {
  const [open, setOpen] = useState<Set<Group>>(new Set());
  // A selection made elsewhere (observation table, HI template) opens its group here.
  const currentKind = p.current?.kind ?? null;
  useEffect(() => {
    if (currentKind) setOpen((prev) => new Set(prev).add(currentKind));
  }, [currentKind, p.current]);
  useEffect(() => {
    if (p.hiSource) setOpen((prev) => new Set(prev).add("hi"));
  }, [p.hiSource]);
  const toggle = (g: Group) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });
  const alma = p.observations.filter(
    (o) => toSpectrumRequest(o)?.kind === "alma",
  );
  const muse = p.observations.filter(
    (o) => toSpectrumRequest(o)?.kind === "muse",
  );
  const groupHeader = (
    g: Group,
    text: string,
    count: number,
    pending: boolean,
  ) => (
    <button
      type="button"
      onClick={() => toggle(g)}
      className="mt-2 flex w-full items-center gap-1 rounded px-1 text-left text-[11px] uppercase tracking-wide text-slate-400 hover:bg-slate-800/60"
      aria-expanded={open.has(g)}
    >
      <span className="w-3 text-slate-500">{open.has(g) ? "▾" : "▸"}</span>
      <span className="text-slate-300">{text}</span>
      <span className="text-slate-600">{pending ? "…" : count}</span>
    </button>
  );
  const obsHeader = (o: ArchiveObservation) => (
    <>
      <span
        className="min-w-0 flex-1 truncate text-slate-200"
        title={observationLabel(o)}
      >
        {(o.obs_id ?? "").replace(/^uid:\/\//, "") || o.target_name || "—"}
      </span>
    </>
  );
  const obsMeta = (o: ArchiveObservation) => (
    <div className="flex flex-wrap gap-x-1 text-[10px] text-slate-500">
      <span>{o.date_obs?.slice(0, 10) ?? "no date"}</span>
      {o.separation_arcmin !== null && (
        <span>· {o.separation_arcmin.toFixed(1)}′</span>
      )}
      {o.covers_target === false && (
        <span className="text-amber-400">· outside footprint</span>
      )}
      {o.covers_target === true && (
        <span className="text-emerald-400">· covers target</span>
      )}
    </div>
  );
  return (
    <div className="flex w-72 shrink-0 flex-col text-xs">
      {p.plotted.length > 0 && (
        <>
          <p className="mt-2 flex items-center gap-1 px-1 text-[11px] uppercase tracking-wide text-slate-500">
            Plotted <span className="text-slate-600">{p.plotted.length}</span>
          </p>
          <PlottedList
            items={p.plotted}
            onToggle={p.onToggleTrace}
            onRemove={p.onRemoveTrace}
          />
        </>
      )}
      {!p.searched ? (
        <p className="mt-2 px-1 text-slate-500">
          Spectra come from the archive search.{" "}
          <button
            type="button"
            onClick={p.onSearch}
            className="text-sky-300 underline hover:text-sky-200"
          >
            search archives
          </button>
        </p>
      ) : (
        <>
          {groupHeader(
            "alma",
            "ALMA cubes",
            alma.length,
            p.searching && alma.length === 0,
          )}
          {open.has("alma") && (
            <div className="pl-1">
              <div className="flex flex-wrap items-center gap-1 py-0.5 text-[10px] text-slate-500">
                line
                <select
                  value={p.refRadio}
                  onChange={(e) => p.onRefRadio(e.target.value)}
                  className="rounded border border-slate-700 bg-slate-950 px-1 py-px text-slate-200"
                  aria-label="Radio line"
                >
                  {p.radioLines.map((l) => (
                    <option key={l.name} value={l.name}>
                      {l.name}
                    </option>
                  ))}
                  {!p.radioLines.length && (
                    <option value="CO(2-1)">CO(2-1)</option>
                  )}
                </select>
                {p.lineGhz !== null ? (
                  <span>{p.lineGhz.toFixed(3)} GHz observed</span>
                ) : (
                  <span className="text-amber-400">
                    enter cz to place {p.lineName}
                  </span>
                )}
              </div>
              {!p.searching && alma.length === 0 && (
                <p className="text-slate-600">
                  none public in the search radius
                </p>
              )}
              <ul className="space-y-0.5">
                {alma.map((o) => {
                  const req = toSpectrumRequest(o) as SpectrumRequest;
                  const expanded = p.expandedAlma.has(req.datalinkUrl);
                  const selected = p.current?.datalinkUrl === req.datalinkUrl;
                  return (
                    <li
                      key={req.datalinkUrl}
                      className={`rounded px-1 py-0.5 ${selected ? "bg-slate-800/80" : "hover:bg-slate-800/40"}`}
                    >
                      <button
                        type="button"
                        onClick={() => p.onToggleAlma(req)}
                        className="flex w-full items-center gap-1 text-left"
                        aria-expanded={expanded}
                      >
                        <span className="w-3 text-slate-500">
                          {expanded ? "▾" : "▸"}
                        </span>
                        {obsHeader(o)}
                      </button>
                      <div className="pl-4">{obsMeta(o)}</div>
                      {expanded && (
                        <ProductRows
                          state={
                            p.productsByObs[req.datalinkUrl] ?? {
                              status: "loading",
                            }
                          }
                          lineGhz={p.lineGhz}
                          onExtract={(prod) => p.onExtractProduct(req, prod)}
                        />
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {groupHeader(
            "muse",
            "MUSE cubes",
            muse.length,
            p.searching && muse.length === 0,
          )}
          {open.has("muse") && (
            <div className="pl-1">
              {!p.searching && muse.length === 0 && (
                <p className="text-slate-600">
                  none public in the search radius
                </p>
              )}
              <ul className="space-y-0.5">
                {muse.map((o) => {
                  const req = toSpectrumRequest(o) as SpectrumRequest;
                  const selected = p.current?.datalinkUrl === req.datalinkUrl;
                  return (
                    <li
                      key={req.datalinkUrl}
                      className={`rounded px-1 py-0.5 ${selected ? "bg-slate-800/80" : "hover:bg-slate-800/40"}`}
                    >
                      <div className="flex items-center gap-1">
                        {obsHeader(o)}
                        <button
                          type="button"
                          onClick={() => p.onSelect(req)}
                          className="shrink-0 rounded border border-emerald-700 px-1 py-px text-[10px] text-emerald-300 hover:bg-emerald-900/40"
                        >
                          extract
                        </button>
                      </div>
                      {obsMeta(o)}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </>
      )}
      {groupHeader("hi", "HI 21 cm", p.hiDetections.length, false)}
      {open.has("hi") && (
        <div className="pl-1">
          {p.hiDetections.length === 0 && (
            <p className="text-slate-600">no catalogue detection</p>
          )}
          <ul className="space-y-0.5">
            {p.hiDetections.map((d) => {
              const key = sourceKey(d);
              const selected = p.hiSource === key;
              return (
                <li
                  key={key}
                  className={`rounded px-1 py-0.5 ${selected ? "bg-slate-800/80" : "hover:bg-slate-800/40"}`}
                >
                  <div className="flex items-center gap-1">
                    <span
                      className="min-w-0 flex-1 truncate text-slate-200"
                      title={`${d.survey} ${d.name ?? d.source_id ?? ""}`}
                    >
                      {d.survey} {d.name ?? d.source_id ?? ""}
                    </span>
                    <button
                      type="button"
                      onClick={() => p.onHITemplate(d)}
                      className="shrink-0 rounded border border-amber-700 px-1 py-px text-[10px] text-amber-300 hover:bg-amber-900/40"
                      title="Double-horn template from the catalogue Vsys, W50, W20 and flux"
                    >
                      template
                    </button>
                  </div>
                  <div className="text-[10px] text-slate-500">
                    {d.velocity_kms !== null
                      ? `cz ${d.velocity_kms.toFixed(0)} km/s`
                      : "no velocity"}
                    {d.w50_kms !== null ? ` · W50 ${d.w50_kms.toFixed(0)}` : ""}
                    {d.flux_jy_kms !== null
                      ? ` · ${d.flux_jy_kms.toFixed(1)} Jy km/s`
                      : ""}
                  </div>
                </li>
              );
            })}
          </ul>
          <label
            className="mt-1 inline-block cursor-pointer rounded border border-slate-700 px-1.5 py-0.5 text-slate-300 hover:bg-slate-800"
            title="Two-column ASCII: velocity (km/s), flux"
          >
            upload HI spectrum
            <input
              type="file"
              accept=".txt,.dat,.csv,.asc,.tsv"
              className="hidden"
              onChange={(e) => p.onUpload(e.target.files?.[0])}
            />
          </label>
        </div>
      )}
      <p className="mt-2 px-1 text-[10px] text-slate-600">
        HST and JWST products stay in the observation list: MAST offers no
        cutout service for them.
      </p>
    </div>
  );
}

export default function SpectrumPanel({
  target,
  request,
  active,
  hiVelocityKms,
  observations,
  hiDetections,
  searched,
  searching,
  onSearch,
}: Props) {
  const [radius, setRadius] = useState(3);
  const [radiusInput, setRadiusInput] = useState("3");
  const [method, setMethod] = useState<"sum" | "mean">("sum");
  const [position, setPosition] = useState<"target" | "cube">("target");
  const [allTraces, setTraces] = useState<Trace[]>([]);
  const traces = useMemo(() => allTraces.filter((t) => t.visible), [allTraces]);
  const [current, setCurrent] = useState<SpectrumRequest | null>(null);
  const [hiSource, setHISource] = useState<string | null>(null);
  const [sourcesOpen, setSourcesOpen] = useState(true);
  const [lines, setLines] = useState<SpectralLine[]>([]);
  const [radioLines, setRadioLines] = useState<RadioLine[]>([]);
  const [showLines, setShowLines] = useState(true);
  const [lineKinds, setLineKinds] = useState<"all" | "emission" | "absorption">(
    "all",
  );
  const [xMode, setXMode] = useState<XMode>("wavelength");
  const [refLine, setRefLine] = useState("Hα");
  const [refRadio, setRefRadio] = useState("CO(2-1)");
  const [relative, setRelative] = useState(true);
  const [normalised, setNormalised] = useState(false);
  const [window, setWindow] = useState(1500);
  const [hiTemplateOn, setHITemplateOn] = useState(false);
  const [uploadOn, setUploadOn] = useState(false);
  const [progress, setProgress] = useState<{
    state: string;
    downloadedMb: number;
    totalMb: number | null;
    started: number;
  } | null>(null);
  const [, setTick] = useState(0);
  const [cache, setCache] = useState<CubeCacheStatus | null>(null);
  /** A cutout that failed on a big product: offer the full download explicitly. */
  const [downloadOffer, setDownloadOffer] = useState<{
    req: SpectrumRequest;
    product: CubeProduct;
  } | null>(null);
  const [hiProfile, setHIProfile] = useState<HIProfile | null>(null);
  const [hiError, setHIError] = useState<string | null>(null);
  const [uploaded, setUploaded] = useState<UploadedHI | null>(null);
  const [productsByObs, setProductsByObs] = useState<
    Record<string, ProductState>
  >({});
  const [expandedAlma, setExpandedAlma] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const [manualCz, setManualCz] = useState("");
  const manual = parseFloat(manualCz);
  const czSource = Number.isFinite(manual)
    ? "manual"
    : target?.kinematics.velocity_kms !== null &&
        target?.kinematics.velocity_kms !== undefined
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
    fetchLines(z, 3000, 25000, ac.signal)
      .then((l) => !ac.signal.aborted && setLines(l))
      .catch(() => undefined);
    fetchRadioLines(z, undefined, undefined, ac.signal)
      .then((l) => !ac.signal.aborted && setRadioLines(l))
      .catch(() => undefined);
    return () => ac.abort();
  }, [z]);

  // Reset everything when the target changes.
  useEffect(() => {
    setTraces([]);
    setCurrent(null);
    setProductsByObs({});
    setExpandedAlma(new Set());
    setHIProfile(null);
    setHISource(null);
    setUploaded(null);
    setError(null);
  }, [target]);

  useEffect(() => {
    const id = setTimeout(
      () => globalThis.dispatchEvent(new Event("resize")),
      50,
    );
    return () => clearTimeout(id);
  }, [sourcesOpen]);

  const reference = useMemo(
    () =>
      lines.find((l) => l.name === refLine) ??
      lines.find((l) => l.name === "Hα") ??
      null,
    [lines, refLine],
  );
  const referenceRadio = useMemo(
    () =>
      radioLines.find((l) => l.name === refRadio) ??
      radioLines.find((l) => l.name === "CO(2-1)") ??
      null,
    [radioLines, refRadio],
  );

  const extractPosition = useCallback(
    (
      req: SpectrumRequest,
      pos: "target" | "cube",
    ): { ra: number; dec: number } | null => {
      if (pos === "cube" && req.cubeRa !== null && req.cubeDec !== null)
        return { ra: req.cubeRa, dec: req.cubeDec };
      if (!target) return null;
      return { ra: target.coordinates.ra_deg, dec: target.coordinates.dec_deg };
    },
    [target],
  );

  const addTrace = useCallback((t: Trace) => {
    setTraces((prev) => {
      const others = prev.filter((x) => x.id !== t.id);
      const color = COLORS[others.length % COLORS.length];
      return [...others, { ...t, color, visible: true }];
    });
  }, []);

  const extractMuse = useCallback(
    async (req: SpectrumRequest, pos: "target" | "cube") => {
      const at = extractPosition(req, pos);
      if (!at) return;
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      setLoading(
        `Requesting a SODA cutout of ${req.label}… ESO builds the cutout server-side, usually within a minute`,
      );
      setProgress({
        state: "soda",
        downloadedMb: 0,
        totalMb: null,
        started: Date.now(),
      });
      setError(null);
      try {
        const spectrum = await fetchMuseSpectrum(
          {
            datalinkUrl: req.datalinkUrl,
            ra: at.ra,
            dec: at.dec,
            radiusArcsec: radius,
            method,
          },
          ac.signal,
        );
        if (!ac.signal.aborted)
          addTrace({
            id: req.datalinkUrl,
            label: req.label,
            kind: "optical",
            spectrum,
            color: "",
            position: pos,
            visible: true,
          });
      } catch (e: unknown) {
        if (!ac.signal.aborted)
          setError(e instanceof ApiError ? e.message : String(e));
      } finally {
        if (!ac.signal.aborted) {
          setLoading(null);
          setProgress(null);
        }
      }
    },
    [extractPosition, radius, method, addTrace],
  );

  /** Load the DataLink products of an ALMA cube into the source column (once per cube). */
  const listAlmaProducts = useCallback(
    async (req: SpectrumRequest, force = false) => {
      setExpandedAlma((prev) => new Set(prev).add(req.datalinkUrl));
      if (!force && productsRef.current[req.datalinkUrl]?.status === "ok")
        return;
      setProductsByObs((prev) => ({
        ...prev,
        [req.datalinkUrl]: { status: "loading" },
      }));
      try {
        const list = await fetchCubeProducts(
          req.datalinkUrl,
          req.spwRanges,
          null,
        );
        setProductsByObs((prev) => ({
          ...prev,
          [req.datalinkUrl]: { status: "ok", products: list },
        }));
      } catch (e: unknown) {
        setProductsByObs((prev) => ({
          ...prev,
          [req.datalinkUrl]: {
            status: "error",
            message: e instanceof ApiError ? e.message : String(e),
          },
        }));
      }
    },
    [],
  );
  const productsRef = useRef(productsByObs);
  productsRef.current = productsByObs;

  const toggleAlma = useCallback(
    (req: SpectrumRequest) => {
      if (expandedAlma.has(req.datalinkUrl)) {
        setExpandedAlma((prev) => {
          const next = new Set(prev);
          next.delete(req.datalinkUrl);
          return next;
        });
      } else {
        setCurrent(req);
        void listAlmaProducts(req);
      }
    },
    [expandedAlma, listAlmaProducts],
  );

  /** SODA band for an ALMA cutout: the chosen line ± twice the velocity window (at least
   * ±2000 km/s) when the product's window holds the line; otherwise the whole product. */
  const cutoutBand = useCallback(
    (product: CubeProduct): { fminGhz?: number; fmaxGhz?: number } => {
      const line = referenceRadio?.observed_frequency_ghz ?? null;
      if (line === null || containsLine(product, line) !== true) return {};
      const v = Math.max(2 * window, 2000);
      return {
        fminGhz: line * (1 - v / C_KMS),
        fmaxGhz: line * (1 + v / C_KMS),
      };
    },
    [referenceRadio, window],
  );

  const extractAlma = useCallback(
    async (
      req: SpectrumRequest,
      product: CubeProduct,
      pos: "target" | "cube",
      mode?: "auto" | "cutout" | "download",
    ) => {
      const at = extractPosition(req, pos);
      if (!at) return;
      // Small products may fall back to a full download on their own; big ones only on request.
      const chosen =
        mode ??
        (product.size_mb !== null && product.size_mb > AUTO_DOWNLOAD_MAX_MB
          ? "cutout"
          : "auto");
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      setDownloadOffer(null);
      setLoading(
        chosen === "download"
          ? `Downloading ${product.name} in full…`
          : `Requesting a SODA cutout of ${product.name} from ALMA…`,
      );
      setProgress({
        state: "queued",
        downloadedMb: 0,
        totalMb: product.size_mb,
        started: Date.now(),
      });
      setError(null);
      try {
        const opts = {
          productUrl: product.url,
          ra: at.ra,
          dec: at.dec,
          radiusArcsec: radius,
          method,
          maxMb: product.size_mb ? Math.ceil(product.size_mb * 1.2) : undefined,
          ...cutoutBand(product),
          mode: chosen,
        };
        let job = await startCubeSpectrumJob(opts, ac.signal);
        while (job.state !== "done" && job.state !== "error") {
          await new Promise((r) => setTimeout(r, 700));
          if (ac.signal.aborted) return;
          job = await fetchCubeJob(job.job_id, ac.signal);
          const j = job;
          setProgress((prev) => ({
            state: j.state,
            downloadedMb: j.downloaded_mb,
            totalMb: j.total_mb ?? product.size_mb,
            started: prev?.started ?? Date.now(),
          }));
          setLoading(
            j.state === "extracting"
              ? `Extracting the aperture spectrum from ${product.name}…`
              : j.state === "downloading"
                ? `${j.message ? `${j.message}. ` : ""}Downloading ${product.name} in full…`
                : `Requesting a SODA cutout of ${product.name} from ALMA (aperture + band)… usually seconds`,
          );
        }
        if (job.state === "error" || !job.spectrum)
          throw new ApiError(502, job.message ?? "cube job failed");
        const spectrum = job.spectrum;
        fetchCubeCache()
          .then(setCache)
          .catch(() => undefined);
        if (!ac.signal.aborted) {
          addTrace({
            id: product.url,
            label: productLabel(req.label, product),
            kind: "radio",
            spectrum,
            color: "",
            position: pos,
            visible: true,
          });
          setXMode("velocity");
          setNormalised(true);
        }
      } catch (e: unknown) {
        if (!ac.signal.aborted) {
          setError(e instanceof ApiError ? e.message : String(e));
          if (chosen === "cutout") setDownloadOffer({ req, product });
        }
      } finally {
        if (!ac.signal.aborted) {
          setLoading(null);
          setProgress(null);
        }
      }
    },
    [extractPosition, radius, method, addTrace, cutoutBand],
  );

  // Elapsed-time ticker while something is loading; cache usage when the tab opens.
  useEffect(() => {
    if (!loading) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [loading]);
  useEffect(() => {
    if (!active) return;
    const ac = new AbortController();
    fetchCubeCache(ac.signal)
      .then(setCache)
      .catch(() => undefined);
    return () => ac.abort();
  }, [active]);

  const plotted = useMemo<PlottedItem[]>(() => {
    const items: PlottedItem[] = allTraces.map((t) => ({
      id: t.id,
      label: t.label,
      color: t.color,
      visible: t.visible,
      kind: t.kind,
    }));
    if (hiProfile)
      items.push({
        id: "hi-template",
        label: `HI template (${hiProfile.survey ?? "catalogue"})`,
        color: "#fbbf24",
        visible: hiTemplateOn,
        kind: "hi",
      });
    if (uploaded)
      items.push({
        id: "hi-upload",
        label: `HI ${uploaded.name}`,
        color: "#f97316",
        visible: uploadOn,
        kind: "hi",
      });
    return items;
  }, [allTraces, hiProfile, hiTemplateOn, uploaded, uploadOn]);
  const togglePlotted = useCallback((id: string, visible: boolean) => {
    if (id === "hi-template") {
      setHITemplateOn(visible);
      if (visible) setXMode("velocity");
    } else if (id === "hi-upload") {
      setUploadOn(visible);
      if (visible) setXMode("velocity");
    } else
      setTraces((prev) =>
        prev.map((t) => (t.id === id ? { ...t, visible } : t)),
      );
  }, []);
  const removePlotted = useCallback((id: string) => {
    if (id === "hi-template") {
      setHIProfile(null);
      setHITemplateOn(false);
      setHISource(null);
    } else if (id === "hi-upload") {
      setUploaded(null);
      setUploadOn(false);
    } else setTraces((prev) => prev.filter((t) => t.id !== id));
  }, []);

  /** Select an observation (from the source column or the observation table) and start on it. */
  const select = useCallback(
    (req: SpectrumRequest) => {
      if (!target) return;
      setCurrent(req);
      setSourcesOpen(true);
      const pos: "target" | "cube" =
        req.coversTarget === false && req.cubeRa !== null ? "cube" : "target";
      setPosition(pos);
      if (req.kind === "muse") void extractMuse(req, pos);
      else void listAlmaProducts(req);
    },
    [target, extractMuse, listAlmaProducts],
  );

  // React to a new request from the observation table.
  useEffect(() => {
    if (request) select(request);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request, target]);

  const loadHITemplate = useCallback(
    (d: HIDetection) => {
      if (!target) return;
      const key = sourceKey(d);
      setHISource(key);
      setHITemplateOn(true);
      setXMode("velocity");
      setHIError(null);
      fetchHIProfile(
        target.coordinates.ra_deg,
        target.coordinates.dec_deg,
        d.source_id ?? undefined,
      )
        .then((p) => setHIProfile(p))
        .catch((e: unknown) =>
          setHIError(e instanceof ApiError ? e.message : String(e)),
        );
    },
    [target],
  );

  // HI template on demand.
  useEffect(() => {
    if (!hiTemplateOn || !target || hiProfile) return;
    const ac = new AbortController();
    setHIError(null);
    fetchHIProfile(
      target.coordinates.ra_deg,
      target.coordinates.dec_deg,
      undefined,
      ac.signal,
    )
      .then((p) => !ac.signal.aborted && setHIProfile(p))
      .catch((e: unknown) => {
        if (ac.signal.aborted) return;
        setHIError(e instanceof ApiError ? e.message : String(e));
      });
    return () => ac.abort();
  }, [hiTemplateOn, target, hiProfile]);

  const velocityOf = useCallback(
    (t: Trace, i: number): number | null => {
      if (t.kind === "optical") {
        if (!reference) return null;
        const v =
          C_KMS *
          (t.spectrum.wavelength_A[i] / reference.rest_wavelength_A - 1);
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
    const hiTemplateShown = hiTemplateOn && hiProfile !== null;
    const uploadShown = uploadOn && uploaded !== null;
    if (
      !traces.length &&
      !(xMode === "velocity" && (hiTemplateShown || uploadShown))
    )
      return null;
    const centre = relative || cz === null ? 0 : cz;
    const lo = centre - window;
    const hi = centre + window;
    const data: Data[] = [];
    const visible =
      xMode === "wavelength"
        ? traces.filter((t) => t.kind === "optical")
        : traces;
    for (const t of visible) {
      const x =
        xMode === "wavelength"
          ? t.spectrum.wavelength_A
          : t.spectrum.wavelength_A.map((_, i) => velocityOf(t, i));
      const xs = x.map((v) => (v === null ? NaN : v));
      let y: (number | null)[] = t.spectrum.flux;
      if (xMode === "velocity" && normalised) y = normalise(xs, y, lo, hi);
      const radioAxis =
        xMode === "velocity" && t.kind === "radio" && !normalised;
      data.push({
        x: xs,
        y,
        type: "scatter",
        mode: "lines",
        name: `${t.label} (r=${t.spectrum.aperture_radius_arcsec}″${t.position === "cube" ? ", cube centre" : ""})`,
        line: { color: t.color, width: 1 },
        yaxis: radioAxis ? "y2" : "y",
        hovertemplate:
          xMode === "wavelength"
            ? "%{x:.1f} Å<br>%{y:.3g}<extra></extra>"
            : "%{x:.0f} km/s<br>%{y:.3g}<extra></extra>",
      });
      if (t.spectrum.error && !normalised && xMode === "wavelength") {
        const upper = t.spectrum.flux.map((f, i) =>
          f === null || t.spectrum.error![i] === null
            ? null
            : f + (t.spectrum.error![i] as number),
        );
        const lower = t.spectrum.flux.map((f, i) =>
          f === null || t.spectrum.error![i] === null
            ? null
            : f - (t.spectrum.error![i] as number),
        );
        data.push(
          {
            x: xs,
            y: upper,
            type: "scatter",
            mode: "lines",
            line: { width: 0 },
            showlegend: false,
            hoverinfo: "skip",
          },
          {
            x: xs,
            y: lower,
            type: "scatter",
            mode: "lines",
            line: { width: 0 },
            fill: "tonexty",
            fillcolor: "rgba(125,211,252,0.15)",
            name: "±1σ",
            hoverinfo: "skip",
          },
        );
      }
    }
    const shapes: Partial<Shape>[] = [];
    const annotations: Partial<PlotlyAnnotation>[] = [];
    if (showLines && xMode === "wavelength") {
      for (const l of lines) {
        if (l.observed_wavelength_A === null) continue;
        if (lineKinds !== "all" && l.kind !== "both" && l.kind !== lineKinds)
          continue;
        const color =
          l.kind === "absorption"
            ? "#f59e0b"
            : l.kind === "emission"
              ? "#f472b6"
              : "#a3e635";
        shapes.push({
          type: "line",
          xref: "x",
          yref: "paper",
          x0: l.observed_wavelength_A,
          x1: l.observed_wavelength_A,
          y0: 0,
          y1: 1,
          line: { color, width: 1, dash: "dot" },
        });
        annotations.push({
          x: l.observed_wavelength_A,
          y: 1,
          xref: "x",
          yref: "paper",
          text: l.name,
          showarrow: false,
          textangle: -90,
          yanchor: "top",
          xanchor: "left",
          font: { size: 9, color },
        });
      }
    }
    if (xMode === "velocity" && cz !== null) {
      shapes.push({
        type: "line",
        xref: "x",
        yref: "paper",
        x0: centre,
        x1: centre,
        y0: 0,
        y1: 1,
        line: { color: "#fde047", width: 1 },
      });
    }
    const hiTraces: Data[] = [];
    if (xMode === "velocity" && (hiTemplateShown || uploadShown)) {
      const hiX = (v: number) => (relative && cz !== null ? v - cz : v);
      const pushHI = (
        xv: number[],
        yv: number[],
        name: string,
        color: string,
        dash?: "dash",
      ) => {
        const y = normalised ? normalise(xv, yv, lo, hi) : yv;
        hiTraces.push({
          x: xv,
          y,
          type: "scatter",
          mode: "lines",
          name,
          yaxis: normalised ? "y" : "y2",
          line: { color, width: 1.5, dash },
          hovertemplate: "%{x:.0f} km/s<br>%{y:.3f}<extra>" + name + "</extra>",
        });
      };
      if (hiTemplateShown && hiProfile)
        pushHI(
          hiProfile.velocity_kms.map(hiX),
          hiProfile.flux_jy,
          `HI template (${hiProfile.survey ?? "catalogue"})`,
          "#fbbf24",
          "dash",
        );
      if (uploadShown && uploaded)
        pushHI(
          uploaded.velocity.map(hiX),
          uploaded.flux,
          `HI ${uploaded.name}`,
          "#f97316",
        );
    }
    const hasY2 = [...data, ...hiTraces].some(
      (d) => (d as { yaxis?: string }).yaxis === "y2",
    );
    const opticalUnit =
      traces.find((t) => t.kind === "optical")?.spectrum.flux_unit ?? "flux";
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
      yaxis: {
        title: {
          text:
            normalised && xMode === "velocity"
              ? "normalised amplitude"
              : opticalUnit,
        },
        gridcolor: "#1e293b",
        zeroline: false,
      },
      legend: { orientation: "h", y: -0.22, font: { size: 10 } },
      showlegend: true,
      shapes,
      annotations,
      hovermode: "x unified",
    };
    if (hasY2) {
      const radioUnit =
        traces.find((t) => t.kind === "radio")?.spectrum.flux_unit ?? "Jy";
      layout.yaxis2 = {
        title: { text: radioUnit },
        overlaying: "y",
        side: "right",
        showgrid: false,
        zeroline: false,
      };
    }
    return { data: [...data, ...hiTraces], layout };
  }, [
    traces,
    lines,
    showLines,
    lineKinds,
    xMode,
    reference,
    referenceRadio,
    relative,
    cz,
    hiTemplateOn,
    uploadOn,
    hiProfile,
    uploaded,
    normalised,
    window,
    velocityOf,
  ]);

  const onUpload = async (file: File | undefined) => {
    if (!file) return;
    try {
      setUploaded(parseHIText(await file.text(), file.name));
      setUploadOn(true);
      setXMode("velocity");
    } catch (e: unknown) {
      setHIError(e instanceof Error ? e.message : String(e));
    }
  };

  const applyRadius = () => {
    const r = parseFloat(radiusInput);
    if (Number.isFinite(r) && r > 0 && r <= 30) setRadius(r);
  };

  return (
    <div className="flex gap-2 px-3 py-2 text-xs">
      <div className="flex shrink-0 flex-col border-r border-slate-800 pr-2">
        <button
          type="button"
          onClick={() => setSourcesOpen((v) => !v)}
          className="mb-1 self-start rounded px-1 text-slate-400 hover:bg-slate-800"
          title={sourcesOpen ? "Hide the source list" : "Show the source list"}
          aria-label={sourcesOpen ? "Hide sources" : "Show sources"}
        >
          {sourcesOpen ? "◂ sources" : "sources ▸"}
        </button>
        {sourcesOpen && (
          <SourceColumn
            observations={observations}
            hiDetections={hiDetections}
            searched={searched}
            searching={searching}
            onSearch={onSearch}
            current={current}
            onSelect={select}
            plotted={plotted}
            onToggleTrace={togglePlotted}
            onRemoveTrace={removePlotted}
            hiSource={hiSource}
            onHITemplate={loadHITemplate}
            onUpload={(f) => void onUpload(f)}
            productsByObs={productsByObs}
            expandedAlma={expandedAlma}
            onToggleAlma={toggleAlma}
            onExtractProduct={(req, prod) => {
              setCurrent(req);
              void extractAlma(req, prod, position);
            }}
            lineGhz={referenceRadio?.observed_frequency_ghz ?? null}
            lineName={referenceRadio?.name ?? refRadio}
            radioLines={radioLines}
            refRadio={refRadio}
            onRefRadio={setRefRadio}
          />
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        {!current && !allTraces.length && (
          <p className="text-slate-500">
            Pick a cube in the source list (or press “spectrum” in the
            observation table). MUSE spectra come from an ESO SODA cutout; ALMA
            spectra from the spectral-window cube product that contains the
            chosen line; HI profiles are catalogue templates or your own upload.
            Traces overplot on a common velocity axis with independently
            normalised amplitudes.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          {current && (
            <>
              <span className="font-medium text-slate-100">
                {current.label}
              </span>
              <select
                value={position}
                onChange={(e) =>
                  setPosition(e.target.value as "target" | "cube")
                }
                className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200"
                aria-label="Extraction position"
              >
                <option value="target">
                  at target
                  {current.coversTarget === false
                    ? " (outside footprint!)"
                    : ""}
                </option>
                <option value="cube" disabled={current.cubeRa === null}>
                  at cube centre
                  {current.cubeRa !== null
                    ? ` (${current.cubeRa.toFixed(4)}, ${current.cubeDec?.toFixed(4)})`
                    : ""}
                </option>
              </select>
              {current.kind === "muse" && (
                <button
                  type="button"
                  onClick={() => void extractMuse(current, position)}
                  className="rounded border border-emerald-700 px-2 py-0.5 text-emerald-300 hover:bg-emerald-900/40"
                >
                  extract
                </button>
              )}
              {current.kind === "alma" && (
                <button
                  type="button"
                  onClick={() => void listAlmaProducts(current)}
                  className="rounded border border-emerald-700 px-2 py-0.5 text-emerald-300 hover:bg-emerald-900/40"
                >
                  list products
                </button>
              )}
            </>
          )}
          <label className="flex items-center gap-1 text-slate-400">
            aperture r
            <input
              type="number"
              min="0.2"
              max="30"
              step="0.5"
              value={radiusInput}
              onChange={(e) => setRadiusInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && applyRadius()}
              className="w-14 rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-100"
              aria-label="Aperture radius in arcseconds"
            />
            ″
            <button
              type="button"
              onClick={applyRadius}
              className="rounded border border-slate-700 px-1.5 py-0.5 text-slate-200 hover:bg-slate-800"
            >
              apply
            </button>
          </label>
          <select
            value={method}
            onChange={(e) => setMethod(e.target.value as "sum" | "mean")}
            className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200"
            aria-label="Aperture combination"
          >
            <option value="sum">sum over aperture</option>
            <option value="mean">mean per spaxel</option>
          </select>
          {cache && (
            <span
              className="ml-auto flex items-center gap-1 text-slate-500"
              title={`ALMA cubes that had to be downloaded in full are kept in ${cache.dir} so a second extraction is instant; the oldest are deleted beyond ${(cache.limit_mb / 1000).toFixed(1)} GB. SODA cutouts and MUSE cutouts are never written to disk.`}
            >
              cube cache{" "}
              {cache.files === 0
                ? "empty"
                : `${cache.files} file${cache.files === 1 ? "" : "s"}, ${cache.bytes_mb >= 1000 ? `${(cache.bytes_mb / 1000).toFixed(2)} GB` : `${cache.bytes_mb.toFixed(0)} MB`}`}
              {cache.files > 0 && (
                <button
                  type="button"
                  onClick={() =>
                    void clearCubeCache()
                      .then(setCache)
                      .catch((e: unknown) =>
                        setError(e instanceof ApiError ? e.message : String(e)),
                      )
                  }
                  className="rounded border border-slate-700 px-1.5 py-0.5 text-slate-200 hover:bg-rose-900/40 hover:text-rose-200"
                  title="Delete every downloaded cube from disk"
                >
                  clear cache
                </button>
              )}
            </span>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-1 text-slate-300">
            <input
              type="radio"
              name="xmode"
              checked={xMode === "wavelength"}
              onChange={() => setXMode("wavelength")}
            />{" "}
            wavelength
          </label>
          <label className="flex items-center gap-1 text-slate-300">
            <input
              type="radio"
              name="xmode"
              checked={xMode === "velocity"}
              onChange={() => setXMode("velocity")}
            />{" "}
            velocity
          </label>
          <span className="text-slate-500">optical line</span>
          <select
            value={refLine}
            onChange={(e) => setRefLine(e.target.value)}
            disabled={xMode !== "velocity"}
            className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200 disabled:opacity-50"
            aria-label="Reference line"
          >
            {[...new Map(lines.map((l) => [l.name, l])).keys()].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
            {!lines.length && <option value="Hα">Hα</option>}
          </select>
          <span className="text-slate-500">radio line</span>
          <select
            value={refRadio}
            onChange={(e) => setRefRadio(e.target.value)}
            disabled={xMode !== "velocity"}
            className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200 disabled:opacity-50"
            aria-label="Radio reference line"
          >
            {radioLines.map((l) => (
              <option key={l.name} value={l.name}>
                {l.name}
              </option>
            ))}
            {!radioLines.length && <option value="CO(2-1)">CO(2-1)</option>}
          </select>
          <label className="flex items-center gap-1 text-slate-300">
            <input
              type="checkbox"
              checked={relative}
              onChange={(e) => setRelative(e.target.checked)}
              disabled={xMode !== "velocity" || cz === null}
            />{" "}
            relative to cz
          </label>
          <label
            className="flex items-center gap-1 text-slate-400"
            title="Systemic velocity used for the redshift of lines and the relative velocity axis"
          >
            cz
            <input
              type="number"
              step="1"
              value={manualCz}
              onChange={(e) => setManualCz(e.target.value)}
              placeholder={cz !== null ? cz.toFixed(0) : "unknown"}
              className="w-20 rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-100"
              aria-label="Systemic velocity in km/s"
            />
            km/s
            {czSource && czSource !== "manual" ? (
              <span className="text-slate-600">({czSource})</span>
            ) : null}
          </label>
          <label
            className="flex items-center gap-1 text-slate-300"
            title="Scale each trace to its own peak inside the window: shapes compare, fluxes do not"
          >
            <input
              type="checkbox"
              checked={normalised}
              onChange={(e) => setNormalised(e.target.checked)}
              disabled={xMode !== "velocity"}
            />{" "}
            normalise amplitudes
          </label>
          <label className="flex items-center gap-1 text-slate-400">
            ± window
            <input
              type="number"
              min="100"
              max="20000"
              step="100"
              value={window}
              onChange={(e) => setWindow(Number(e.target.value) || 1500)}
              disabled={xMode !== "velocity"}
              className="w-16 rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-100"
              aria-label="Velocity window in km/s"
            />
            km/s
          </label>
          <span className="text-slate-600">|</span>
          <label className="flex items-center gap-1 text-slate-300">
            <input
              type="checkbox"
              checked={showLines}
              onChange={(e) => setShowLines(e.target.checked)}
            />{" "}
            lines
          </label>
          <select
            value={lineKinds}
            onChange={(e) => setLineKinds(e.target.value as typeof lineKinds)}
            className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200"
            aria-label="Line kinds"
          >
            <option value="all">emission + absorption</option>
            <option value="emission">emission only</option>
            <option value="absorption">absorption only</option>
          </select>
          <span className="text-slate-600">|</span>
          <label
            className="flex items-center gap-1 text-slate-300"
            title="Overplot a 21 cm profile (velocity mode)"
          >
            <input
              type="checkbox"
              checked={hiTemplateOn}
              onChange={(e) => {
                setHITemplateOn(e.target.checked);
                if (e.target.checked) setXMode("velocity");
              }}
            />{" "}
            HI template
          </label>
          <label className="cursor-pointer rounded border border-slate-700 px-1.5 py-0.5 text-slate-200 hover:bg-slate-800">
            upload HI (v, S)
            <input
              type="file"
              accept=".txt,.dat,.csv,.asc,.tsv"
              className="hidden"
              onChange={(e) => onUpload(e.target.files?.[0])}
            />
          </label>
        </div>

        {loading && (
          <div className="space-y-1" data-testid="progress">
            <p className="text-slate-400">
              {loading}
              {progress && (
                <span className="text-slate-500">{` · ${Math.max(0, Math.round((Date.now() - progress.started) / 1000))} s`}</span>
              )}
            </p>
            {progress && (
              <div className="flex items-center gap-2">
                <div className="h-2 w-full max-w-md overflow-hidden rounded bg-slate-800">
                  {progress.state === "downloading" && progress.totalMb ? (
                    <div
                      className="h-full bg-emerald-500 transition-[width] duration-500"
                      style={{
                        width: `${Math.min(100, (100 * progress.downloadedMb) / progress.totalMb).toFixed(1)}%`,
                      }}
                    />
                  ) : (
                    <div className="h-full w-1/3 animate-pulse bg-sky-500/70" />
                  )}
                </div>
                <span className="whitespace-nowrap text-slate-400">
                  {progress.state === "downloading" && progress.totalMb
                    ? `${progress.downloadedMb.toFixed(0)} / ${progress.totalMb.toFixed(0)} MB (${((100 * progress.downloadedMb) / progress.totalMb).toFixed(0)}%)`
                    : progress.state === "downloading"
                      ? `${progress.downloadedMb.toFixed(0)} MB`
                      : progress.state === "extracting"
                        ? "extracting"
                        : progress.state === "soda"
                          ? "waiting for ESO"
                          : progress.state === "cutout"
                            ? "waiting for ALMA"
                            : "starting"}
                </span>
              </div>
            )}
          </div>
        )}
        {error && <p className="text-rose-300">{error}</p>}
        {downloadOffer && !loading && (
          <p className="flex flex-wrap items-center gap-2 text-slate-400">
            The ALMA SODA service did not serve a cutout of this product (see
            above). Run
            <code className="rounded bg-slate-800 px-1 text-[11px] text-slate-200">
              backend/scripts/alma_soda_diag.py "{downloadOffer.product.url}"{" "}
              {target?.coordinates.ra_deg.toFixed(5)}{" "}
              {target?.coordinates.dec_deg.toFixed(5)} {radius}
            </code>
            to see each mirror's reply, or
            <button
              type="button"
              onClick={() =>
                void extractAlma(
                  downloadOffer.req,
                  downloadOffer.product,
                  position,
                  "download",
                )
              }
              className="rounded border border-amber-700 px-1.5 py-0.5 text-amber-300 hover:bg-amber-900/40"
            >
              download the full product (
              {downloadOffer.product.size_mb &&
              downloadOffer.product.size_mb >= 1000
                ? `${(downloadOffer.product.size_mb / 1000).toFixed(1)} GB`
                : `${downloadOffer.product.size_mb ?? "?"} MB`}
              ) instead
            </button>
          </p>
        )}
        {hiError && hiTemplateOn && (
          <p className="text-amber-300">HI profile: {hiError}</p>
        )}
        {traces.flatMap((t) =>
          t.spectrum.warnings.map((w, i) => (
            <p key={`${t.id}-${i}`} className="text-amber-300">
              {t.label}: {w}
            </p>
          )),
        )}

        {figure && active && (
          <Plot
            data={figure.data}
            layout={figure.layout}
            config={{ displaylogo: false, responsive: true }}
            style={{ width: "100%" }}
            useResizeHandler
            onError={(e) => {
              console.error("Plotly render error", e);
              setError(
                `Plot error: ${e instanceof Error ? e.message : String(e)}`,
              );
            }}
          />
        )}
        <PlottedList
          items={plotted}
          onToggle={togglePlotted}
          onRemove={removePlotted}
          compact
        />
        {xMode === "velocity" && normalised && traces.length > 1 && (
          <p className="text-[11px] text-slate-500">
            Amplitudes are normalised independently for a shape comparison:
            fluxes, angular resolutions and calibrations differ between traces.
            Optical velocities use the optical convention about{" "}
            {reference?.name ?? "Hα"}; radio velocities the radio convention
            about {referenceRadio?.name ?? "CO(2-1)"}.
          </p>
        )}
        {traces.length > 0 && (
          <p className="text-[11px] text-slate-500">
            {traces
              .map(
                (t) =>
                  `${t.label}: ${t.spectrum.n_channels} ch, ${t.spectrum.n_spaxels} spx, pixel ${t.spectrum.pixel_scale_arcsec?.toFixed(2)}″${t.spectrum.source.access ? `, ${t.spectrum.source.access}` : ""}${t.spectrum.source.band_ghz ? ` ${t.spectrum.source.band_ghz} GHz` : ""}${t.spectrum.cached ? ", cached" : ""}`,
              )
              .join(" · ")}
            {hiTemplateOn && hiProfile && xMode === "velocity"
              ? ` · ${hiProfile.note}`
              : ""}
          </p>
        )}
      </div>
    </div>
  );
}
