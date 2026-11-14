<script lang="ts">
  import { onMount } from 'svelte';
  import mapboxgl from 'mapbox-gl';
  import 'mapbox-gl/dist/mapbox-gl.css';
  import { env } from '$env/dynamic/public';
  import type { WaterBodyCollection } from '$lib/types';

  export let data: WaterBodyCollection | null = null;
  export let selectedId: string | null = null;
  export let pulseIds: string[] = [];
  export let onselect: (waterBodyId: string) => void = () => {};

  let container: HTMLDivElement;
  let map: mapboxgl.Map | null = null;
  let mapError = '';
  let pulseOn = false;
  let pulseTimer: ReturnType<typeof setInterval> | null = null;

  const token = env.PUBLIC_MAPBOX_TOKEN || '';
  const emptyCollection: WaterBodyCollection = { type: 'FeatureCollection', features: [] };

  function extendBounds(bounds: mapboxgl.LngLatBounds, coordinates: unknown): void {
    if (!Array.isArray(coordinates) || coordinates.length === 0) {
      return;
    }
    if (typeof coordinates[0] === 'number' && typeof coordinates[1] === 'number') {
      bounds.extend([coordinates[0], coordinates[1]]);
      return;
    }
    for (const child of coordinates) {
      extendBounds(bounds, child);
    }
  }

  function extendGeometry(bounds: mapboxgl.LngLatBounds, geometry: unknown): void {
    if (!geometry || typeof geometry !== 'object') {
      return;
    }
    const candidate = geometry as {
      type?: string;
      coordinates?: unknown;
      geometries?: unknown[];
    };
    if (candidate.type === 'GeometryCollection' && Array.isArray(candidate.geometries)) {
      for (const child of candidate.geometries) {
        extendGeometry(bounds, child);
      }
      return;
    }
    if (candidate.coordinates) {
      extendBounds(bounds, candidate.coordinates);
    }
  }

  function fitToData(collection: WaterBodyCollection): void {
    if (!map || collection.features.length === 0) {
      return;
    }
    const bounds = new mapboxgl.LngLatBounds();
    for (const feature of collection.features) {
      extendGeometry(bounds, feature.geometry);
    }
    if (!bounds.isEmpty()) {
      map.fitBounds(bounds, { padding: 64, maxZoom: 13.5, duration: 800 });
    }
  }

  function fitToFeature(id: string): void {
    if (!map || !data) {
      return;
    }
    const feature = data.features.find(
      (item) => item.properties.water_body_id === id
    );
    if (!feature) {
      return;
    }
    const bounds = new mapboxgl.LngLatBounds();
    extendGeometry(bounds, feature.geometry);
    if (!bounds.isEmpty()) {
      map.fitBounds(bounds, { padding: 90, maxZoom: 15.5, duration: 700 });
    }
  }

  function zoomToAll(): void {
    if (data) {
      fitToData(data);
    }
  }

  /** Let the dashboard re-fit the canvas after the split pane is dragged. */
  export function resizeMap(): void {
    map?.resize();
  }

  function ensureLayers(): void {
    if (!map) {
      return;
    }
    if (!map.getLayer('water-fill')) {
      map.addLayer({
        id: 'water-fill',
        type: 'fill',
        source: 'water-bodies',
        paint: {
          'fill-color': '#4B4B4B',
          'fill-opacity': 0.45
        }
      });
    }
    if (!map.getLayer('water-outline')) {
      map.addLayer({
        id: 'water-outline',
        type: 'line',
        source: 'water-bodies',
        paint: {
          'line-color': '#8F8F8F',
          'line-width': 1.2,
          'line-opacity': 0.9
        }
      });
    }
  }

  function updateSelection(): void {
    if (!map || !map.getLayer('water-fill')) {
      return;
    }
    const id = selectedId ?? '';
    const pulseExpression = [
      'in',
      ['get', 'water_body_id'],
      ['literal', pulseIds]
    ] as mapboxgl.Expression;
    const selectedExpression = ['==', ['get', 'water_body_id'], id];

    map.setPaintProperty('water-fill', 'fill-color', [
      'case',
      pulseExpression,
      '#FF453A',
      selectedExpression,
      '#6B8FFF',
      '#4B4B4B'
    ]);
    map.setPaintProperty('water-fill', 'fill-opacity', [
      'case',
      pulseExpression,
      pulseOn ? 0.85 : 0.45,
      selectedExpression,
      0.5,
      0.35
    ]);
    map.setPaintProperty('water-outline', 'line-color', [
      'case',
      pulseExpression,
      '#FF453A',
      selectedExpression,
      '#6B8FFF',
      '#8F8F8F'
    ]);
    map.setPaintProperty('water-outline', 'line-width', [
      'case',
      pulseExpression,
      pulseOn ? 4 : 2,
      selectedExpression,
      2.5,
      1.2
    ]);
  }

  function syncPulse(): void {
    if (pulseIds.length === 0) {
      if (pulseTimer) {
        clearInterval(pulseTimer);
        pulseTimer = null;
      }
      pulseOn = false;
      updateSelection();
      return;
    }
    if (!pulseTimer) {
      pulseTimer = setInterval(() => {
        pulseOn = !pulseOn;
        updateSelection();
      }, 620);
    }
    updateSelection();
  }

  function renderData(collection: WaterBodyCollection): void {
    if (!map || !map.isStyleLoaded()) {
      return;
    }
    const source = map.getSource('water-bodies') as mapboxgl.GeoJSONSource | undefined;
    if (source) {
      source.setData(collection);
    } else {
      map.addSource('water-bodies', { type: 'geojson', data: collection });
      ensureLayers();
    }
    fitToData(collection);
    if (selectedId) {
      fitToFeature(selectedId);
    }
    updateSelection();
  }

  $: if (map && data && map.isStyleLoaded()) {
    renderData(data);
  }

  $: if (map && map.isStyleLoaded()) {
    updateSelection();
  }

  $: if (map && selectedId && map.isStyleLoaded()) {
    fitToFeature(selectedId);
  }

  $: if (map && pulseIds) {
    syncPulse();
  }

  onMount(() => {
    if (!token) {
      mapError =
        'Add PUBLIC_MAPBOX_TOKEN to frontend/.env to render the Mapbox base map. The results list still works.';
      return;
    }

    try {
      mapboxgl.accessToken = token;
      map = new mapboxgl.Map({
        container,
        style: 'mapbox://styles/mapbox/dark-v11',
        center: [77.5946, 12.9716],
        zoom: 10,
        attributionControl: true
      });
      map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right');

      map.on('load', () => {
        renderData(data ?? emptyCollection);
      });

      map.on('click', 'water-fill', (event) => {
        const feature = event.features?.[0];
        const id = feature?.properties?.water_body_id;
        if (id) {
          onselect(String(id));
        }
      });

      map.on('mouseenter', 'water-fill', () => {
        if (map) {
          map.getCanvas().style.cursor = 'pointer';
        }
      });
      map.on('mouseleave', 'water-fill', () => {
        if (map) {
          map.getCanvas().style.cursor = '';
        }
      });
    } catch (error) {
      mapError = error instanceof Error ? error.message : 'Mapbox failed to initialize.';
    }

    return () => {
      if (pulseTimer) {
        clearInterval(pulseTimer);
        pulseTimer = null;
      }
      map?.remove();
      map = null;
    };
  });
