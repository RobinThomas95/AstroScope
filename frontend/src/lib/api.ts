import { keyHeaders, loadKeys } from "./keys";
import type {
  ArchiveSearch,
  CubeCacheStatus,
  CubeJobStatus,
  CubeProduct,
  HIProfile,
  HISearch,
  KeyStatus,
  LiteratureResult,
  LlmModels,
  RadioLine,
  ResolvedTarget,
  SpectralLine,
  Spectrum1D,
  SurveyCatalog,
  SynthesisResult,
} from "../types";

const BASE =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(
    /\/$/,
    "",
  ) ?? "";

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

/** FastAPI error bodies: a string detail, or a list of validation errors (loc, msg). */
export function formatDetail(body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((d: { loc?: unknown[]; msg?: string }) => {
        const loc = Array.isArray(d.loc)
          ? d.loc.filter((x) => x !== "query" && x !== "body").join(".")
          : "";
        return loc ? `${loc}: ${d.msg ?? "invalid"}` : (d.msg ?? "");
      })
      .filter(Boolean);
    if (parts.length) return `Invalid request: ${parts.join("; ")}`;
  }
  return fallback;
}

async function getJson<T>(
  path: string,
  signal?: AbortSignal,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    signal,
    headers: { Accept: "application/json", ...extraHeaders },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = formatDetail(await res.json(), detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export function resolveTarget(
  query: string,
  opts?: { refresh?: boolean; signal?: AbortSignal },
) {
  const params = new URLSearchParams({ q: query });
  if (opts?.refresh) params.set("refresh", "true");
  return getJson<ResolvedTarget>(
    `/api/resolve?${params.toString()}`,
    opts?.signal,
  );
}

export function fetchSurveyCatalog(signal?: AbortSignal) {
  return getJson<SurveyCatalog>("/api/surveys", signal);
}

export function searchArchives(
  ra: number,
  dec: number,
  radiusArcmin: number,
  archives?: string[],
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    ra: ra.toFixed(6),
    dec: dec.toFixed(6),
    radius_arcmin: String(radiusArcmin),
  });
  if (archives?.length) params.set("archives", archives.join(","));
  return getJson<ArchiveSearch>(`/api/archives?${params.toString()}`, signal);
}

export const ARCHIVE_KEYS = ["eso", "mast", "alma"] as const;

export function searchHI(
  ra: number,
  dec: number,
  radiusArcmin?: number,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    ra: ra.toFixed(6),
    dec: dec.toFixed(6),
  });
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

export function fetchMuseSpectrum(
  opts: {
    datalinkUrl: string;
    ra: number;
    dec: number;
    radiusArcsec: number;
    method: "sum" | "mean";
  },
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    datalink_url: opts.datalinkUrl,
    ra: opts.ra.toFixed(6),
    dec: opts.dec.toFixed(6),
    radius_arcsec: String(opts.radiusArcsec),
    method: opts.method,
  });
  return getJson<Spectrum1D>(`/api/spectrum/muse?${params.toString()}`, signal);
}

export function fetchLines(
  z: number | null,
  wmin?: number,
  wmax?: number,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  if (z !== null) params.set("z", String(z));
  if (wmin !== undefined) params.set("wmin", String(wmin));
  if (wmax !== undefined) params.set("wmax", String(wmax));
  return getJson<SpectralLine[]>(`/api/lines?${params.toString()}`, signal);
}

export function fetchHIProfile(
  ra: number,
  dec: number,
  sourceId?: string,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    ra: ra.toFixed(6),
    dec: dec.toFixed(6),
  });
  if (sourceId) params.set("source_id", sourceId);
  return getJson<HIProfile>(`/api/hi/profile?${params.toString()}`, signal);
}

