import type { Feature, FeatureCollection, Geometry } from 'geojson';

export interface WaterBodyProperties {
  water_body_id: string;
  name: string;
  area_m2: number;
  area_hectares: number;
  bbox: [number, number, number, number];
  centroid: [number, number];
  osm_type: string;
  osm_id: number;
  water_type: string;
  tags: Record<string, string>;
  source: string;
  mask_crs: string;
}

export type WaterBodyFeature = Feature<Geometry, WaterBodyProperties>;
export type WaterBodyCollection = FeatureCollection<Geometry, WaterBodyProperties>;

export interface Intent {
  target_entity: string;
  location: string;
  action: string;
  radius_km: number | null;
}

export interface GeocodeResult {
  display_name: string;
  lat: number;
  lon: number;
  bbox: [number, number, number, number];
  category: string;
  result_type: string;
  osm_type: string;
  osm_id: number | null;
}

export interface Step {
  name: string;
  status: 'ok' | 'warning' | 'error';
  detail: string;
}

export interface DiscoveryResponse {
  query: string;
  language: string;
  transcript: string | null;
  english_text: string;
  intent: Intent;
  location: GeocodeResult;
  water_bodies: WaterBodyCollection;
  count: number;
  confirmation_text: string;
  confirmation_audio_base64: string | null;
  confirmation_audio_mime: string | null;
  steps: Step[];
  warnings: string[];
}

export interface HealthResponse {
  status: string;
  environment: string;
  bhashini_configured: boolean;
  nvidia_configured: boolean;
  gee_configured: boolean;
  sentinel_hub_configured: boolean;
  demo_data_available: boolean;
}

export interface LanguageOption {
  code: string;
  label: string;
}

// --- Phase 2: historical profiling ------------------------------------------

export interface HistoryPoint {
  date: string;
  area_m2: number | null;
  observed: boolean;
}

export interface MonthlyClimatology {
  month: number;
  median_area_m2: number;
  min_area_m2: number;
  max_area_m2: number;
  samples: number;
}

export interface SeasonalBaseline {
  window_years: number;
  start_date: string;
  end_date: string;
  annual_median_m2: number;
  survival_threshold_m2: number;
  amplitude_pct: number;
  drought_months: number[];
  peak_months: number[];
  climatology: MonthlyClimatology[];
}

export interface TrendMetrics {
  slope_m2_per_year: number;
  slope_pct_per_year: number;
  recent_median_m2: number;
  prior_median_m2: number;
  permanent_change_pct: number;
  months_observed: number;
  months_missing: number;
}

export type HistoryClassification =
  | 'normal'
  | 'seasonal_drought'
  | 'permanent_loss'
  | 'insufficient_data';

export interface HistoryFlag {
  water_body_id: string;
  area_m2: number;
  baseline_area_m2: number;
  drop_pct: number;
  flag: boolean;
  classification: HistoryClassification;
  confidence: number;
  evidence: string[];
  evaluated_month: number;
  threshold_pct: number;
  index_version: string;
  source: string;
}

export interface TrendAnalysis {
  summary_en: string;
  localized_summary: string | null;
  language: string;
  seasonal_pattern: string;
  drought_window: string;
  anomaly: string;
  confidence: number;
  evidence: string[];
  source: 'nvidia' | 'deterministic';
  audio_base64: string | null;
  audio_mime: string | null;
}

export interface HistoricalProfile {
  water_body_id: string;
  name: string;
  language: string;
  source: string;
  index_version: string;
  generated_at: string;
  series: HistoryPoint[];
  baseline: SeasonalBaseline;
  trend: TrendMetrics;
  flag: HistoryFlag;
  analysis: TrendAnalysis;
  steps: Step[];
  warnings: string[];
  cached: boolean;
}

/**
 * Progressive view built from WebSocket messages. Fields arrive in order
 * (`historical`, then `flag`, then `briefing`), so the chart can render before
 * the LLM and Bhashini calls finish.
 */
export interface HistoryView {
  water_body_id: string;
  name: string;
  source: string;
  index_version: string;
  series: HistoryPoint[];
  baseline: SeasonalBaseline | null;
  trend: TrendMetrics | null;
  flag: HistoryFlag | null;
  analysis: TrendAnalysis | null;
  steps: Step[];
  warnings: string[];
  cached: boolean;
}

