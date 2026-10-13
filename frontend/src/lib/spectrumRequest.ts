import type { ArchiveObservation, SpectrumRequest } from "../types";

/** Which extraction path an observation supports: ESO MUSE cubes via SODA, ALMA cubes via DataLink. */
export function extractKind(o: ArchiveObservation): "muse" | "alma" | null {
  if (o.public === false || !o.access_url) return null;
  if (
    o.archive === "eso" &&
    (o.instrument ?? "").toUpperCase() === "MUSE" &&
    (o.dataproduct_type ?? "").toLowerCase() === "cube"
  )
    return "muse";
  if (
    o.archive === "alma" &&
    (o.dataproduct_type ?? "").toLowerCase() === "cube"
  )
    return "alma";
  return null;
}

export function observationLabel(o: ArchiveObservation): string {
  const kind = extractKind(o);
  const prefix =
    kind === "alma"
      ? "ALMA"
      : kind === "muse"
        ? "MUSE"
        : (o.instrument ?? o.archive.toUpperCase());
  return `${prefix} ${(o.obs_id ?? "").replace(/^uid:\/\//, "").slice(0, 40)} ${o.date_obs?.slice(0, 10) ?? ""}`.trim();
}

export function toSpectrumRequest(
  o: ArchiveObservation,
): SpectrumRequest | null {
  const kind = extractKind(o);
  if (!kind) return null;
  return {
    kind,
    datalinkUrl: o.access_url as string,
    label: observationLabel(o),
    obsId: o.obs_id,
    cubeRa: o.ra_deg,
    cubeDec: o.dec_deg,
    coversTarget: o.covers_target,
    spwRanges:
      typeof o.extra?.spw_ranges_ghz === "string"
        ? o.extra.spw_ranges_ghz
        : null,
  };
}
