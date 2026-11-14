<script lang="ts">
  import type { ForensicClassification, ForensicView } from '$lib/types';

  export let forensics: ForensicView | null = null;
  export let loading = false;
  export let error = '';
  export let languageLabel: (code: string) => string = (code) => code;
  export let onPlay: () => void = () => {};
  export let onRetry: () => void = () => {};

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
      Running the LangGraph forensic pipeline…
    </div>
  {/if}

  {#if diagnosis}
    <div class="diagnosis-row">
      <span class="diag-chip {tone}">
        {CLASSIFICATION_LABEL[diagnosis.classification]}
      </span>
      <span class="confidence">{Math.round(diagnosis.confidence * 100)}% confidence</span>
    </div>

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

    {#if forensics?.warnings.length}
      <div class="forensics-warnings">
        {#each forensics.warnings as warning}
          <p>⚠ {warning}</p>
        {/each}
      </div>
    {/if}
  {:else if !loading && !error}
    <p class="empty-note">
      When a lake crosses its shrinkage flag, the forensic pipeline diagnoses the cause here.
    </p>
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
    font-size: 0.9rem;
    letter-spacing: -0.01em;
  }

  h3 {
    color: #fee2e2;
    font-size: 0.82rem;
    line-height: 1.35;
  }

  .forensics-sub {
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

  .badge.demo {
    color: #fcd34d;
    background: rgba(251, 191, 36, 0.12);
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
    font-size: 0.68rem;
    font-weight: 800;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .diag-chip.encroachment {
    color: #fca5a5;
    background: rgba(248, 113, 113, 0.18);
  }

  .diag-chip.siltation,
  .diag-chip.suspected {
    color: #fcd34d;
    background: rgba(251, 191, 36, 0.16);
  }

  .diag-chip.drought {
    color: #6ee7b7;
    background: rgba(16, 185, 129, 0.16);
  }

  .diag-chip.muted {
    color: #cbd5e1;
    background: rgba(148, 163, 184, 0.16);
  }

  .confidence {
    color: #94a3b8;
    font-size: 0.66rem;
    white-space: nowrap;
  }

  .metric-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.35rem;
  }

  .metric-grid div {
    display: grid;
    gap: 0.1rem;
    padding: 0.45rem 0.5rem;
    border: 1px solid rgba(148, 163, 184, 0.12);
    border-radius: 9px;
    background: rgba(148, 163, 184, 0.05);
  }

  .metric-label {
    color: #64748b;
    font-size: 0.56rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .metric-grid strong {
    color: #e2e8f0;
    font-size: 0.76rem;
  }

  .metric-note {
    color: #7f9aa6;
    font-size: 0.62rem;
  }

  .report {
    display: grid;
    gap: 0.35rem;
    padding: 0.65rem;
    border: 1px solid rgba(248, 113, 113, 0.28);
    border-radius: 11px;
    background: rgba(127, 29, 29, 0.14);
  }

  .report p {
    margin: 0;
    color: #cbd5e1;
    font-size: 0.74rem;
    line-height: 1.55;
  }

  .report .action {
    color: #a5f3fc;
    font-size: 0.7rem;
  }

  .report details {
    color: #94a3b8;
    font-size: 0.68rem;
  }

  .report details summary {
    cursor: pointer;
  }

  .report details p {
    margin-top: 0.25rem;
    color: #94a3b8;
  }

  .evidence {
    display: grid;
    gap: 0.18rem;
    margin: 0;
    padding-left: 1rem;
    color: #94a3b8;
    font-size: 0.68rem;
    line-height: 1.5;
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

  .forensics-loading {
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

  .forensics-error {
    display: grid;
    gap: 0.4rem;
    padding: 0.6rem;
    border: 1px solid rgba(248, 113, 113, 0.35);
    border-radius: 10px;
    color: #fecaca;
    background: rgba(127, 29, 29, 0.18);
    font-size: 0.72rem;
  }

  .forensics-error p {
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

  .forensics-warnings {
    display: grid;
    gap: 0.2rem;
  }

  .forensics-warnings p {
    margin: 0;
    color: #fcd34d;
    font-size: 0.66rem;
    line-height: 1.45;
  }

  .empty-note {
    color: #7f9aa6;
    font-size: 0.72rem;
    line-height: 1.55;
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

  @media (max-width: 520px) {
    .metric-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
