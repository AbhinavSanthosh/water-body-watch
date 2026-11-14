<script lang="ts">
  import type { ForensicClassification, ForensicView } from '$lib/types';

  export let forensics: ForensicView | null = null;
  export let loading = false;
  export let error = '';
  export let languageLabel: (code: string) => string = (code) => code;
  export let onPlay: () => void = () => {};
  export let onRetry: () => void = () => {};
  export let onRun: () => void = () => {};

  const CLASSIFICATION_LABEL: Record<ForensicClassification, string> = {
    seasonal_drought: 'Seasonal drought',
    siltation_debris: 'Siltation / debris',
    encroachment: 'Active encroachment',
    encroachment_suspected: 'Suspected encroachment',
    inconclusive: 'Inconclusive',
    insufficient_data: 'Insufficient data'
  };

  const CLASSIFICATION_TONE: Record<ForensicClassification, string> = {
    seasonal_drought: 'drought',
    siltation_debris: 'siltation',
    encroachment: 'encroachment',
    encroachment_suspected: 'suspected',
    inconclusive: 'muted',
    insufficient_data: 'muted'
  };

  function velocity(value: number | undefined | null): string {
    if (typeof value !== 'number') {
      return '—';
    }
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(value);
  }

  function hectares(value: number | undefined | null): string {
    if (typeof value !== 'number') {
      return '—';
    }
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 }).format(value / 10_000);
  }

  $: diagnosis = forensics?.diagnosis ?? null;
  $: report = forensics?.report ?? null;
  $: summary = report?.localized_report || report?.report_en || '';
  $: tone = diagnosis ? CLASSIFICATION_TONE[diagnosis.classification] : 'muted';
</script>