async function postJson<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal,
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    signal,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...extraHeaders,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = formatDetail(await res.json(), detail);
    } catch {
      /* non-JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export function fetchKeyStatus(signal?: AbortSignal) {
  return getJson<KeyStatus>("/api/keys/status", signal);
}

export function fetchLlmModels(signal?: AbortSignal) {
  return getJson<LlmModels>("/api/llm/models", signal, keyHeaders());
}

export function fetchLiterature(
  q: string,
  names: string[],
  opts: { refereed?: boolean; maxRows?: number } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ q });
  if (names.length) params.set("names", names.join(","));
  if (opts.refereed) params.set("refereed", "true");
  if (opts.maxRows) params.set("max_rows", String(opts.maxRows));
  return getJson<LiteratureResult>(
    `/api/literature?${params.toString()}`,
    signal,
    keyHeaders(),
  );
}

export function postSynthesis(
  q: string,
  names: string[],
  opts: { refereed?: boolean; maxAbstracts?: number } = {},
  signal?: AbortSignal,
) {
  const model = loadKeys().geminiModel || undefined;
  return postJson<SynthesisResult>(
    "/api/synthesis",
    {
      q,
      names,
      refereed: !!opts.refereed,
      max_abstracts: opts.maxAbstracts ?? 60,
      model,
    },
    signal,
    keyHeaders(),
  );
}

export function fetchRadioLines(
  z: number | null,
  fminGhz?: number,
  fmaxGhz?: number,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams();
  if (z !== null) params.set("z", String(z));
  if (fminGhz !== undefined) params.set("fmin_ghz", String(fminGhz));
  if (fmaxGhz !== undefined) params.set("fmax_ghz", String(fmaxGhz));
  return getJson<RadioLine[]>(`/api/lines/radio?${params.toString()}`, signal);
}

export function fetchCubeProducts(
  datalinkUrl: string,
  spwRanges: string | null,
  lineGhz: number | null,
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ datalink_url: datalinkUrl });
  if (spwRanges) params.set("spw_ranges", spwRanges);
  if (lineGhz) params.set("line_ghz", String(lineGhz));
  return getJson<CubeProduct[]>(
    `/api/cubes/products?${params.toString()}`,
    signal,
  );
}

export function fetchCubeSpectrum(
  opts: {
    productUrl: string;
    ra: number;
    dec: number;
    radiusArcsec: number;
    method: "sum" | "mean";
    maxMb?: number;
  },
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    product_url: opts.productUrl,
    ra: opts.ra.toFixed(6),
    dec: opts.dec.toFixed(6),
    radius_arcsec: String(opts.radiusArcsec),
    method: opts.method,
  });
  if (opts.maxMb) params.set("max_mb", String(opts.maxMb));
  return getJson<Spectrum1D>(`/api/spectrum/cube?${params.toString()}`, signal);
}

/** Start a cube download-and-extract job; poll it with fetchCubeJob for progress. */
export function startCubeSpectrumJob(
  opts: {
    productUrl: string;
    ra: number;
    dec: number;
    radiusArcsec: number;
    method: "sum" | "mean";
    maxMb?: number;
    /** SODA band edges in GHz (ALMA cutouts only). */
    fminGhz?: number;
    fmaxGhz?: number;
    mode?: "auto" | "cutout" | "download";
  },
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({
    product_url: opts.productUrl,
    ra: opts.ra.toFixed(6),
    dec: opts.dec.toFixed(6),
    radius_arcsec: String(opts.radiusArcsec),
    method: opts.method,
  });
  if (opts.maxMb) params.set("max_mb", String(opts.maxMb));
  if (opts.fminGhz && opts.fmaxGhz) {
    params.set("fmin_ghz", opts.fminGhz.toFixed(6));
    params.set("fmax_ghz", opts.fmaxGhz.toFixed(6));
  }
  if (opts.mode) params.set("mode", opts.mode);
  return postJson<CubeJobStatus>(
    `/api/spectrum/cube/jobs?${params.toString()}`,
    {},
    signal,
  );
}

export function fetchCubeJob(jobId: string, signal?: AbortSignal) {
  return getJson<CubeJobStatus>(
    `/api/spectrum/cube/jobs/${encodeURIComponent(jobId)}`,
    signal,
  );
}

export function fetchCubeCache(signal?: AbortSignal) {
  return getJson<CubeCacheStatus>("/api/cubes/cache", signal);
}

export async function clearCubeCache(): Promise<CubeCacheStatus> {
  const res = await fetch(`${BASE}/api/cubes/cache`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  if (!res.ok)
    throw new ApiError(
      res.status,
      formatDetail(await res.json().catch(() => null), res.statusText),
    );
  return (await res.json()) as CubeCacheStatus;
}
