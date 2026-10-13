// Mirrors backend/app/schemas/target.py and backend/app/services/surveys.py

export interface Coordinates {
  ra_deg: number;
  dec_deg: number;
  ra_hms: string;
  dec_dms: string;
  gal_l_deg: number | null;
  gal_b_deg: number | null;
  source: string | null;
}

export interface Kinematics {
  redshift: number | null;
  redshift_err: number | null;
  velocity_kms: number | null;
  velocity_err_kms: number | null;
  measurement_type: string | null;
  quality: string | null;
  ned_redshift: number | null;
  ned_velocity_kms: number | null;
  source: string | null;
}

export interface Morphology {
  type: string | null;
  quality: string | null;
  object_type: string | null;
  object_type_label: string | null;
  major_axis_arcmin: number | null;
  minor_axis_arcmin: number | null;
  position_angle_deg: number | null;
  source: string | null;
}

export interface Distance {
  value: number;
  unit: string;
  method: string | null;
  bibcode: string | null;
  source: string;
}

export interface GroupMembership {
  name: string;
  catalog: string | null;
  relation: "member" | "nearby";
  object_type: string | null;
  separation_arcmin: number | null;
  redshift: number | null;
}

export interface Environment {
  memberships: GroupMembership[];
  nearby_groups: GroupMembership[];
  search_radius_arcmin: number | null;
  note: string | null;
}

export interface ResolvedTarget {
  query: string;
  input_kind: "name" | "coordinates";
  main_id: string | null;
  ned_name: string | null;
  aliases: string[];
  coordinates: Coordinates;
  kinematics: Kinematics;
  morphology: Morphology;
  distances: Distance[];
  environment: Environment;
  suggested_fov_deg: number;
  sources: string[];
  warnings: string[];
  cached: boolean;
}

export interface Survey {
  id: string;
  label: string;
  band: string;
  description: string;
  cmap: string | null;
}

export interface Overlay {
  id: string;
  label: string;
  color: string;
  description: string;
  /** API-relative path serving the coverage as a FITS MOC (see apiUrl()). */
  moc_url: string;
}

export interface TargetOverlay {
  id: string;
  label: string;
  color: string;
  description: string;
  /** API path with {ra}, {dec}, {radius} placeholders. */
  moc_url_template: string;
}

export interface SurveyCatalog {
  surveys: Survey[];
  overlays: Overlay[];
  target_overlays: TargetOverlay[];
  default_grid: string[];
}

/** An overlay ready to load in a pane; url is null when it needs a target that is not set. */
export interface ResolvedOverlay {
  id: string;
  label: string;
  color: string;
  description: string;
  url: string | null;
  targetBound: boolean;
}

// ---- Module 3: archives ----

export interface ArchiveObservation {
  archive: string;
  collection: string | null;
  instrument: string | null;
  obs_id: string | null;
  dataproduct_type: string | null;
  calib_level: number | null;
  target_name: string | null;
  date_obs: string | null;
  mjd_obs: number | null;
  exptime_s: number | null;
  band: string | null;
  em_min_m: number | null;
  em_max_m: number | null;
  resolution_arcsec: number | null;
  fov_deg: number | null;
  ra_deg: number | null;
  dec_deg: number | null;
  separation_arcmin: number | null;
  s_region: string | null;
  covers_target: boolean | null;
  access_url: string | null;
  preview_url: string | null;
  archive_url: string | null;
  proposal_id: string | null;
  release_date: string | null;
  public: boolean | null;
  extra: Record<string, string | number | null>;
}

export interface ArchiveResult {
  archive: string;
  label: string;
  status: "ok" | "error" | "skipped";
  error: string | null;
  table: string | null;
  count: number;
  truncated: boolean;
  elapsed_s: number | null;
  observations: ArchiveObservation[];
}

export interface ArchiveSearch {
  ra_deg: number;
  dec_deg: number;
  radius_arcmin: number;
  results: ArchiveResult[];
  instrument_counts: Record<string, number>;
}

export interface HIDetection {
  survey: string;
  catalog: string;
  source_id: string | null;
  name: string | null;
  ra_deg: number | null;
  dec_deg: number | null;
  separation_arcmin: number | null;
  velocity_kms: number | null;
  velocity_err_kms: number | null;
  w50_kms: number | null;
  w50_err_kms: number | null;
  w20_kms: number | null;
  flux_jy_kms: number | null;
  flux_err_jy_kms: number | null;
  snr: number | null;
  rms_mjy: number | null;
  distance_mpc: number | null;
  log_mhi: number | null;
  vizier_url: string | null;
}

export interface HISurveyResult {
  survey: string;
  catalog: string;
  status: "ok" | "error";
  error: string | null;
  detections: HIDetection[];
}

