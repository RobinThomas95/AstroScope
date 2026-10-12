import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, searchArchives, searchHI } from "../lib/api";
import type {
  ArchiveObservation,
  ArchiveSearch,
  HISearch,
  ResolvedTarget,
} from "../types";

interface Props {
  target: ResolvedTarget | null;
  radiusArcmin: number;
  onRadiusChange: (r: number) => void;
}

type Tab = "obs" | "hi";
type SortKey = "date" | "exptime" | "sep" | "instrument";

function num(v: number | null | undefined, digits = 1, suffix = ""): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}${suffix}`;
}

function instrumentKey(o: ArchiveObservation): string {
  return o.instrument ?? o.collection ?? o.archive.toUpperCase();
}

function Links({ o }: { o: ArchiveObservation }) {
  const link = (href: string | null, label: string) =>
    href ? (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className="rounded border border-slate-700 px-1.5 py-0.5 text-[11px] text-sky-300 hover:bg-slate-800"
      >
        {label}
      </a>
    ) : null;
  return (
    <span className="flex flex-wrap gap-1">
      {link(o.preview_url, "preview")}
      {link(o.access_url, "data")}
      {link(o.archive_url, "archive")}
    </span>
  );
}

export default function ArchivePanel({ target, radiusArcmin, onRadiusChange }: Props) {
  const [tab, setTab] = useState<Tab>("obs");
  const [archives, setArchives] = useState<ArchiveSearch | null>(null);
  const [hi, setHI] = useState<HISearch | null>(null);
  const [loadingObs, setLoadingObs] = useState(false);
  const [loadingHI, setLoadingHI] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [instrumentFilter, setInstrumentFilter] = useState<Set<string>>(new Set());
  const [sortKey, setSortKey] = useState<SortKey>("date");
  const [collapsed, setCollapsed] = useState(false);
  const [radiusInput, setRadiusInput] = useState(String(radiusArcmin));
  const abortRef = useRef<AbortController | null>(null);

  // Query both services whenever the target or radius changes.
  useEffect(() => {
    abortRef.current?.abort();
    if (!target) {
      setArchives(null);
      setHI(null);
      return;
    }
    const ac = new AbortController();
    abortRef.current = ac;
    const { ra_deg: ra, dec_deg: dec } = target.coordinates;
    setError(null);
    setLoadingObs(true);
    setLoadingHI(true);
    setInstrumentFilter(new Set());
    searchArchives(ra, dec, radiusArcmin, ac.signal)
      .then((r) => !ac.signal.aborted && setArchives(r))
      .catch((e: unknown) => {
        if (ac.signal.aborted) return;
        setError(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => !ac.signal.aborted && setLoadingObs(false));
    searchHI(ra, dec, undefined, ac.signal)
      .then((r) => !ac.signal.aborted && setHI(r))
      .catch((e: unknown) => {
        if (ac.signal.aborted) return;
        setError(e instanceof ApiError ? e.message : String(e));
      })
      .finally(() => !ac.signal.aborted && setLoadingHI(false));
    return () => ac.abort();
  }, [target, radiusArcmin]);

  const allObs = useMemo(
    () => (archives ? archives.results.flatMap((r) => r.observations) : []),
    [archives],
  );

  const observations = useMemo(() => {
    const rows = instrumentFilter.size
      ? allObs.filter((o) => instrumentFilter.has(instrumentKey(o)))
      : allObs.slice();
    const cmp: Record<SortKey, (a: ArchiveObservation, b: ArchiveObservation) => number> = {
      date: (a, b) => (b.mjd_obs ?? -Infinity) - (a.mjd_obs ?? -Infinity),
      exptime: (a, b) => (b.exptime_s ?? -1) - (a.exptime_s ?? -1),
      sep: (a, b) => (a.separation_arcmin ?? Infinity) - (b.separation_arcmin ?? Infinity),
      instrument: (a, b) => instrumentKey(a).localeCompare(instrumentKey(b)),
    };
    return rows.sort(cmp[sortKey]);
  }, [allObs, instrumentFilter, sortKey]);

  const toggleInstrument = (name: string) =>
    setInstrumentFilter((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  const applyRadius = () => {
    const r = parseFloat(radiusInput);
    if (Number.isFinite(r) && r > 0 && r <= 60) onRadiusChange(r);
  };

  const hiDetections = hi ? hi.results.flatMap((r) => r.detections) : [];

  return (
    <div className="flex min-h-0 flex-col rounded-lg border border-slate-800 bg-slate-900/60">
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-3 py-1.5 text-xs">
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          className="rounded px-1 text-slate-400 hover:bg-slate-800"
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? "▸" : "▾"}
        </button>
        {(["obs", "hi"] as Tab[]).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`rounded px-2 py-0.5 ${
              tab === t ? "bg-slate-800 text-white" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {t === "obs"
              ? `Archive observations${archives ? ` (${allObs.length})` : ""}`
              : `HI 21 cm${hi ? ` (${hiDetections.length})` : ""}`}
          </button>
        ))}
        <span className="ml-2 text-slate-500">
          {loadingObs || loadingHI ? "querying…" : target ? `around ${target.main_id ?? target.query}` : "no target"}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <label className="text-slate-400">radius</label>
          <input
            type="number"
            min="0.5"
            max="60"
            step="0.5"
            value={radiusInput}
            onChange={(e) => setRadiusInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyRadius()}
            className="w-16 rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-100"
            aria-label="Archive search radius in arcminutes"
          />
          <span className="text-slate-500">′</span>
          <button
            type="button"
            onClick={applyRadius}
            className="rounded border border-slate-700 px-2 py-0.5 text-slate-200 hover:bg-slate-800"
          >
            Search
          </button>
        </div>
      </div>

      {!collapsed && (
        <div className="min-h-0 flex-1 overflow-auto">
          {error && <p className="px-3 py-2 text-xs text-rose-300">{error}</p>}
          {!target && (
            <p className="px-3 py-3 text-xs text-slate-500">
              Resolve a target to cross-match the ESO, MAST and ALMA archives and HI surveys.
            </p>
          )}

          {tab === "obs" && archives && (
            <>
              <div className="flex flex-wrap items-center gap-1 px-3 py-1.5 text-[11px]">
                {archives.results.map((r) => (
                  <span
                    key={r.archive}
                    title={r.error ?? r.table ?? ""}
                    className={`rounded px-1.5 py-0.5 ${
                      r.status === "ok"
                        ? "bg-emerald-900/50 text-emerald-300"
                        : "bg-rose-900/50 text-rose-300"
                    }`}
                  >
                    {r.label}: {r.status === "ok" ? `${r.count}${r.truncated ? "+" : ""}` : "error"}
                  </span>
                ))}
                <span className="mx-1 text-slate-600">|</span>
                {Object.entries(archives.instrument_counts).map(([name, count]) => (
                  <button
                    key={name}
                    type="button"
                    onClick={() => toggleInstrument(name)}
                    className={`rounded border px-1.5 py-0.5 ${
                      instrumentFilter.has(name)
                        ? "border-sky-500 bg-sky-900/40 text-sky-200"
                        : "border-slate-700 text-slate-300 hover:bg-slate-800"
                    }`}
                  >
                    {name} <span className="text-slate-500">{count}</span>
                  </button>
                ))}
                {instrumentFilter.size > 0 && (
                  <button
                    type="button"
                    onClick={() => setInstrumentFilter(new Set())}
                    className="text-slate-400 underline"
                  >
                    clear
                  </button>
                )}
                <select
                  value={sortKey}
                  onChange={(e) => setSortKey(e.target.value as SortKey)}
                  className="ml-auto rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-200"
                  aria-label="Sort observations"
                >
                  <option value="date">newest first</option>
                  <option value="exptime">longest exposure</option>
                  <option value="sep">closest</option>
                  <option value="instrument">by instrument</option>
                </select>
              </div>
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-slate-900 text-[10px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-2 py-1">Instrument</th>
                    <th className="px-2 py-1">Obs ID</th>
                    <th className="px-2 py-1">Date (UTC)</th>
                    <th className="px-2 py-1 text-right">Exp (s)</th>
                    <th className="px-2 py-1">Band</th>
                    <th className="px-2 py-1 text-right">Res (″)</th>
                    <th className="px-2 py-1 text-right">Sep (′)</th>
                    <th className="px-2 py-1">Type</th>
                    <th className="px-2 py-1">Links</th>
                  </tr>
                </thead>
                <tbody>
                  {observations.map((o, i) => (
                    <tr
                      key={`${o.archive}-${o.obs_id ?? i}`}
                      className="border-t border-slate-800/60 align-top hover:bg-slate-800/40"
                    >
                      <td className="px-2 py-1 whitespace-nowrap">
                        <span className="font-medium text-slate-100">{instrumentKey(o)}</span>
                        {o.collection && o.collection !== o.instrument && (
                          <span className="ml-1 text-slate-500">{o.collection}</span>
                        )}
                        {o.public === false && (
                          <span className="ml-1 rounded bg-amber-900/50 px-1 text-[10px] text-amber-300">
                            proprietary
                          </span>
                        )}
                      </td>
                      <td className="max-w-[14rem] truncate px-2 py-1 font-mono text-[11px] text-slate-300" title={o.obs_id ?? ""}>
                        {o.obs_id ?? "—"}
                        {o.target_name && <div className="truncate text-slate-500">{o.target_name}</div>}
                      </td>
                      <td className="px-2 py-1 font-mono text-[11px] text-slate-300">
                        {o.date_obs ? o.date_obs.replace("T", " ").replace("Z", "") : "—"}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-[11px]">{num(o.exptime_s, 0)}</td>
                      <td className="px-2 py-1 whitespace-nowrap text-slate-300">{o.band ?? "—"}</td>
                      <td className="px-2 py-1 text-right font-mono text-[11px]">{num(o.resolution_arcsec, 2)}</td>
                      <td className="px-2 py-1 text-right font-mono text-[11px]">{num(o.separation_arcmin, 2)}</td>
                      <td className="px-2 py-1 text-slate-400">
                        {o.dataproduct_type ?? "—"}
                        {o.calib_level !== null && <span className="text-slate-600"> L{o.calib_level}</span>}
                      </td>
                      <td className="px-2 py-1">
                        <Links o={o} />
                      </td>
                    </tr>
                  ))}
                  {observations.length === 0 && !loadingObs && (
                    <tr>
                      <td colSpan={9} className="px-3 py-3 text-slate-500">
                        No observations within {archives.radius_arcmin}′.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </>
          )}

          {tab === "hi" && hi && (
            <>
              <div className="flex flex-wrap items-center gap-1 px-3 py-1.5 text-[11px]">
                {hi.results.map((r) => (
                  <span
                    key={r.catalog}
                    title={r.error ?? r.catalog}
                    className={`rounded px-1.5 py-0.5 ${
                      r.status === "ok"
                        ? "bg-emerald-900/50 text-emerald-300"
                        : "bg-rose-900/50 text-rose-300"
                    }`}
                  >
                    {r.survey}: {r.status === "ok" ? r.detections.length : "error"}
                  </span>
                ))}
                <span className="ml-auto text-slate-500">search radius {hi.radius_arcmin.toFixed(1)}′ (half beam, min 3′)</span>
              </div>
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-slate-900 text-[10px] uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-2 py-1">Survey</th>
                    <th className="px-2 py-1">Source</th>
                    <th className="px-2 py-1 text-right">Sep (′)</th>
                    <th className="px-2 py-1 text-right">cz (km/s)</th>
                    <th className="px-2 py-1 text-right">W50</th>
                    <th className="px-2 py-1 text-right">W20</th>
                    <th className="px-2 py-1 text-right">S (Jy km/s)</th>
                    <th className="px-2 py-1 text-right">S/N</th>
                    <th className="px-2 py-1 text-right">log M_HI</th>
                    <th className="px-2 py-1"></th>
                  </tr>
                </thead>
                <tbody>
                  {hiDetections.map((d, i) => (
                    <tr key={`${d.catalog}-${d.source_id ?? i}`} className="border-t border-slate-800/60 hover:bg-slate-800/40">
                      <td className="px-2 py-1 text-slate-300">{d.survey}</td>
                      <td className="px-2 py-1 font-mono text-[11px] text-slate-100">
                        {d.source_id ?? "—"}
                        {d.name && d.name !== d.source_id && <span className="ml-1 text-slate-500">{d.name}</span>}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-[11px]">{num(d.separation_arcmin, 2)}</td>
                      <td className="px-2 py-1 text-right font-mono text-[11px]">
                        {num(d.velocity_kms, 0)}
                        {d.velocity_err_kms !== null && <span className="text-slate-500"> ±{num(d.velocity_err_kms, 0)}</span>}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-[11px]">
                        {num(d.w50_kms, 0)}
                        {d.w50_err_kms !== null && <span className="text-slate-500"> ±{num(d.w50_err_kms, 0)}</span>}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-[11px]">{num(d.w20_kms, 0)}</td>
                      <td className="px-2 py-1 text-right font-mono text-[11px]">
                        {num(d.flux_jy_kms, 2)}
                        {d.flux_err_jy_kms !== null && <span className="text-slate-500"> ±{num(d.flux_err_jy_kms, 2)}</span>}
                      </td>
                      <td className="px-2 py-1 text-right font-mono text-[11px]">{num(d.snr, 1)}</td>
                      <td className="px-2 py-1 text-right font-mono text-[11px]">{num(d.log_mhi, 2)}</td>
                      <td className="px-2 py-1">
                        {d.vizier_url && (
                          <a href={d.vizier_url} target="_blank" rel="noreferrer" className="text-[11px] text-sky-300">
                            VizieR
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                  {hiDetections.length === 0 && !loadingHI && (
                    <tr>
                      <td colSpan={10} className="px-3 py-3 text-slate-500">
                        No HI detections within the search radius.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  );
}
