import { useEffect, useMemo, useRef, useState } from "react";
import Plotly from "plotly.js-basic-dist-min";
import createPlotlyComponent from "react-plotly.js/factory";
import type { Data, Layout } from "plotly.js";
import { ApiError, fetchLiterature, postSynthesis } from "../lib/api";
import type { LiteratureResult, Paper, ResolvedTarget, SynthesisResult } from "../types";

const Plot = createPlotlyComponent(Plotly);

interface Props {
  target: ResolvedTarget | null;
  onOpenKeys: () => void;
  /** Plots are only rendered while the tab is visible (Plotly cannot size a hidden element). */
  active: boolean;
}

function cleanName(n: string): string {
  return n.replace(/\s+/g, " ").trim();
}

/** Aliases worth sending to ADS: catalogue names, not survey designations. */
function searchNames(target: ResolvedTarget): string[] {
  const good = /^(NGC|IC|M|UGC|ESO|MCG|PGC|LEDA|FCC|VCC|Mrk|MRK|ARP|Arp|NAME|3C|4C|PKS|[A-Z]{1,4})\s/;
  const names = new Set<string>();
  for (const a of target.aliases) {
    const n = cleanName(a).replace(/^NAME\s+/, "");
    if (good.test(a) && n.length <= 24 && !/J\d{4,}/.test(n)) names.add(n);
  }
  if (target.ned_name) names.add(cleanName(target.ned_name));
  const main = target.main_id ? cleanName(target.main_id) : target.query;
  names.delete(main);
  return [...names].slice(0, 8);
}

