import { useCallback, useEffect, useRef, useState } from "react";
import AladinGrid from "./components/AladinGrid";
import ArchivePanel from "./components/ArchivePanel";
import KeysDialog from "./components/KeysDialog";
import TargetCard from "./components/TargetCard";
import TargetSearch from "./components/TargetSearch";
import { ApiError, fetchSurveyCatalog, resolveTarget } from "./lib/api";
import type { ResolvedTarget, SurveyCatalog } from "./types";

export default function App() {
  const [catalog, setCatalog] = useState<SurveyCatalog | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [target, setTarget] = useState<ResolvedTarget | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [radiusArcmin, setRadiusArcmin] = useState(5);
  const [keysOpen, setKeysOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const [catalogAttempt, setCatalogAttempt] = useState(0);

  // The catalog is static, so a failure is almost always the backend still starting up:
  // retry with backoff, and let the user retry by hand.
  useEffect(() => {
    const ac = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const load = (attempt: number) => {
      fetchSurveyCatalog(ac.signal)
        .then((c) => {
          setCatalog(c);
          setCatalogError(null);
        })
        .catch((err: unknown) => {
          if (ac.signal.aborted) return;
          setCatalogError(err instanceof Error ? err.message : String(err));
          if (attempt < 6) timer = setTimeout(() => load(attempt + 1), 1500 * (attempt + 1));
        });
    };
    load(0);
    return () => {
      ac.abort();
      if (timer) clearTimeout(timer);
    };
  }, [catalogAttempt]);

  const search = useCallback(async (query: string) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setBusy(true);
    setError(null);
    try {
      const result = await resolveTarget(query, { signal: ac.signal });
      if (!ac.signal.aborted) setTarget(result);
    } catch (err: unknown) {
      if (ac.signal.aborted) return;
      if (err instanceof ApiError) {
        setError(err.status === 404 ? `Not found: ${err.message}` : err.message);
      } else {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (!ac.signal.aborted) setBusy(false);
    }
  }, []);

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-4 border-b border-slate-800 px-4 py-3">
        <h1 className="text-xl font-bold tracking-tight text-white">
          Astro<span className="text-sky-400">Scope</span>
        </h1>
        <p className="hidden text-sm text-slate-400 md:block">
          Target reconnaissance &amp; multi-wavelength analysis
        </p>
        <div className="ml-auto flex-1 max-w-2xl">
          <TargetSearch busy={busy} onSearch={search} />
        </div>
        <button
          type="button"
          onClick={() => setKeysOpen(true)}
          className="rounded border border-slate-700 px-2 py-1 text-sm text-slate-300 hover:bg-slate-800"
          title="API keys (ADS, Gemini)"
        >
          ⚙ keys
        </button>
      </header>
      <KeysDialog open={keysOpen} onClose={() => setKeysOpen(false)} />

      {error && (
        <div className="border-b border-rose-900 bg-rose-950/60 px-4 py-2 text-sm text-rose-200">
          {error}
        </div>
      )}

      <main className="flex min-h-0 flex-1 gap-3 p-3">
        <aside className="hidden w-80 shrink-0 overflow-y-auto lg:block">
          {target ? (
            <TargetCard target={target} />
          ) : (
            <div className="rounded-lg border border-dashed border-slate-800 p-4 text-sm text-slate-500">
              Resolve a target to see its coordinates, redshift, morphology and environment here.
            </div>
          )}
        </aside>
        <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
          {catalog ? (
            <>
              <div className="min-h-0 flex-[3]">
                <AladinGrid
                  catalog={catalog}
                  target={target}
                  footprintRadiusArcmin={Math.max(radiusArcmin, 2)}
                />
              </div>
              <div className="min-h-0 flex-[2]">
                <ArchivePanel
                  target={target}
                  radiusArcmin={radiusArcmin}
                  onRadiusChange={setRadiusArcmin}
                  onOpenKeys={() => setKeysOpen(true)}
                />
              </div>
            </>
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-sm text-slate-500">
              {catalogError ? (
                <>
                  <span>Could not load the survey catalog: {catalogError}</span>
                  <span className="text-xs">Is the backend running on port 8000? Retrying…</span>
                  <button
                    type="button"
                    onClick={() => setCatalogAttempt((n) => n + 1)}
                    className="rounded border border-slate-700 px-3 py-1 text-slate-200 hover:bg-slate-800"
                  >
                    Retry now
                  </button>
                </>
              ) : (
                "Loading survey catalog…"
              )}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