export interface HISearch {
  ra_deg: number;
  dec_deg: number;
  radius_arcmin: number;
  results: HISurveyResult[];
}

// ---- Module 4: spectra ----

export interface SpectralLine {
  name: string;
  rest_wavelength_A: number;
  observed_wavelength_A: number | null;
  kind: "emission" | "absorption" | "both";
  group: string;
}

export interface Spectrum1D {
  wavelength_A: number[];
  flux: (number | null)[];
  error: (number | null)[] | null;
  flux_unit: string;
  wavelength_unit: string;
  frequency_ghz: (number | null)[] | null;
  method: "sum" | "mean";
  n_spaxels: number;
  aperture_radius_arcsec: number;
  center_ra_deg: number;
  center_dec_deg: number;
  pixel_scale_arcsec: number | null;
  n_channels: number;
  source: Record<string, string | number | null>;
  warnings: string[];
  cached: boolean;
}

export interface HIProfile {
  velocity_kms: number[];
  flux_jy: number[];
  model: string;
  source_id: string | null;
  survey: string | null;
  velocity_sys_kms: number | null;
  w50_kms: number | null;
  w20_kms: number | null;
  integrated_flux_jy_kms: number | null;
  peak_jy: number | null;
  note: string;
}

/** A request to extract a spectrum from one cube (MUSE via SODA, or ALMA via a product file). */
export interface SpectrumRequest {
  kind: "muse" | "alma";
  datalinkUrl: string;
  label: string;
  obsId: string | null;
  /** Cube centre from ObsCore (s_ra, s_dec), used when the target is outside the footprint. */
  cubeRa: number | null;
  cubeDec: number | null;
  coversTarget: boolean | null;
  /** ALMA: "spw:fmin-fmax; ..." in GHz, from ObsCore. */
  spwRanges: string | null;
}

export interface CubeProduct {
  name: string;
  url: string;
  size_mb: number | null;
  spw: number | null;
  kind: string;
  semantics: string | null;
  intent: string | null;
  freq_center_ghz: number | null;
  freq_min_ghz: number | null;
  freq_max_ghz: number | null;
  freq_approx: boolean;
  contains_line: boolean | null;
}

export interface RadioLine {
  name: string;
  rest_frequency_ghz: number;
  observed_frequency_ghz: number | null;
}

// ---- Module 5 / 6: literature ----

export interface Paper {
  bibcode: string;
  title: string | null;
  first_author: string | null;
  authors: string[];
  year: number | null;
  pubdate: string | null;
  journal: string | null;
  doctype: string | null;
  refereed: boolean;
  citation_count: number;
  keywords: string[];
  facilities: string[];
  abstract: string | null;
  ads_url: string;
}

export interface YearCount {
  year: number;
  count: number;
  refereed: number;
}

export interface NamedCount {
  name: string;
  count: number;
}

export interface CitationBin {
  label: string;
  min: number;
  max: number | null;
  count: number;
}

export interface Bibliometrics {
  total: number;
  refereed: number;
  years: YearCount[];
  facilities: NamedCount[];
  keywords: NamedCount[];
  citation_bins: CitationBin[];
  total_citations: number;
  h_index: number;
  top_cited: string[];
  most_recent: string[];
}

export interface LiteratureResult {
  query: string;
  names: string[];
  ads_query: string;
  num_found: number;
  truncated: boolean;
  papers: Paper[];
  metrics: Bibliometrics;
  token_source: "header" | "server";
  cached: boolean;
}

export interface Synthesis {
  summary: string;
  consensus: string[];
  open_questions: string[];
  key_papers: { bibcode: string; reason: string }[];
  keyword_clusters: { theme: string; keywords: string[]; bibcodes: string[] }[];
  facilities: { name: string; role: string }[];
  publication_trend: string;
}

export interface SynthesisResult {
  query: string;
  model: string;
  provider: string;
  n_abstracts: number;
  bibcodes_used: string[];
  synthesis: Synthesis;
  prompt_tokens: number | null;
  output_tokens: number | null;
  key_source: "header" | "server";
  cached: boolean;
  warnings: string[];
}

export interface KeyStatus {
  ads_server_key: boolean;
  gemini_server_key: boolean;
  gemini_model: string;
}

export interface LlmModels {
  models: string[];
  suggested: string | null;
  all_models: string[];
}

export interface CubeJobStatus {
  job_id: string;
  state: "queued" | "cutout" | "downloading" | "extracting" | "done" | "error";
  downloaded_mb: number;
  total_mb: number | null;
  message: string | null;
  spectrum: Spectrum1D | null;
}

export interface CubeCacheStatus {
  dir: string;
  files: number;
  bytes_mb: number;
  limit_mb: number;
}