export default function LiteraturePanel({ target, onOpenKeys, active }: Props) {
  const [lit, setLit] = useState<LiteratureResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refereed, setRefereed] = useState(true);
  const [facilityFilter, setFacilityFilter] = useState<Set<string>>(new Set());
  const [sortKey, setSortKey] = useState<"citations" | "year">("citations");
  const [showAll, setShowAll] = useState(false);
  const [synth, setSynth] = useState<SynthesisResult | null>(null);
  const [synthLoading, setSynthLoading] = useState(false);
  const [synthError, setSynthError] = useState<string | null>(null);
  const [maxAbstracts, setMaxAbstracts] = useState(60);
  const abortRef = useRef<AbortController | null>(null);

  const mainName = target ? (target.main_id ? cleanName(target.main_id) : target.query) : "";
  const names = useMemo(() => (target ? searchNames(target) : []), [target]);

  useEffect(() => {
    abortRef.current?.abort();
    setLit(null);
    setSynth(null);
    setSynthError(null);
    setFacilityFilter(new Set());
    if (!target) return;
    const ac = new AbortController();
    abortRef.current = ac;
    setLoading(true);
    setError(null);
    fetchLiterature(mainName, names, { refereed }, ac.signal)
      .then((r) => !ac.signal.aborted && setLit(r))
      .catch((e: unknown) => {
        if (ac.signal.aborted) return;
        setError(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => !ac.signal.aborted && setLoading(false));
    return () => ac.abort();
  }, [target, mainName, names, refereed]);

  const filtered = useMemo<Paper[]>(() => {
    if (!lit) return [];
    const rows = facilityFilter.size
      ? lit.papers.filter((p) => p.facilities.some((f) => facilityFilter.has(f)))
      : lit.papers.slice();
    rows.sort(sortKey === "citations" ? (a, b) => b.citation_count - a.citation_count : (a, b) => (b.year ?? 0) - (a.year ?? 0));
    return rows;
  }, [lit, facilityFilter, sortKey]);

  const yearFigure = useMemo(() => {
    if (!lit) return null;
    const years = lit.metrics.years.map((y) => y.year);
    if (!years.length) return null;
    const y0 = Math.min(...years);
    const y1 = Math.max(...years);
    const axis = Array.from({ length: y1 - y0 + 1 }, (_, i) => y0 + i);
    const all = new Map(lit.metrics.years.map((y) => [y.year, y.count]));
    const data: Data[] = [
      {
        x: axis,
        y: axis.map((y) => all.get(y) ?? 0),
        type: "bar",
        name: facilityFilter.size ? "all papers" : "papers",
        marker: { color: facilityFilter.size ? "rgba(148,163,184,0.35)" : "#7dd3fc" },
        hovertemplate: "%{x}: %{y} papers<extra></extra>",
      },
    ];
    if (facilityFilter.size) {
      const sel = new Map<number, number>();
      for (const p of filtered) if (p.year !== null) sel.set(p.year, (sel.get(p.year) ?? 0) + 1);
      data.push({
        x: axis,
        y: axis.map((y) => sel.get(y) ?? 0),
        type: "bar",
        name: [...facilityFilter].join(" / "),
        marker: { color: "#f472b6" },
        hovertemplate: "%{x}: %{y} papers<extra>" + [...facilityFilter].join("/") + "</extra>",
      });
    }
    const layout: Partial<Layout> = {
      autosize: true,
      height: 220,
      margin: { l: 40, r: 10, t: 10, b: 35 },
      barmode: "overlay",
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(2,6,23,0.6)",
      font: { color: "#cbd5e1", size: 11 },
      xaxis: { title: { text: "Year" }, gridcolor: "#1e293b", dtick: axis.length > 40 ? 10 : 5 },
      yaxis: { title: { text: "Papers" }, gridcolor: "#1e293b" },
      legend: { orientation: "h", y: 1.15, font: { size: 10 } },
      showlegend: facilityFilter.size > 0,
    };
    return { data, layout };
  }, [lit, filtered, facilityFilter]);

  const citeFigure = useMemo(() => {
    if (!lit) return null;
    const bins = lit.metrics.citation_bins;
    const data: Data[] = [
      { x: bins.map((b) => b.label), y: bins.map((b) => b.count), type: "bar", marker: { color: "#a3e635" }, hovertemplate: "%{x} citations: %{y} papers<extra></extra>" },
    ];
    const layout: Partial<Layout> = {
      autosize: true,
      height: 220,
      margin: { l: 40, r: 10, t: 10, b: 35 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(2,6,23,0.6)",
      font: { color: "#cbd5e1", size: 11 },
      xaxis: { title: { text: "Citations per paper" }, type: "category" },
      yaxis: { title: { text: "Papers" }, gridcolor: "#1e293b" },
    };
    return { data, layout };
  }, [lit]);

  const toggleFacility = (name: string) =>
    setFacilityFilter((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const runSynthesis = async () => {
    if (!target) return;
    setSynthLoading(true);
    setSynthError(null);
    try {
      setSynth(await postSynthesis(mainName, names, { refereed, maxAbstracts }));
    } catch (e: unknown) {
      setSynthError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSynthLoading(false);
    }
  };

  if (!target) {
    return <p className="px-3 py-3 text-xs text-slate-500">Resolve a target to search the ADS literature.</p>;
  }

  const byBib = new Map((lit?.papers ?? []).map((p) => [p.bibcode, p]));
  const shown = showAll ? filtered : filtered.slice(0, 30);

  return (
    <div className="flex flex-col gap-3 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium text-slate-100">ADS: {mainName}</span>
        {names.length > 0 && <span className="text-slate-500">+ {names.join(", ")}</span>}
        <label className="flex items-center gap-1 text-slate-300">
          <input type="checkbox" checked={refereed} onChange={(e) => setRefereed(e.target.checked)} /> refereed only
        </label>
        {loading && <span className="text-slate-400">querying ADS…</span>}
        {lit && (
          <span className="text-slate-400">
            {lit.metrics.total} papers{lit.truncated ? ` of ${lit.num_found}` : ""} · {lit.metrics.refereed} refereed ·{" "}
            {lit.metrics.total_citations} citations · h = {lit.metrics.h_index}
            {lit.token_source === "server" ? " · server ADS token" : " · your ADS token"}
          </span>
        )}
        <button type="button" onClick={onOpenKeys} className="ml-auto rounded border border-slate-700 px-2 py-0.5 text-slate-300 hover:bg-slate-800">
          keys
        </button>
      </div>
      {error && (
        <p className="text-rose-300">
          {error}{" "}
          {/token|key/i.test(error) && (
            <button type="button" onClick={onOpenKeys} className="underline">
              open the Keys dialog
            </button>
          )}
        </p>
      )}

      {lit && (
        <>
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-slate-500">facilities:</span>
            {lit.metrics.facilities.slice(0, 24).map((f) => (
              <button
                key={f.name}
                type="button"
                onClick={() => toggleFacility(f.name)}
                className={`rounded border px-1.5 py-0.5 ${facilityFilter.has(f.name) ? "border-pink-500 bg-pink-900/40 text-pink-200" : "border-slate-700 text-slate-300 hover:bg-slate-800"}`}
              >
                {f.name} <span className="text-slate-500">{f.count}</span>
              </button>
            ))}
            {facilityFilter.size > 0 && (
              <button type="button" onClick={() => setFacilityFilter(new Set())} className="text-slate-400 underline">
                clear
              </button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-slate-500">keywords:</span>
            {lit.metrics.keywords.slice(0, 20).map((k) => (
              <span key={k.name} className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">
                {k.name} <span className="text-slate-500">{k.count}</span>
              </span>
            ))}
          </div>

          <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium text-slate-200">
                  Papers{facilityFilter.size ? ` mentioning ${[...facilityFilter].join(" / ")}` : ""} ({filtered.length})
                </span>
                <select value={sortKey} onChange={(e) => setSortKey(e.target.value as "citations" | "year")} className="rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200" aria-label="Sort papers">
                  <option value="citations">most cited</option>
                  <option value="year">newest</option>
                </select>
              </div>
              <table className="w-full text-left">
                <thead className="text-[10px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-1 py-1">Year</th>
                    <th className="px-1 py-1">Title</th>
                    <th className="px-1 py-1">First author</th>
                    <th className="px-1 py-1">Journal</th>
                    <th className="px-1 py-1 text-right">Cites</th>
                    <th className="px-1 py-1">Facilities</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((p) => (
                    <tr key={p.bibcode} className="border-t border-slate-800/60 align-top hover:bg-slate-800/40">
                      <td className="px-1 py-1 font-mono text-[11px]">{p.year ?? "—"}</td>
                      <td className="px-1 py-1">
                        <a href={p.ads_url} target="_blank" rel="noreferrer" className="text-sky-300 hover:underline" title={p.abstract ?? ""}>
                          {p.title ?? p.bibcode}
                        </a>
                        {!p.refereed && <span className="ml-1 rounded bg-slate-800 px-1 text-[10px] text-slate-400">{p.doctype ?? "non-refereed"}</span>}
                      </td>
                      <td className="px-1 py-1 text-slate-300">{p.first_author ?? "—"}</td>
                      <td className="px-1 py-1 text-slate-400">{p.journal ?? "—"}</td>
                      <td className="px-1 py-1 text-right font-mono text-[11px]">{p.citation_count}</td>
                      <td className="px-1 py-1 text-slate-400">{p.facilities.slice(0, 5).join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length > 30 && (
                <button type="button" onClick={() => setShowAll((v) => !v)} className="mt-1 text-slate-400 underline">
                  {showAll ? "show fewer" : `show all ${filtered.length}`}
                </button>
              )}
            </div>
            <div className="min-w-0 xl:sticky xl:top-0 xl:self-start">
              {active && yearFigure && (
                <Plot data={yearFigure.data} layout={yearFigure.layout} config={{ displaylogo: false, responsive: true }} style={{ width: "100%" }} useResizeHandler />
              )}
              {active && citeFigure && (
                <Plot data={citeFigure.data} layout={citeFigure.layout} config={{ displaylogo: false, responsive: true }} style={{ width: "100%" }} useResizeHandler />
              )}
            </div>
          </div>

          <div className="mt-2 rounded-lg border border-slate-800 bg-slate-950/60 p-3">
            <div className="flex flex-wrap items-center gap-3">
              <span className="font-medium text-slate-100">Scientific synthesis (Gemini)</span>
              <label className="flex items-center gap-1 text-slate-400">
                abstracts
                <input type="number" min="5" max="150" value={maxAbstracts} onChange={(e) => setMaxAbstracts(Number(e.target.value) || 60)} className="w-16 rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-100" aria-label="Number of abstracts to synthesise" />
              </label>
              <button type="button" onClick={runSynthesis} disabled={synthLoading || !lit.papers.length} className="rounded bg-emerald-700 px-3 py-1 text-white hover:bg-emerald-600 disabled:opacity-50">
                {synthLoading ? "synthesising…" : synth ? "re-run" : "synthesise"}
              </button>
              {synth && (
                <span className="text-slate-500">
                  {synth.model} · {synth.n_abstracts} abstracts · {synth.prompt_tokens ?? "?"} in / {synth.output_tokens ?? "?"} out tokens
                  {synth.key_source === "server" ? " · server key" : " · your key"}{synth.cached ? " · cached" : ""}
                </span>
              )}
            </div>
            {synthError && (
              <p className="mt-2 text-rose-300">
                {synthError}{" "}
                {/key/i.test(synthError) && (
                  <button type="button" onClick={onOpenKeys} className="underline">
                    open the Keys dialog
                  </button>
                )}
              </p>
            )}
            {synth && (
              <div className="mt-3 grid grid-cols-1 gap-4 text-sm lg:grid-cols-2">
                <div className="lg:col-span-2">
                  <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Summary</h4>
                  <p className="leading-relaxed text-slate-200">{synth.synthesis.summary}</p>
                </div>
                <div>
                  <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-emerald-400">Consensus and established properties</h4>
                  <ul className="list-disc space-y-1 pl-4 text-slate-200">
                    {synth.synthesis.consensus.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
                <div>
                  <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-amber-400">Peculiarities and open questions</h4>
                  <ul className="list-disc space-y-1 pl-4 text-slate-200">
                    {synth.synthesis.open_questions.map((c, i) => <li key={i}>{c}</li>)}
                  </ul>
                </div>
                <div>
                  <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Key papers</h4>
                  <ul className="space-y-1 text-xs">
                    {synth.synthesis.key_papers.map((kp) => {
                      const p = byBib.get(kp.bibcode);
                      return (
                        <li key={kp.bibcode}>
                          <a href={p?.ads_url ?? `https://ui.adsabs.harvard.edu/abs/${kp.bibcode}/abstract`} target="_blank" rel="noreferrer" className="font-mono text-sky-300">
                            {kp.bibcode}
                          </a>
                          {p && <span className="text-slate-400"> {p.first_author} {p.year}, {p.citation_count} cites</span>}
                          <span className="text-slate-200"> — {kp.reason}</span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
                <div>
                  <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Keyword clusters</h4>
                  <ul className="space-y-1 text-xs">
                    {synth.synthesis.keyword_clusters.map((c) => (
                      <li key={c.theme}>
                        <span className="font-medium text-slate-100">{c.theme}</span>
                        <span className="text-slate-400">: {c.keywords.join(", ")}</span>
                        {c.bibcodes.length > 0 && <span className="text-slate-600"> ({c.bibcodes.length} papers)</span>}
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Facilities</h4>
                  <ul className="space-y-1 text-xs">
                    {synth.synthesis.facilities.map((f) => (
                      <li key={f.name}>
                        <span className="font-medium text-slate-100">{f.name}</span>
                        <span className="text-slate-300">: {f.role}</span>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Publication trend</h4>
                  <p className="text-xs text-slate-200">{synth.synthesis.publication_trend}</p>
                </div>
                {synth.warnings.length > 0 && (
                  <p className="text-[11px] text-amber-300 lg:col-span-2">{synth.warnings.join(" · ")}</p>
                )}
                <p className="text-[11px] text-slate-500 lg:col-span-2">
                  Generated by {synth.model} from {synth.n_abstracts} ADS abstracts; statistics above are computed
                  directly from ADS. Verify claims against the cited papers.
                </p>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