export interface HistoricalMessage {
  type: 'historical';
  water_body_id: string;
  source: string;
  index_version: string;
  series: HistoryPoint[];
  baseline: SeasonalBaseline;
  trend: TrendMetrics;
  steps: Step[];
  warnings: string[];
}

export interface FlagMessage {
  type: 'flag';
  water_body_id: string;
  flag: HistoryFlag;
}

export interface BriefingMessage {
  type: 'briefing';
  water_body_id: string;
  language: string;
  analysis: TrendAnalysis;
}

export interface HistoryErrorMessage {
  type: 'error';
  water_body_id: string;
  detail: string;
}

export type HistoryStreamMessage =
  | HistoricalMessage
  | FlagMessage
  | BriefingMessage
  | HistoryErrorMessage;

// --- Phase 3: live NDWI monitoring ------------------------------------------

export type AlertSeverity = 'normal' | 'watch' | 'critical';

export interface LiveSceneInfo {
  source: string;
  bounds: number[];
  width: number;
  height: number;
  resolution_m: number;
  cloud_pct: number;
  valid_pixel_pct: number;
  observed_at: string;
  window_start: string;
  window_end: string;
}

export interface LiveFlag {
  water_body_id: string;
  area_m2: number;
  baseline_area_m2: number;
  drop_pct: number;
  flag: boolean;
  classification: HistoryClassification;
  confidence: number;
  threshold_pct: number;
  ndwi_threshold: number;
  evaluated_month: number;
  water_pixels: number;
  valid_pixels: number;
  pixel_area_m2: number;
  cloud_pct: number;
  source: string;
  index_version: string;
  evidence: string[];
}

export interface LiveAlert {
  headline: string;
  summary_en: string;
  localized_summary: string | null;
  language: string;
  severity: AlertSeverity;
  recommended_action: string;
  confidence: number;
  evidence: string[];
  source: 'nvidia' | 'deterministic';
  audio_base64: string | null;
  audio_mime: string | null;
}

export interface LiveProfile {
  water_body_id: string;
  name: string;
  language: string;
  observed_at: string;
  source: string;
  index_version: string;
  scene: LiveSceneInfo;
  flag: LiveFlag;
  alert: LiveAlert;
  baseline: SeasonalBaseline;
  steps: Step[];
  warnings: string[];
  cached: boolean;
}

/** Progressive view assembled from `live`, `flag`, and `alert` messages. */
export interface LiveView {
  water_body_id: string;
  name: string;
  source: string;
  index_version: string;
  observed_at: string;
  scene: LiveSceneInfo | null;
  flag: LiveFlag | null;
  alert: LiveAlert | null;
  warnings: string[];
  cached: boolean;
}

export interface LiveMessage {
  type: 'live';
  water_body_id: string;
  observed_at: string;
  source: string;
  index_version: string;
  scene: LiveSceneInfo;
  flag: LiveFlag;
  baseline: SeasonalBaseline;
  steps: Step[];
  warnings: string[];
}

export interface LiveFlagMessage {
  type: 'flag';
  water_body_id: string;
  flag: LiveFlag;
}

export interface LiveAlertMessage {
  type: 'alert';
  water_body_id: string;
  language: string;
  alert: LiveAlert;
}

export interface LiveErrorMessage {
  type: 'error';
  water_body_id: string;
  detail: string;
}

export type LiveStreamMessage =
  | LiveMessage
  | LiveFlagMessage
  | LiveAlertMessage
  | LiveErrorMessage;

// --- Phase 4: forensic diagnostics ------------------------------------------

export type ForensicClassification =
  | 'seasonal_drought'
  | 'siltation_debris'
  | 'encroachment'
  | 'encroachment_suspected'
  | 'inconclusive'
  | 'insufficient_data';

export interface RecessionMetrics {
  start_date: string | null;
  end_date: string | null;
  months: number;
  days: number;
  start_area_m2: number;
  end_area_m2: number;
  loss_m2: number;
  velocity_m2_per_day: number;
  natural_velocity_m2_per_day: number;
  velocity_ratio: number;
}

export interface RetreatGeometry {
  max_area_m2: number;
  current_area_m2: number;
  shrinkage_pct: number;
  shared_boundary_ratio: number;
  front_straightness: number;
  asymmetry_index: number;
  mean_retreat_m: number;
  max_retreat_m: number;
  style: string;
  current_geometry: Geometry;
}