</script>

<div class="map-wrap">
  <div class="map" bind:this={container}></div>
  {#if data && data.features.length > 0}
    <button class="map-action" on:click={zoomToAll} title="Zoom to all discovered lakes">
      Fit lakes
    </button>
  {/if}
  {#if mapError}
    <div class="map-fallback">
      <div class="map-fallback-icon">◎</div>
      <strong>Mapbox needs a token</strong>
      <p>{mapError}</p>
    </div>
  {/if}
</div>

<style>
  .map-wrap {
    position: relative;
    min-height: 460px;
    height: 100%;
    overflow: hidden;
    border-radius: 0;
    background: #161616;
  }

  .map {
    position: absolute;
    inset: 0;
  }

  .map-action {
    position: absolute;
    top: 0.75rem;
    left: 0.75rem;
    z-index: 2;
    padding: 0.4rem 0.7rem;
    border: 1px solid #2e2e2e;
    border-radius: 0;
    color: #fafafa;
    background: #161616;
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.66rem;
    font-weight: 400;
  }

  .map-action:hover {
    background: #242424;
  }

  .map-fallback {
    position: absolute;
    inset: 0;
    display: grid;
    place-content: center;
    gap: 0.5rem;
    padding: 2rem;
    text-align: center;
    color: #fafafa;
    background: #0c0c0c;
  }

  .map-fallback-icon {
    font-size: 2.75rem;
    color: #6b8fff;
  }

  .map-fallback p {
    max-width: 28rem;
    margin: 0;
    color: #8f8f8f;
    font-size: 0.88rem;
    line-height: 1.5;
  }
</style>
