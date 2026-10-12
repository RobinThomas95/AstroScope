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