<div class="forensics">
  <div class="forensics-heading">
    <div>
      <h2>Forensic diagnosis</h2>
      <p class="forensics-sub">Recession velocity · retreat shape · buffer NDBI</p>
    </div>
    <span class="badge" class:demo={forensics?.source === 'synthetic-demo'}>
      {forensics?.source === 'sentinel-hub' ? 'Sentinel-2' : forensics?.source ? 'Synthetic' : 'idle'}
    </span>
  </div>

  {#if error}
    <div class="forensics-error">
      <p>{error}</p>
      <button class="retry" on:click={onRetry}>Retry</button>
    </div>
  {/if}

  {#if loading && !forensics?.diagnosis}
    <div class="forensics-loading">
      <span class="spinner"></span>
      Analysing recession, retreat shape, and surrounding land use…
    </div>
  {/if}

  {#if diagnosis}
    <div class="diagnosis-row">
      <span class="diag-chip {tone}">
        {CLASSIFICATION_LABEL[diagnosis.classification]}
      </span>
      <span class="confidence">{Math.round(diagnosis.confidence * 100)}% confidence</span>
    </div>

    {#if diagnosis.evidence.length}
      <ul class="evidence diagnosis-evidence">
        {#each diagnosis.evidence as item}
          <li>{item}</li>
        {/each}
      </ul>
    {/if}

    {#if forensics?.recession && forensics.geometry && forensics.ndbi}
      <div class="metric-grid">
        <div>
          <span class="metric-label">Retreat velocity</span>
          <strong>{velocity(forensics.recession.velocity_m2_per_day)} m²/day</strong>
          <span class="metric-note">{forensics.recession.velocity_ratio.toFixed(2)}× natural</span>
        </div>
        <div>
          <span class="metric-label">Retreat shape</span>
          <strong>{Math.round(forensics.geometry.shared_boundary_ratio * 100)}% shared</strong>
          <span class="metric-note">{Math.round(forensics.geometry.front_straightness * 100)}% straight</span>
        </div>
        <div>
          <span class="metric-label">Buffer built-up</span>
          <strong>{forensics.ndbi.ndbi_built_pct.toFixed(0)}%</strong>
          <span class="metric-note">NDBI {forensics.ndbi.ndbi_mean.toFixed(2)}</span>
        </div>
      </div>

      <div class="metric-grid secondary">
        <div>
          <span class="metric-label">Retreat style</span>
          <strong>{forensics.geometry.style.replaceAll('_', ' ')}</strong>
          <span class="metric-note">
            {forensics.geometry.mean_retreat_m.toFixed(0)} m mean ·
            {forensics.geometry.max_retreat_m.toFixed(0)} m max
          </span>
        </div>
        <div>
          <span class="metric-label">Area loss</span>
          <strong>{hectares(forensics.recession.loss_m2)} ha</strong>
          <span class="metric-note">
            {forensics.recession.start_date ?? '—'} → {forensics.recession.end_date ?? '—'}
          </span>
        </div>
        <div>
          <span class="metric-label">Buffer ring</span>
          <strong>{hectares(forensics.ndbi.ring_area_m2)} ha</strong>
          <span class="metric-note">
            anomaly {forensics.ndbi.ndbi_anomaly.toFixed(2)} · {forensics.ndbi.buffer_m.toFixed(0)} m
          </span>
        </div>
      </div>
    {/if}

    {#if report}
      <div class="report">
        <h3>{report.headline}</h3>
        <p>{summary}</p>
        <p class="action">→ {report.recommended_action}</p>
        {#if report.localized_report}
          <details>
            <summary>English original ({languageLabel(report.language)})</summary>
            <p>{report.report_en}</p>
          </details>
        {/if}
        {#if report.evidence.length}
          <ul class="evidence">
            {#each report.evidence as item}
              <li>{item}</li>
            {/each}
          </ul>
        {/if}
        {#if report.audio_base64}
          <button class="play" on:click={onPlay}>▶ Play diagnosis</button>
        {/if}
      </div>
    {/if}
  {:else if !loading && !error}
    <p class="empty-note">
      When a lake crosses its shrinkage flag, the forensic analysis diagnoses the cause here.
    </p>
    <button class="run" on:click={onRun}>Run forensic diagnosis</button>
  {/if}
</div>

<style>
  .forensics {
    display: grid;
    gap: 0.55rem;
  }

  .forensics-heading {
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

  .forensics-sub {
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

  .badge.demo {
    color: #ffd60a;
    border-color: rgba(255, 159, 10, 0.35);
    background: rgba(255, 159, 10, 0.08);
  }

  .diagnosis-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .diag-chip {
    padding: 0.22rem 0.55rem;
    border-radius: 999px;
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.62rem;
    font-weight: 400;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .diag-chip.encroachment {
    color: #FFB4AB;
    background: rgba(255, 69, 58, 0.18);
  }

  .diag-chip.siltation,
  .diag-chip.suspected {
    color: #FFD60A;
    background: rgba(255, 159, 10, 0.16);
  }

  .diag-chip.drought {
    color: #30D158;
    background: rgba(48, 209, 88, 0.16);
  }

  .diag-chip.muted {
    color: #AEAEAE;
    background: #2E2E2E;
  }

  .confidence {
    color: #8F8F8F;
    font-size: 0.66rem;
    white-space: nowrap;
  }

  .metric-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.35rem;
  }

  .metric-grid.secondary {
    margin-top: 0.1rem;
  }

  .metric-grid div {
    display: grid;
    gap: 0.1rem;
    padding: 0.45rem 0.5rem;
    border: 1px solid #2E2E2E;
    border-radius: 0;
    background: #1A1A1A;
  }

  .metric-label {
    color: #6a6a6a;
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.54rem;
    text-transform: uppercase;
    letter-spacing: 0.07em;
  }

  .metric-grid strong {
    color: #DBDBDB;
    font-size: 0.76rem;
  }

  .metric-note {
    color: #8F8F8F;
    font-size: 0.62rem;
  }

  .report {
    display: grid;
    gap: 0.35rem;
    padding: 0.65rem;
    border: 1px solid rgba(255, 69, 58, 0.28);
    border-radius: 0;
    background: rgba(255, 69, 58, 0.06);
  }

  .report p {
    margin: 0;
    color: #AEAEAE;
    font-size: 0.74rem;
    line-height: 1.55;
  }

  .report .action {
    color: #FAFAFA;
    font-size: 0.7rem;
  }

  .report details {
    color: #8F8F8F;
    font-size: 0.68rem;
  }

  .report details summary {
    cursor: pointer;
  }

  .report details p {
    margin-top: 0.25rem;
    color: #8F8F8F;
  }

  .evidence {
    display: grid;
    gap: 0.18rem;
    margin: 0;
    padding-left: 1rem;
    color: #8F8F8F;
    font-size: 0.68rem;
    line-height: 1.5;
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

  .forensics-loading {
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

  .forensics-error {
    display: grid;
    gap: 0.4rem;
    padding: 0.6rem;
    border: 1px solid rgba(255, 69, 58, 0.35);
    border-radius: 0;
    color: #FFB4AB;
    background: rgba(255, 69, 58, 0.08);
    font-size: 0.72rem;
  }

  .forensics-error p {
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

  .empty-note {
    color: #8F8F8F;
    font-size: 0.72rem;
    line-height: 1.55;
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

  @media (max-width: 520px) {
    .metric-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
