<script lang="ts">
  import type { VisionMarkerSeverity, VisionView } from '$lib/types';

  export let vision: VisionView | null = null;
  export let loading = false;
  export let error = '';
  export let languageLabel: (code: string) => string = (code) => code;
  export let onPlay: () => void = () => {};
  export let onRetry: () => void = () => {};
  export let onRun: () => void = () => {};

  const MARKER_LABEL: Record<string, string> = {
    concrete_debris: 'Concrete debris',
    algal_bloom: 'Algal bloom',
    exposed_lakebed: 'Exposed lakebed',
    clear: 'No anomaly'
  };

  const SEVERITY_TONE: Record<VisionMarkerSeverity, string> = {
    none: 'muted',
    low: 'drought',
    medium: 'watch',
    high: 'critical'
  };

  function dateLabel(value: string | undefined): string {
    if (!value) {
      return '—';
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });
  }

  $: inspection = vision?.inspection ?? null;
  $: summary = inspection?.localized_report || inspection?.report_en || '';
  $: patches = vision?.patches ?? [];
</script>

<div class="vision">
  <div class="vision-heading">
    <div>
      <h2>Visual validation</h2>
      <p class="vision-sub">Before and after imagery · visual inspection</p>
    </div>
    <span class="badge">
      {inspection?.source === 'nvidia' ? 'AI vision' : vision?.source ? 'Rule-based' : 'idle'}
    </span>
  </div>

  {#if error}
    <div class="vision-error">
      <p>{error}</p>
      <button class="retry" on:click={onRetry}>Retry</button>
    </div>
  {/if}

  {#if loading && !patches.length}
    <div class="vision-loading">
      <span class="spinner"></span>
      Rendering before and after imagery and running the visual inspection…
    </div>
  {/if}

  {#if patches.length}
    <div class="patch-grid">
      {#each patches as patch (patch.role)}
        <figure class="patch">
          <img
            src={`data:${patch.mime};base64,${patch.image_base64}`}
            alt={`${patch.role} satellite patch`}
          />
          <figcaption>
            <span class:historical={patch.role === 'historical'}>
              {patch.role === 'historical' ? 'Historical baseline' : 'Current'}
            </span>
            <span class="patch-date">{dateLabel(patch.date)}</span>
          </figcaption>
        </figure>
      {/each}
    </div>
  {/if}

  {#if inspection}
    <div class="inspection">
      <div class="inspection-head">
        <h3>{inspection.headline}</h3>
        <span class="confidence">{Math.round(inspection.confidence * 100)}% confidence</span>
      </div>
      <p>{summary}</p>
      {#if inspection.localized_report}
        <details>
          <summary>English original ({languageLabel(inspection.language)})</summary>
          <p>{inspection.report_en}</p>
        </details>
      {/if}

      {#if inspection.markers.length}
        <ul class="markers">
          {#each inspection.markers as marker}
            <li>
              <span class="marker-chip {SEVERITY_TONE[marker.severity]}">
                {MARKER_LABEL[marker.marker] ?? marker.marker}
              </span>
              <span class="marker-location">{marker.location}</span>
              <span class="marker-meta">
                {marker.severity} · {Math.round(marker.confidence * 100)}%
              </span>
              <p>{marker.description}</p>
            </li>
          {/each}
        </ul>
      {/if}

      {#if vision?.diagnosis}
        <p class="diag-line">
          Diagnosis: {vision.diagnosis.classification.replaceAll('_', ' ')} ·
          {Math.round(vision.diagnosis.confidence * 100)}% confidence
        </p>
      {/if}

      {#if inspection.audio_base64}
        <button class="play" on:click={onPlay}>▶ Play briefing</button>
      {/if}
    </div>
  {/if}

  {#if !loading && !error && !patches.length && !inspection}
    <p class="empty-note">
      Select a lake to render before and after imagery and run the visual inspection.
    </p>
    <button class="run" on:click={onRun}>Run visual check</button>
  {/if}
</div>

<style>
  .vision {
    display: grid;
    gap: 0.55rem;
  }

  .vision-heading {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.6rem;
  }

  h2,
  h3 {
    margin: 0;
  }

  h2 {
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.78rem;
    font-weight: 400;
    letter-spacing: 0.01em;
    color: #fafafa;
  }

  h3 {
    color: #ffb4ab;
    font-size: 0.8rem;
    font-weight: 400;
    line-height: 1.35;
  }

  .vision-sub {
    margin: 0.15rem 0 0;
    color: #6A6A6A;
    font-size: 0.66rem;
  }

  .badge {
    padding: 0.16rem 0.5rem;
    border: 1px solid #2e2e2e;
    border-radius: 999px;
    color: #dbdbdb;
    background: #242424;
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.6rem;
    font-weight: 400;
    white-space: nowrap;
  }

  .patch-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.5rem;
  }

  .patch {
    margin: 0;
    display: grid;
    gap: 0.25rem;
  }

  .patch img {
    width: 100%;
    border-radius: 0;
    border: 1px solid #2E2E2E;
    background: #161616;
    aspect-ratio: 1 / 1;
    object-fit: cover;
  }

  .patch figcaption {
    display: flex;
    justify-content: space-between;
    gap: 0.4rem;
    color: #8F8F8F;
    font-size: 0.62rem;
  }

  .patch figcaption .historical {
    color: #30D158;
  }

  .patch-date {
    color: #6A6A6A;
  }

  .inspection {
    display: grid;
    gap: 0.35rem;
    padding: 0.65rem;
    border: 1px solid #2E2E2E;
    border-radius: 0;
    background: #1A1A1A;
  }

  .inspection-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .confidence {
    color: #8F8F8F;
    font-size: 0.64rem;
    white-space: nowrap;
  }

  .inspection p {
    margin: 0;
    color: #AEAEAE;
    font-size: 0.74rem;
    line-height: 1.55;
  }

  .inspection details {
    color: #8F8F8F;
    font-size: 0.68rem;
  }

  .inspection details summary {
    cursor: pointer;
  }

  .inspection details p {
    margin-top: 0.25rem;
    color: #8F8F8F;
  }

  .markers {
    display: grid;
    gap: 0.4rem;
    margin: 0.25rem 0 0;
    padding: 0;
    list-style: none;
  }

  .markers li {
    display: grid;
    gap: 0.15rem;
  }

  .marker-chip {
    justify-self: start;
    padding: 0.12rem 0.4rem;
    border-radius: 999px;
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.56rem;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .marker-chip.critical {
    color: #FFB4AB;
    background: rgba(255, 69, 58, 0.18);
  }

  .marker-chip.watch {
    color: #FFD60A;
    background: rgba(255, 159, 10, 0.16);
  }

  .marker-chip.drought {
    color: #30D158;
    background: rgba(48, 209, 88, 0.16);
  }

  .marker-chip.muted {
    color: #AEAEAE;
    background: #2E2E2E;
  }

  .marker-location {
    color: #6A6A6A;
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .marker-meta {
    color: #8F8F8F;
    font-size: 0.6rem;
    text-transform: capitalize;
  }

  .diag-line {
    margin: 0.1rem 0 0;
    color: #8F8F8F;
    font-size: 0.66rem;
    text-transform: capitalize;
  }

  .markers p {
    color: #8F8F8F;
    font-size: 0.68rem;
    line-height: 1.45;
  }

  .play {
    justify-self: start;
    border: 1px solid #fafafa;
    border-radius: 0;
    padding: 0.42rem 0.68rem;
    color: #0c0c0c;
    background: #fafafa;
    font-weight: 500;
    font-size: 0.66rem;
  }

  .play:hover {
    background: #dbdbdb;
    border-color: #dbdbdb;
  }

  .vision-loading {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.7rem;
    border: 1px solid #2e2e2e;
    border-radius: 0;
    color: #dbdbdb;
    background: #1a1a1a;
    font-size: 0.7rem;
  }

  .vision-error {
    display: grid;
    gap: 0.4rem;
    padding: 0.6rem;
    border: 1px solid rgba(255, 69, 58, 0.35);
    border-radius: 0;
    color: #FFB4AB;
    background: rgba(255, 69, 58, 0.08);
    font-size: 0.72rem;
  }

  .vision-error p {
    margin: 0;
  }

  .retry {
    justify-self: start;
    border: 1px solid rgba(255, 69, 58, 0.4);
    border-radius: 0;
    padding: 0.3rem 0.6rem;
    color: #FFB4AB;
    background: transparent;
    font-size: 0.68rem;
  }

  .run {
    justify-self: start;
    border: 1px solid #2e2e2e;
    border-radius: 0;
    padding: 0.4rem 0.7rem;
    color: #dbdbdb;
    background: transparent;
    font-size: 0.66rem;
    font-weight: 500;
  }

  .run:hover {
    background: #242424;
  }

  .spinner {
    width: 0.85rem;
    height: 0.85rem;
    border: 2px solid #2e2e2e;
    border-top-color: #6b8fff;
    border-radius: 999px;
    animation: spin 0.8s linear infinite;
    flex: none;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
