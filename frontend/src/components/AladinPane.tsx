import { useEffect, useRef, useState } from "react";
import type { AladinCatalog, AladinInstance, AladinMOC, AladinStatic } from "aladin-lite";
import { loadAladin } from "../lib/aladin";
import { apiUrl } from "../lib/api";
import type { ViewSync } from "../lib/viewSync";
import type { ResolvedOverlay, Survey } from "../types";

export interface TargetMarker {
  ra: number;
  dec: number;
  label: string;
}

interface Props {
  id: string;
  sync: ViewSync;
  surveys: Survey[];
  overlays: ResolvedOverlay[];
  surveyId: string;
  enabledOverlays: string[];
  marker: TargetMarker | null;
  onSurveyChange: (surveyId: string) => void;
  onOverlayToggle: (overlayId: string) => void;
}

const ALADIN_OPTIONS = {
  cooFrame: "ICRS" as const,
  projection: "SIN",
  showReticle: true,
  reticleColor: "rgba(253, 224, 71, 0.8)",
  showCooGrid: false,
  showCooGridControl: false,
  showLayersControl: false,
  showFullscreenControl: false,
  showSimbadPointerControl: false,
  showProjectionControl: false,
  showZoomControl: false,
  showSettingsControl: false,
  showShareControl: false,
  showStatusBar: false,
  showFrame: false,
  showCooLocation: false,
  showContextMenu: false,
};

