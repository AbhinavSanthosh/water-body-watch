<script lang="ts">
  import type { VisionMarkerSeverity, VisionView } from '$lib/types';

  export let vision: VisionView | null = null;
  export let loading = false;
  export let error = '';
  export let languageLabel: (code: string) => string = (code) => code;
  export let onPlay: () => void = () => {};
  export let onRetry: () => void = () => {};

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
      <p class="vision-sub">Side-by-side RGB patches · VLM inspection</p>
    </div>
    <span class="badge">
      {inspection?.source === 'nvidia' ? 'NVIDIA VLM' : vision?.source ? 'Deterministic' : 'idle'}
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
      Extracting RGB patches and running the vision model…
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
              <p>{marker.description}</p>
            </li>
          {/each}
        </ul>
      {/if}

      {#if inspection.audio_base64}
        <button class="play" on:click={onPlay}>▶ Play briefing</button>
      {/if}
    </div>
  {/if}

  {#if vision?.warnings.length}
    <div class="vision-warnings">
      {#each vision.warnings as warning}
        <p>⚠ {warning}</p>
      {/each}
    </div>
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
    font-size: 0.9rem;
    letter-spacing: -0.01em;
  }

  h3 {
    color: #fee2e2;
    font-size: 0.8rem;
    line-height: 1.35;
  }

  .vision-sub {
    margin: 0.15rem 0 0;
    color: #64748b;
    font-size: 0.66rem;
  }

  .badge {
    padding: 0.2rem 0.5rem;
    border-radius: 999px;
    color: #a5f3fc;
    background: rgba(34, 211, 238, 0.12);
    font-size: 0.62rem;
    font-weight: 650;
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
    border-radius: 10px;
    border: 1px solid rgba(148, 163, 184, 0.18);
    background: #06141a;
    aspect-ratio: 1 / 1;
    object-fit: cover;
  }

  .patch figcaption {
    display: flex;
    justify-content: space-between;
    gap: 0.4rem;
    color: #94a3b8;
    font-size: 0.62rem;
  }

  .patch figcaption .historical {
    color: #6ee7b7;
  }

  .patch-date {
    color: #64748b;
  }

  .inspection {
    display: grid;
    gap: 0.35rem;
    padding: 0.65rem;
    border: 1px solid rgba(148, 163, 184, 0.16);
    border-radius: 11px;
    background: rgba(148, 163, 184, 0.05);
  }

  .inspection-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .confidence {
    color: #94a3b8;
    font-size: 0.64rem;
    white-space: nowrap;
  }

  .inspection p {
    margin: 0;
    color: #cbd5e1;
    font-size: 0.74rem;
    line-height: 1.55;
  }

  .inspection details {
    color: #94a3b8;
    font-size: 0.68rem;
  }

  .inspection details summary {
    cursor: pointer;
  }

  .inspection details p {
    margin-top: 0.25rem;
    color: #94a3b8;
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
    font-size: 0.6rem;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .marker-chip.critical {
    color: #fca5a5;
    background: rgba(248, 113, 113, 0.18);
  }

  .marker-chip.watch {
    color: #fcd34d;
    background: rgba(251, 191, 36, 0.16);
  }

  .marker-chip.drought {
    color: #6ee7b7;
    background: rgba(16, 185, 129, 0.16);
  }

  .marker-chip.muted {
    color: #cbd5e1;
    background: rgba(148, 163, 184, 0.16);
  }

  .marker-location {
    color: #64748b;
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .markers p {
    color: #94a3b8;
    font-size: 0.68rem;
    line-height: 1.45;
  }

  .play {
    justify-self: start;
    border: 0;
    border-radius: 8px;
    padding: 0.42rem 0.68rem;
    color: #032b33;
    background: #a5f3fc;
    font-weight: 650;
    font-size: 0.7rem;
  }

  .vision-loading {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.7rem;
    border: 1px solid rgba(34, 211, 238, 0.18);
    border-radius: 10px;
    color: #a5f3fc;
    background: rgba(8, 47, 58, 0.4);
    font-size: 0.72rem;
  }

  .vision-error {
    display: grid;
    gap: 0.4rem;
    padding: 0.6rem;
    border: 1px solid rgba(248, 113, 113, 0.35);
    border-radius: 10px;
    color: #fecaca;
    background: rgba(127, 29, 29, 0.18);
    font-size: 0.72rem;
  }

  .vision-error p {
    margin: 0;
  }

  .retry {
    justify-self: start;
    border: 1px solid rgba(248, 113, 113, 0.4);
    border-radius: 8px;
    padding: 0.3rem 0.6rem;
    color: #fecaca;
    background: transparent;
    font-size: 0.68rem;
  }

  .vision-warnings {
    display: grid;
    gap: 0.2rem;
  }

  .vision-warnings p {
    margin: 0;
    color: #fcd34d;
    font-size: 0.66rem;
    line-height: 1.45;
  }

  .spinner {
    width: 0.85rem;
    height: 0.85rem;
    border: 2px solid rgba(165, 243, 252, 0.25);
    border-top-color: #67e8f9;
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
