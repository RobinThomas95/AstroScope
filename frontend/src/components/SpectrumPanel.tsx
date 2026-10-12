import { useEffect, useMemo, useRef, useState } from "react";
import Plotly from "plotly.js-basic-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { Annotation as PlotlyAnnotation, Data, Layout, Shape } from "plotly.js";
import { ApiError, fetchHIProfile, fetchLines, fetchMuseSpectrum } from "../lib/api";
import type { HIProfile, ResolvedTarget, SpectralLine, Spectrum1D, SpectrumRequest } from "../types";

const Plot = createPlotlyComponent(Plotly);
const C_KMS = 299792.458;

interface Props {
  target: ResolvedTarget | null;
  request: SpectrumRequest | null;
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

export default function SpectrumPanel({ target, request }: Props) {
  const [radius, setRadius] = useState(3);
  const [radiusInput, setRadiusInput] = useState("3");
  const [method, setMethod] = useState<"sum" | "mean">("sum");
  const [spectrum, setSpectrum] = useState<Spectrum1D | null>(null);
  const [lines, setLines] = useState<SpectralLine[]>([]);
  const [showLines, setShowLines] = useState(true);
  const [lineKinds, setLineKinds] = useState<"all" | "emission" | "absorption">("all");
  const [xMode, setXMode] = useState<XMode>("wavelength");
  const [refLine, setRefLine] = useState("Hα");
  const [relative, setRelative] = useState(true);
  const [showHI, setShowHI] = useState(false);
  const [hiProfile, setHIProfile] = useState<HIProfile | null>(null);
  const [hiError, setHIError] = useState<string | null>(null);
  const [uploaded, setUploaded] = useState<UploadedHI | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const z = target?.kinematics.redshift ?? null;
  const cz = target?.kinematics.velocity_kms ?? (z !== null ? z * C_KMS : null);

  // Extract whenever the request, aperture or method changes.
  useEffect(() => {
    abortRef.current?.abort();
    if (!request || !target) {
      setSpectrum(null);
      return;
    }
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setError(null);
    fetchMuseSpectrum(
      {
        datalinkUrl: request.datalinkUrl,
        ra: target.coordinates.ra_deg,
        dec: target.coordinates.dec_deg,
        radiusArcsec: radius,
        method,
      },
      ac.signal,
    )
      .then((s) => !ac.signal.aborted && setSpectrum(s))
      .catch((e: unknown) => {
        if (ac.signal.aborted) return;
        setError(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => !ac.signal.aborted && setLoading(false));
    return () => ac.abort();
  }, [request, target, radius, method]);

  // Line list for the spectrum's window.
  useEffect(() => {
    if (!spectrum) return;
    const wl = spectrum.wavelength_A;
    const ac = new AbortController();
    fetchLines(z, wl[0], wl[wl.length - 1], ac.signal)
      .then((l) => !ac.signal.aborted && setLines(l))
      .catch(() => undefined);
    return () => ac.abort();
  }, [spectrum, z]);

  // HI template profile on demand.
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

  // Reset target-bound state when the target changes.
  useEffect(() => {
    setHIProfile(null);
    setUploaded(null);
  }, [target]);

  const reference = useMemo(
    () => lines.find((l) => l.name === refLine) ?? lines.find((l) => l.name === "Hα") ?? null,
    [lines, refLine],
  );

  const toX = (wl: number): number => {
    if (xMode === "wavelength" || !reference) return wl;
    const v = C_KMS * (wl / reference.rest_wavelength_A - 1);
    return relative && cz !== null ? v - cz : v;
  };
  const hiX = (v: number): number => (relative && cz !== null ? v - cz : v);

  const figure = useMemo(() => {
    if (!spectrum) return null;
    const x = spectrum.wavelength_A.map(toX);
    const data: Data[] = [
      {
        x,
        y: spectrum.flux,
        type: "scatter",
        mode: "lines",
        name: `MUSE ${spectrum.method} (r=${spectrum.aperture_radius_arcsec}″, ${spectrum.n_spaxels} spx)`,
        line: { color: "#7dd3fc", width: 1 },
        hovertemplate:
          xMode === "wavelength" ? "%{x:.1f} Å<br>%{y:.3g}<extra></extra>" : "%{x:.0f} km/s<br>%{y:.3g}<extra></extra>",
      },
    ];
    if (spectrum.error) {
      const upper = spectrum.flux.map((f, i) =>
        f === null || spectrum.error![i] === null ? null : f + (spectrum.error![i] as number),
      );
      const lower = spectrum.flux.map((f, i) =>
        f === null || spectrum.error![i] === null ? null : f - (spectrum.error![i] as number),
      );
      data.push(
        { x, y: upper, type: "scatter", mode: "lines", line: { width: 0 }, showlegend: false, hoverinfo: "skip" },
        {
          x,
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
    const shapes: Partial<Shape>[] = [];
    const annotations: Partial<PlotlyAnnotation>[] = [];
    if (showLines) {
      for (const l of lines) {
        if (l.observed_wavelength_A === null) continue;
        if (lineKinds !== "all" && l.kind !== "both" && l.kind !== lineKinds) continue;
        const lx = toX(l.observed_wavelength_A);
        const color = l.kind === "absorption" ? "#f59e0b" : l.kind === "emission" ? "#f472b6" : "#a3e635";
        shapes.push({ type: "line", xref: "x", yref: "paper", x0: lx, x1: lx, y0: 0, y1: 1, line: { color, width: 1, dash: "dot" } });
        annotations.push({ x: lx, y: 1, xref: "x", yref: "paper", text: l.name, showarrow: false, textangle: -90, yanchor: "top", xanchor: "left", font: { size: 9, color } });
      }
    }
    if (xMode === "velocity" && cz !== null) {
      const sx = relative ? 0 : cz;
      shapes.push({ type: "line", xref: "x", yref: "paper", x0: sx, x1: sx, y0: 0, y1: 1, line: { color: "#fde047", width: 1 } });
    }
    const hiTraces: Data[] = [];
    if (xMode === "velocity" && showHI) {
      if (hiProfile) {
        hiTraces.push({
          x: hiProfile.velocity_kms.map(hiX),
          y: hiProfile.flux_jy,
          type: "scatter",
          mode: "lines",
          name: `HI template (${hiProfile.survey ?? "catalogue"} ${hiProfile.source_id ?? ""})`,
          yaxis: "y2",
          line: { color: "#fb923c", width: 1.5, dash: "dash" },
          hovertemplate: "%{x:.0f} km/s<br>%{y:.3f} Jy<extra>HI template</extra>",
        });
      }
      if (uploaded) {
        hiTraces.push({
          x: uploaded.velocity.map(hiX),
          y: uploaded.flux,
          type: "scatter",
          mode: "lines",
          name: `HI ${uploaded.name}`,
          yaxis: "y2",
          line: { color: "#fbbf24", width: 1.5 },
          hovertemplate: "%{x:.0f} km/s<br>%{y:.3f}<extra>HI</extra>",
        });
      }
    }
    const layout: Partial<Layout> = {
      autosize: true,
      height: 340,
      margin: { l: 60, r: hiTraces.length ? 60 : 20, t: 30, b: 45 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(2,6,23,0.6)",
      font: { color: "#cbd5e1", size: 11 },
      xaxis: {
        title: {
          text:
            xMode === "wavelength"
              ? "Observed wavelength (Å)"
              : `Velocity (km/s)${relative ? " relative to systemic" : ""}, from ${reference?.name ?? "Hα"}`,
        },
        gridcolor: "#1e293b",
        zeroline: false,
        // In velocity mode the full MUSE range spans ~10^5 km/s; start zoomed on the line.
        ...(xMode === "velocity"
          ? { range: [(relative || cz === null ? 0 : cz) - 1500, (relative || cz === null ? 0 : cz) + 1500] }
          : {}),
      },
      yaxis: { title: { text: spectrum.flux_unit }, gridcolor: "#1e293b", zeroline: false },
      legend: { orientation: "h", y: -0.2, font: { size: 10 } },
      shapes,
      annotations,
      hovermode: "x unified",
    };
    if (hiTraces.length) {
      layout.yaxis2 = {
        title: { text: "HI flux density (Jy)" },
        overlaying: "y",
        side: "right",
        showgrid: false,
        zeroline: false,
      };
    }
    return { data: [...data, ...hiTraces], layout };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spectrum, lines, showLines, lineKinds, xMode, reference, relative, cz, showHI, hiProfile, uploaded]);

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

  if (!request) {
    return (
      <p className="px-3 py-3 text-xs text-slate-500">
        Pick a public MUSE cube in the observation list and press “spectrum” to extract an
        integrated 1-D spectrum at the target position (ESO DataLink / SODA cutout).
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium text-slate-100">{request.label}</span>
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
          <button type="button" onClick={applyRadius} className="rounded border border-slate-700 px-1.5 py-0.5 text-slate-200 hover:bg-slate-800">
            apply
          </button>
        </label>
        <select value={method} onChange={(e) => setMethod(e.target.value as "sum" | "mean")} className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200" aria-label="Aperture combination">
          <option value="sum">sum over aperture</option>
          <option value="mean">mean per spaxel</option>
        </select>
        <label className="flex items-center gap-1 text-slate-300">
          <input type="checkbox" checked={showLines} onChange={(e) => setShowLines(e.target.checked)} /> lines
        </label>
        <select value={lineKinds} onChange={(e) => setLineKinds(e.target.value as typeof lineKinds)} className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200" aria-label="Line kinds">
          <option value="all">emission + absorption</option>
          <option value="emission">emission only</option>
          <option value="absorption">absorption only</option>
        </select>
        <span className="text-slate-600">|</span>
        <label className="flex items-center gap-1 text-slate-300">
          <input type="radio" name="xmode" checked={xMode === "wavelength"} onChange={() => setXMode("wavelength")} /> wavelength
        </label>
        <label className="flex items-center gap-1 text-slate-300">
          <input type="radio" name="xmode" checked={xMode === "velocity"} onChange={() => setXMode("velocity")} /> velocity about
        </label>
        <select value={refLine} onChange={(e) => setRefLine(e.target.value)} disabled={xMode !== "velocity"} className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200 disabled:opacity-50" aria-label="Reference line">
          {[...new Map(lines.map((l) => [l.name, l])).keys()].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
          {!lines.length && <option value="Hα">Hα</option>}
        </select>
        <label className="flex items-center gap-1 text-slate-300">
          <input type="checkbox" checked={relative} onChange={(e) => setRelative(e.target.checked)} disabled={xMode !== "velocity" || cz === null} /> relative to cz{cz !== null ? ` = ${cz.toFixed(0)}` : ""}
        </label>
        <span className="text-slate-600">|</span>
        <label className="flex items-center gap-1 text-slate-300" title="Overplot a 21 cm profile (velocity mode)">
          <input type="checkbox" checked={showHI} onChange={(e) => { setShowHI(e.target.checked); if (e.target.checked) setXMode("velocity"); }} /> HI profile
        </label>
        <label className="cursor-pointer rounded border border-slate-700 px-1.5 py-0.5 text-slate-200 hover:bg-slate-800">
          upload HI (v, S)
          <input type="file" accept=".txt,.dat,.csv,.asc,.tsv" className="hidden" onChange={(e) => onUpload(e.target.files?.[0])} />
        </label>
      </div>

      {loading && <p className="text-slate-400">Requesting SODA cutout and extracting… (first call can take a minute)</p>}
      {error && <p className="text-rose-300">{error}</p>}
      {hiError && showHI && <p className="text-amber-300">HI profile: {hiError}</p>}
      {spectrum?.warnings.map((w, i) => (
        <p key={i} className="text-amber-300">{w}</p>
      ))}

      {figure && (
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
      {spectrum && (
        <p className="text-[11px] text-slate-500">
          {spectrum.n_channels} channels, {spectrum.wavelength_A[0].toFixed(0)}–{spectrum.wavelength_A[spectrum.wavelength_A.length - 1].toFixed(0)} Å ·
          pixel {spectrum.pixel_scale_arcsec?.toFixed(2)}″ · {String(spectrum.source.object ?? "")} {String(spectrum.source.date_obs ?? "")}
          {spectrum.cached ? " · cached" : ""}
          {showHI && hiProfile && xMode === "velocity" ? ` · ${hiProfile.note}` : ""}
        </p>
      )}
    </div>
  );
}
