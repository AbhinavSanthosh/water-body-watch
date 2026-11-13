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
  demo_data_available: boolean;
}

export interface LanguageOption {
  code: string;
  label: string;
}
