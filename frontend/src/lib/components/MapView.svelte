<script lang="ts">
  import { onMount } from 'svelte';
  import mapboxgl from 'mapbox-gl';
  import 'mapbox-gl/dist/mapbox-gl.css';
  import { env } from '$env/dynamic/public';
  import type { WaterBodyCollection } from '$lib/types';

  export let data: WaterBodyCollection | null = null;
  export let selectedId: string | null = null;
  export let onselect: (waterBodyId: string) => void = () => {};

  let container: HTMLDivElement;
  let map: mapboxgl.Map | null = null;
  let mapError = '';

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

  function fitToData(collection: WaterBodyCollection): void {
    if (!map || collection.features.length === 0) {
      return;
    }
    const bounds = new mapboxgl.LngLatBounds();
    for (const feature of collection.features) {
      extendBounds(bounds, feature.geometry);
    }
    if (!bounds.isEmpty()) {
      map.fitBounds(bounds, { padding: 64, maxZoom: 13.5, duration: 800 });
    }
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
          'fill-color': '#22d3ee',
          'fill-opacity': 0.35
        }
      });
    }
    if (!map.getLayer('water-outline')) {
      map.addLayer({
        id: 'water-outline',
        type: 'line',
        source: 'water-bodies',
        paint: {
          'line-color': '#67e8f9',
          'line-width': 1.5,
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
    map.setPaintProperty('water-fill', 'fill-color', [
      'case',
      ['==', ['get', 'water_body_id'], id],
      '#fb923c',
      '#22d3ee'
    ]);
    map.setPaintProperty('water-fill', 'fill-opacity', [
      'case',
      ['==', ['get', 'water_body_id'], id],
      0.62,
      0.35
    ]);
    map.setPaintProperty('water-outline', 'line-color', [
      'case',
      ['==', ['get', 'water_body_id'], id],
      '#fdba74',
      '#67e8f9'
    ]);
    map.setPaintProperty('water-outline', 'line-width', [
      'case',
      ['==', ['get', 'water_body_id'], id],
      3,
      1.5
    ]);
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
    updateSelection();
  }

  $: if (map && data && map.isStyleLoaded()) {
    renderData(data);
  }

  $: if (map && map.isStyleLoaded()) {
    updateSelection();
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
      map?.remove();
      map = null;
    };
  });
</script>

<div class="map-wrap">
  <div class="map" bind:this={container}></div>
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
    border-radius: 18px;
    background:
      radial-gradient(circle at 20% 20%, rgba(34, 211, 238, 0.16), transparent 45%),
      radial-gradient(circle at 80% 80%, rgba(14, 116, 144, 0.24), transparent 50%),
      #07151b;
  }

  .map {
    position: absolute;
    inset: 0;
  }

  .map-fallback {
    position: absolute;
    inset: 0;
    display: grid;
    place-content: center;
    gap: 0.5rem;
    padding: 2rem;
    text-align: center;
    color: #cffafe;
    background: rgba(7, 21, 27, 0.9);
  }

  .map-fallback-icon {
    font-size: 3rem;
    color: #22d3ee;
  }

  .map-fallback p {
    max-width: 28rem;
    margin: 0;
    color: #94a3b8;
    font-size: 0.9rem;
    line-height: 1.5;
  }
</style>
