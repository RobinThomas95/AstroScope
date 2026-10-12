import type { ArchiveSearch, HISearch, ResolvedTarget, SurveyCatalog } from "../types";

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

/** Turn an API-relative path (e.g. "/api/overlays/hst/moc") into a fetchable URL. */
export function apiUrl(path: string): string {
  return path.startsWith("/") ? `${BASE}${path}` : path;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal, headers: { Accept: "application/json" } });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export function resolveTarget(query: string, opts?: { refresh?: boolean; signal?: AbortSignal }) {
  const params = new URLSearchParams({ q: query });
  if (opts?.refresh) params.set("refresh", "true");
  return getJson<ResolvedTarget>(`/api/resolve?${params.toString()}`, opts?.signal);
}

export function fetchSurveyCatalog(signal?: AbortSignal) {
  return getJson<SurveyCatalog>("/api/surveys", signal);
}

export function searchArchives(
  ra: number,
  dec: number,
  radiusArcmin: number,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    ra: ra.toFixed(6),
    dec: dec.toFixed(6),
    radius_arcmin: String(radiusArcmin),
  });
  return getJson<ArchiveSearch>(`/api/archives?${params.toString()}`, signal);
}

export function searchHI(ra: number, dec: number, radiusArcmin?: number, signal?: AbortSignal) {
  const params = new URLSearchParams({ ra: ra.toFixed(6), dec: dec.toFixed(6) });
  if (radiusArcmin) params.set("radius_arcmin", String(radiusArcmin));
  return getJson<HISearch>(`/api/hi?${params.toString()}`, signal);
}

/** Fill a target-overlay template with the current position. */
export function fillOverlayTemplate(
  template: string,
  ra: number,
  dec: number,
  radiusArcmin: number,
): string {
  return template
    .replace("{ra}", ra.toFixed(6))
    .replace("{dec}", dec.toFixed(6))
    .replace("{radius}", String(radiusArcmin));
}
