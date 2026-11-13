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