export default function AladinPane({
  id,
  sync,
  surveys,
  overlays,
  surveyId,
  enabledOverlays,
  marker,
  onSurveyChange,
  onOverlayToggle,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const aladinRef = useRef<AladinInstance | null>(null);
  const staticRef = useRef<AladinStatic | null>(null);
  const mocsRef = useRef<Map<string, { moc: AladinMOC; url: string }>>(new Map());
  const markerCatRef = useRef<AladinCatalog | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showOverlays, setShowOverlays] = useState(false);
  const [overlayStatus, setOverlayStatus] = useState<
    Record<string, "loading" | "ok" | "error" | "needs-target">
  >({});
  const menuRef = useRef<HTMLDivElement>(null);

  // Close the overlay menu when clicking anywhere else.
  useEffect(() => {
    if (!showOverlays) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setShowOverlays(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [showOverlays]);

  // Create the Aladin instance once and register it with the sync controller.
  useEffect(() => {
    let cancelled = false;
    const el = containerRef.current;
    if (!el) return;

    loadAladin()
      .then((A) => {
        if (cancelled || !containerRef.current) return;
        const initial = sync.state;
        const aladin = A.aladin(containerRef.current, {
          ...ALADIN_OPTIONS,
          survey: surveyId,
          fov: initial?.fov ?? 0.25,
          target: initial ? `${initial.ra} ${initial.dec}` : undefined,
        });
        staticRef.current = A;
        aladinRef.current = aladin;
        sync.register(id, aladin);
        setReady(true);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });

    return () => {
      cancelled = true;
      sync.unregister(id);
      aladinRef.current = null;
      mocsRef.current.clear();
      markerCatRef.current = null;
      el.replaceChildren();
    };
    // The instance is created once per pane; survey/overlay changes are handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, sync]);

  // Base survey.
  useEffect(() => {
    const aladin = aladinRef.current;
    if (!ready || !aladin) return;
    try {
      aladin.setBaseImageLayer(surveyId);
    } catch (err) {
      console.error(`Pane ${id}: could not switch to survey ${surveyId}`, err);
    }
  }, [ready, surveyId, id]);

  // Coverage overlays (MOCs): add newly enabled ones, remove disabled ones, and reload any
  // whose URL changed (target-bound footprints follow the resolved target).
  useEffect(() => {
    const aladin = aladinRef.current;
    const A = staticRef.current;
    if (!ready || !aladin || !A) return;
    const wanted = new Map<string, ResolvedOverlay>();
    for (const key of enabledOverlays) {
      const def = overlays.find((o) => o.id === key);
      if (def) wanted.set(key, def);
    }
    for (const [key, entry] of mocsRef.current) {
      const def = wanted.get(key);
      if (def && def.url === entry.url) continue;
      try {
        aladin.remove(entry.moc);
      } catch (err) {
        console.warn(`Pane ${id}: could not remove overlay ${key}`, err);
      }
      mocsRef.current.delete(key);
      setOverlayStatus((s) => {
        const next = { ...s };
        delete next[key];
        return next;
      });
    }
    for (const [key, def] of wanted) {
      if (mocsRef.current.has(key)) continue;
      if (!def.url) {
        setOverlayStatus((s) => ({ ...s, [key]: "needs-target" }));
        continue;
      }
      const url = apiUrl(def.url);
      setOverlayStatus((s) => ({ ...s, [key]: "loading" }));
      try {
        const moc = A.MOCFromURL(
          url,
          { name: def.label, color: def.color, lineWidth: 1.5, opacity: 0.35, fill: true },
          () => setOverlayStatus((s) => ({ ...s, [key]: "ok" })),
          () => {
            console.error(`Pane ${id}: overlay ${key} failed to load from ${url}`);
            setOverlayStatus((s) => ({ ...s, [key]: "error" }));
          },
        );
        aladin.addMOC(moc);
        mocsRef.current.set(key, { moc, url: def.url });
      } catch (err) {
        console.error(`Pane ${id}: could not load overlay ${key} from ${url}`, err);
        setOverlayStatus((s) => ({ ...s, [key]: "error" }));
      }
    }
  }, [ready, enabledOverlays, overlays, id]);

  // Target marker.
  useEffect(() => {
    const aladin = aladinRef.current;
    const A = staticRef.current;
    if (!ready || !aladin || !A) return;
    try {
      if (!markerCatRef.current) {
        markerCatRef.current = A.catalog({
          name: "Target",
          color: "#fde047",
          sourceSize: 22,
          shape: "plus",
        });
        aladin.addCatalog(markerCatRef.current);
      }
      markerCatRef.current.removeAll();
      if (marker) {
        markerCatRef.current.addSources([A.source(marker.ra, marker.dec, { name: marker.label })]);
      }
    } catch (err) {
      console.warn(`Pane ${id}: could not draw target marker`, err);
    }
  }, [ready, marker, id]);

  const survey = surveys.find((s) => s.id === surveyId);

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-slate-800 bg-black">
      <div className="flex items-center gap-2 border-b border-slate-800 bg-slate-900/90 px-2 py-1 text-xs">
        <select
          value={surveyId}
          onChange={(e) => onSurveyChange(e.target.value)}
          className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950 px-1 py-0.5 text-slate-100 focus:outline-none"
          aria-label={`Survey for pane ${id}`}
        >
          {surveys.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
        {survey && (
          <span className="hidden shrink-0 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-400 lg:inline">
            {survey.band}
          </span>
        )}
        <div className="relative" ref={menuRef}>
          <button
            type="button"
            onClick={() => setShowOverlays((v) => !v)}
            className={`rounded border px-1.5 py-0.5 ${
              Object.values(overlayStatus).includes("error")
                ? "border-rose-600 text-rose-300"
                : enabledOverlays.length
                  ? "border-sky-600 text-sky-300"
                  : "border-slate-700 text-slate-300"
            } hover:bg-slate-800`}
            title="Coverage overlays"
          >
            MOC{enabledOverlays.length ? ` (${enabledOverlays.length})` : ""}
          </button>
          {showOverlays && (
            <div className="absolute right-0 z-20 mt-1 w-60 rounded border border-slate-700 bg-slate-900 p-2 shadow-xl">
              {[false, true].map((targetBound) => (
                <div key={String(targetBound)}>
                  <p className="mt-1 text-[10px] uppercase tracking-wide text-slate-500">
                    {targetBound ? "Footprints around the target" : "Mission coverage"}
                  </p>
                  {overlays
                    .filter((o) => o.targetBound === targetBound)
                    .map((o) => (
                      <label key={o.id} className="flex cursor-pointer items-center gap-2 py-0.5" title={o.description}>
                        <input
                          type="checkbox"
                          checked={enabledOverlays.includes(o.id)}
                          onChange={() => onOverlayToggle(o.id)}
                        />
                        <span className="inline-block h-2 w-2 rounded-sm" style={{ background: o.color }} />
                        <span className="text-slate-200">{o.label}</span>
                        {overlayStatus[o.id] === "loading" && (
                          <span className="ml-auto text-[10px] text-slate-500">loading…</span>
                        )}
                        {overlayStatus[o.id] === "needs-target" && (
                          <span className="ml-auto text-[10px] text-amber-400">needs a target</span>
                        )}
                        {overlayStatus[o.id] === "error" && (
                          <span className="ml-auto text-[10px] text-rose-400" title={`${o.url ? apiUrl(o.url) : ""} failed`}>
                            none / failed
                          </span>
                        )}
                      </label>
                    ))}
                </div>
              ))}
              <p className="mt-1 text-[10px] text-slate-500">
                Mission coverage comes from the CDS MOC server; footprints are computed from the
                archives' ObsCore tables around the target.
              </p>
            </div>
          )}
        </div>
      </div>
      <div ref={containerRef} className="aladin-pane relative min-h-0 flex-1" />
      {!ready && !error && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-slate-500">
          Loading Aladin Lite…
        </div>
      )}
      {error && (
        <div className="absolute inset-0 flex items-center justify-center p-4 text-center text-xs text-rose-300">
          Aladin Lite failed to start: {error}
        </div>
      )}
    </div>
  );
}