export interface BufferUrbanization {
  buffer_m: number;
  ring_area_m2: number;
  ndbi_mean: number;
  ndbi_built_pct: number;
  ndbi_anomaly: number;
}

export interface ForensicDiagnosis {
  classification: ForensicClassification;
  confidence: number;
  evidence: string[];
  index_version: string;
}

export interface ForensicReport {
  headline: string;
  report_en: string;
  localized_report: string | null;
  language: string;
  recommended_action: string;
  confidence: number;
  evidence: string[];
  source: 'nvidia' | 'deterministic';
  audio_base64: string | null;
  audio_mime: string | null;
}

export interface ForensicProfile {
  water_body_id: string;
  name: string;
  language: string;
  source: string;
  index_version: string;
  generated_at: string;
  recession: RecessionMetrics;
  geometry: RetreatGeometry;
  ndbi: BufferUrbanization;
  diagnosis: ForensicDiagnosis;
  report: ForensicReport;
  steps: Step[];
  warnings: string[];
  cached: boolean;
}

/** Progressive view assembled from forensic WebSocket messages. */
export interface ForensicView {
  water_body_id: string;
  name: string;
  source: string;
  index_version: string;
  recession: RecessionMetrics | null;
  geometry: RetreatGeometry | null;
  ndbi: BufferUrbanization | null;
  diagnosis: ForensicDiagnosis | null;
  report: ForensicReport | null;
  warnings: string[];
  cached: boolean;
}

export interface ForensicMessage {
  type: 'forensic';
  water_body_id: string;
  source: string;
  index_version: string;
  recession: RecessionMetrics;
  geometry: RetreatGeometry;
  ndbi: BufferUrbanization;
  diagnosis: ForensicDiagnosis;
  steps: Step[];
  warnings: string[];
}

export interface DiagnosisMessage {
  type: 'diagnosis';
  water_body_id: string;
  diagnosis: ForensicDiagnosis;
}

export interface ForensicBriefingMessage {
  type: 'briefing';
  water_body_id: string;
  language: string;
  report: ForensicReport;
}

export interface ForensicErrorMessage {
  type: 'error';
  water_body_id: string;
  detail: string;
}

export type ForensicStreamMessage =
  | ForensicMessage
  | DiagnosisMessage
  | ForensicBriefingMessage
  | ForensicErrorMessage;

// --- Phase 5: vision validation & final briefing -----------------------------

export type VisionMarkerSeverity = 'none' | 'low' | 'medium' | 'high';

export interface VisionFinding {
  marker: string;
  severity: VisionMarkerSeverity;
  confidence: number;
  location: string;
  description: string;
}

export interface ImagePatch {
  role: 'historical' | 'current';
  date: string;
  image_base64: string;
  mime: string;
}

export interface VisionInspection {
  headline: string;
  report_en: string;
  localized_report: string | null;
  language: string;
  confidence: number;
  markers: VisionFinding[];
  source: 'nvidia' | 'deterministic';
  audio_base64: string | null;
  audio_mime: string | null;
}

export interface VisionProfile {
  water_body_id: string;
  name: string;
  language: string;
  source: string;
  index_version: string;
  generated_at: string;
  patches: ImagePatch[];
  inspection: VisionInspection;
  diagnosis: ForensicDiagnosis;
  steps: Step[];
  warnings: string[];
  cached: boolean;
}

/** Progressive view assembled from vision WebSocket messages. */
export interface VisionView {
  water_body_id: string;
  name: string;
  source: string;
  index_version: string;
  patches: ImagePatch[];
  inspection: VisionInspection | null;
  diagnosis: ForensicDiagnosis | null;
  warnings: string[];
  cached: boolean;
}

export interface VisionMessage {
  type: 'vision';
  water_body_id: string;
  source: string;
  index_version: string;
  patches: ImagePatch[];
  inspection: VisionInspection;
  diagnosis: ForensicDiagnosis;
  steps: Step[];
  warnings: string[];
}

export interface VisionBriefingMessage {
  type: 'briefing';
  water_body_id: string;
  language: string;
  inspection: VisionInspection;
}

export interface VisionErrorMessage {
  type: 'error';
  water_body_id: string;
  detail: string;
}

export type VisionStreamMessage =
  | VisionMessage
  | VisionBriefingMessage
  | VisionErrorMessage;
