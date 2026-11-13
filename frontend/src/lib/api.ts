import { env } from '$env/dynamic/public';
import type {
  DiscoveryResponse,
  HealthResponse,
  LanguageOption,
  WaterBodyCollection,
  WaterBodyFeature
} from '$lib/types';

const API_BASE = (env.PUBLIC_API_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '');

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    return body.detail || `Request failed with status ${response.status}`;
  } catch {
    return `Request failed with status ${response.status}`;
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return (await response.json()) as T;
}

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE}/api/health`);
  return parseResponse<HealthResponse>(response);
}

export async function getLanguages(): Promise<LanguageOption[]> {
  const response = await fetch(`${API_BASE}/api/languages`);
  return parseResponse<LanguageOption[]>(response);
}

export async function queryText(
  text: string,
  language: string,
  demo: boolean
): Promise<DiscoveryResponse> {
  const response = await fetch(`${API_BASE}/api/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, language, demo, speak: true })
  });
  return parseResponse<DiscoveryResponse>(response);
}

export async function queryVoice(
  audio: Blob,
  language: string,
  demo: boolean
): Promise<DiscoveryResponse> {
  const form = new FormData();
  form.append('audio', audio, 'query.wav');
  form.append('language', language);
  form.append('demo', String(demo));
  const response = await fetch(`${API_BASE}/api/voice/query`, {
    method: 'POST',
    body: form
  });
  return parseResponse<DiscoveryResponse>(response);
}

export async function getMask(waterBodyId: string): Promise<WaterBodyFeature> {
  const params = new URLSearchParams({ water_body_id: waterBodyId });
  const response = await fetch(`${API_BASE}/api/water-bodies/mask?${params}`);
  return parseResponse<WaterBodyFeature>(response);
}

export function featureCollection(
  features: WaterBodyFeature[]
): WaterBodyCollection {
  return { type: 'FeatureCollection', features };
}
