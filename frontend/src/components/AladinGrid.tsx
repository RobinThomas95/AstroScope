import { useEffect, useMemo, useRef, useState } from "react";
import { ViewSync, type ViewState } from "../lib/viewSync";
import { fillOverlayTemplate } from "../lib/api";
import type { ResolvedOverlay, ResolvedTarget, SurveyCatalog } from "../types";
import AladinPane, { type TargetMarker } from "./AladinPane";

interface PaneConfig {
  id: string;
  surveyId: string;
  overlays: string[];
}

interface Props {
  catalog: SurveyCatalog;
  target: ResolvedTarget | null;
  /** Radius used for target-bound footprint overlays (arcmin). */
  footprintRadiusArcmin: number;
}

const PANE_IDS = ["nw", "ne", "sw", "se"];

function fovLabel(fovDeg: number): string {
  if (fovDeg >= 1) return `${fovDeg.toFixed(2)}°`;
  const arcmin = fovDeg * 60;
  if (arcmin >= 1) return `${arcmin.toFixed(1)}′`;
  return `${(arcmin * 60).toFixed(0)}″`;
}

function formatRaDec(ra: number, dec: number): string {
  const raH = ra / 15;
  const h = Math.floor(raH);
  const m = Math.floor((raH - h) * 60);
  const s = ((raH - h) * 60 - m) * 60;
  const sign = dec < 0 ? "-" : "+";
  const ad = Math.abs(dec);
  const d = Math.floor(ad);
  const dm = Math.floor((ad - d) * 60);
  const ds = ((ad - d) * 60 - dm) * 60;
  const pad = (n: number, w = 2) => n.toString().padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${s.toFixed(1).padStart(4, "0")} ${sign}${pad(d)}:${pad(dm)}:${ds
    .toFixed(0)
    .padStart(2, "0")}`;
}

export default function AladinGrid({ catalog, target, footprintRadiusArcmin }: Props) {
  const syncRef = useRef<ViewSync | null>(null);
  if (!syncRef.current) {
    syncRef.current = new ViewSync({ ra: 53.4015, dec: -36.1404, fov: 0.5 });
  }
  const sync = syncRef.current;
  if (import.meta.env.DEV) {
    // Debug handle for the browser console / end-to-end tests.
    (window as unknown as { __astroscopeSync?: ViewSync }).__astroscopeSync = sync;
  }

  const [panes, setPanes] = useState<PaneConfig[]>(() =>
    PANE_IDS.map((id, i) => ({
      id,
      surveyId: catalog.default_grid[i] ?? catalog.surveys[i % catalog.surveys.length].id,
      overlays: [],
    })),
  );
  const [synced, setSynced] = useState(true);
  const [view, setView] = useState<ViewState | null>(sync.state);
  const [fovInput, setFovInput] = useState("");

  useEffect(() => sync.subscribe((s) => setView(s)), [sync]);

  useEffect(() => {
    sync.setEnabled(synced);
  }, [sync, synced]);

  // Recentre on every newly resolved target.
  useEffect(() => {
    if (!target) return;
    sync.goTo({
      ra: target.coordinates.ra_deg,
      dec: target.coordinates.dec_deg,
      fov: target.suggested_fov_deg,
    });
  }, [sync, target]);

  const marker = useMemo<TargetMarker | null>(
    () =>
      target
        ? {
            ra: target.coordinates.ra_deg,
            dec: target.coordinates.dec_deg,
            label: target.main_id ?? target.query,
          }
        : null,
    [target],
  );

  const overlays = useMemo<ResolvedOverlay[]>(() => {
    const fixed: ResolvedOverlay[] = catalog.overlays.map((o) => ({
      id: o.id,
      label: o.label,
      color: o.color,
      description: o.description,
      url: o.moc_url,
      targetBound: false,
    }));
    const bound: ResolvedOverlay[] = catalog.target_overlays.map((o) => ({
      id: o.id,
      label: o.label,
      color: o.color,
      description: o.description,
      url: target
        ? fillOverlayTemplate(
            o.moc_url_template,
            target.coordinates.ra_deg,
            target.coordinates.dec_deg,
            footprintRadiusArcmin,
          )
        : null,
      targetBound: true,
    }));
    return [...fixed, ...bound];
  }, [catalog, target, footprintRadiusArcmin]);

  const updatePane = (id: string, patch: Partial<PaneConfig>) =>
    setPanes((prev) => prev.map((p) => (p.id === id ? { ...p, ...patch } : p)));

  const toggleOverlay = (id: string, overlayId: string) =>
    setPanes((prev) =>
      prev.map((p) =>
        p.id === id
          ? {
              ...p,
              overlays: p.overlays.includes(overlayId)
                ? p.overlays.filter((o) => o !== overlayId)
                : [...p.overlays, overlayId],
            }
          : p,
      ),
    );

  const applyFovInput = () => {
    const arcmin = parseFloat(fovInput);
    if (Number.isFinite(arcmin) && arcmin > 0) sync.goTo({ fov: arcmin / 60 });
    setFovInput("");
  };

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2 text-xs">
        <label className="flex cursor-pointer items-center gap-2">
          <input type="checkbox" checked={synced} onChange={(e) => setSynced(e.target.checked)} />
          <span className={synced ? "text-sky-300" : "text-slate-300"}>
            Sync pan / zoom across panes
          </span>
        </label>
        <span className="font-mono text-slate-300">
          {view ? `${formatRaDec(view.ra, view.dec)} · FOV ${fovLabel(view.fov)}` : "—"}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <input
            type="number"
            min="0.1"
            step="0.5"
            value={fovInput}
            onChange={(e) => setFovInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && applyFovInput()}
            placeholder="FOV ′"
            className="w-20 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-100 focus:outline-none"
            aria-label="Field of view in arcminutes"
          />
          <button
            type="button"
            onClick={applyFovInput}
            className="rounded border border-slate-700 px-2 py-1 text-slate-200 hover:bg-slate-800"
          >
            Set FOV
          </button>
          <button
            type="button"
            disabled={!target}
            onClick={() =>
              target &&
              sync.goTo({
                ra: target.coordinates.ra_deg,
                dec: target.coordinates.dec_deg,
                fov: target.suggested_fov_deg,
              })
            }
            className="rounded bg-slate-800 px-2 py-1 text-slate-100 hover:bg-slate-700 disabled:opacity-40"
          >
            Recentre on target
          </button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-4 gap-2 md:grid-cols-2 md:grid-rows-2">
        {panes.map((p) => (
          <AladinPane
            key={p.id}
            id={p.id}
            sync={sync}
            surveys={catalog.surveys}
            overlays={overlays}
            surveyId={p.surveyId}
            enabledOverlays={p.overlays}
            marker={marker}
            onSurveyChange={(surveyId) => updatePane(p.id, { surveyId })}
            onOverlayToggle={(overlayId) => toggleOverlay(p.id, overlayId)}
          />
        ))}
      </div>
    </div>
  );
}
