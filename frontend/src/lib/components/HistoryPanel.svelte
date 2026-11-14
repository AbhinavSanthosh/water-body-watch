<script lang="ts">
  import AreaChart from '$lib/charts/AreaChart.svelte';
  import type { HistoryClassification, HistoryView } from '$lib/types';

  export let history: HistoryView | null = null;
  export let loading = false;
  export let error = '';
  export let languageLabel: (code: string) => string = (code) => code;
  export let onPlay: () => void = () => {};
  export let onRetry: () => void = () => {};

  const CLASSIFICATION_LABEL: Record<HistoryClassification, string> = {
    normal: 'Within seasonal tolerance',
    seasonal_drought: 'Seasonal low',
    permanent_loss: 'Possible permanent loss',
    insufficient_data: 'Insufficient history'
  };

  function hectares(value: number | undefined | null): string {
    if (typeof value !== 'number') {
      return '—';
    }
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 }).format(
      value / 10_000
    );
  }

  function percent(value: number | undefined | null): string {
    if (typeof value !== 'number') {
      return '—';
    }
    return `${value >= 0 ? '+' : ''}${value.toFixed(1)}%`;
  }

  /** `drop_pct` is positive when the lake shrank; show that as a signed change. */
  function changePercent(dropPct: number | undefined | null): string {
    if (typeof dropPct !== 'number') {
      return '—';
    }
    return percent(-dropPct);
  }

  function sourceLabel(source: string | undefined): string {
    if (!source) {
      return 'loading';
    }
    return source === 'jrc-gsw' ? 'JRC Global Surface Water' : 'Synthetic demo series';
  }

  $: analysis = history?.analysis ?? null;
  $: summary = analysis?.localized_summary || analysis?.summary_en || '';
  $: classification = history?.flag?.classification ?? null;
</script>

<div class="history">
  <div class="history-heading">
    <h2>Historical profile</h2>
    <span class="badge" class:demo={history?.source === 'synthetic-demo'}>
      {sourceLabel(history?.source)}
    </span>
  </div>

  {#if error}
    <div class="history-error">
      <p>{error}</p>
      <button class="retry" on:click={onRetry}>Retry</button>
    </div>
  {/if}

  {#if loading && !history}
    <div class="history-loading">
      <span class="spinner"></span>
      Querying the JRC baseline and building the seasonal profile…
    </div>
  {/if}

  {#if history?.series?.length}
    <AreaChart
      points={history.series}
      baseline={history.baseline}
      trend={history.trend}
    />

    {#if history.baseline}
      <div class="metric-row">
        <div>
          <span class="metric-label">Seasonal swing</span>
          <strong>{history.baseline.amplitude_pct.toFixed(0)}%</strong>
        </div>
        <div>
          <span class="metric-label">Natural low</span>
          <strong>{hectares(history.baseline.survival_threshold_m2)} ha</strong>
        </div>
        <div>
          <span class="metric-label">Window</span>
          <strong>{history.baseline.window_years} yrs</strong>
        </div>
      </div>
    {/if}

    {#if history.flag}
      <div class="flag-row" class:flagged={history.flag.flag}>
        <div>
          <span class="flag-chip {classification}">
            {classification ? CLASSIFICATION_LABEL[classification] : 'Evaluating'}
          </span>
          <p class="flag-detail">
            {hectares(history.flag.area_m2)} ha now vs
            {hectares(history.flag.baseline_area_m2)} ha same-month baseline
            ({changePercent(history.flag.drop_pct)}).
          </p>
        </div>
        <span class="confidence">{Math.round(history.flag.confidence * 100)}% conf.</span>
      </div>
    {/if}

    {#if loading && !analysis}
      <div class="history-loading compact">
        <span class="spinner"></span>
        Writing the trend briefing…
      </div>
    {/if}

    {#if summary}
      <div class="briefing">
        <div class="briefing-head">
          <span class="eyebrow">
            {analysis?.localized_summary ? languageLabel(analysis.language) : 'English'} briefing
          </span>
          <span class="source-tag">
            {analysis?.source === 'nvidia' ? 'NVIDIA NeMo' : 'Deterministic'}
          </span>
        </div>
        <p>{summary}</p>
        {#if analysis?.localized_summary && analysis.summary_en}
          <details>
            <summary>English original</summary>
            <p>{analysis.summary_en}</p>
          </details>
        {/if}
        {#if analysis?.evidence?.length}
          <ul class="evidence">
            {#each analysis.evidence as item}
              <li>{item}</li>
            {/each}
          </ul>
        {/if}
        {#if analysis?.audio_base64}
          <button class="play" on:click={onPlay}>▶ Play briefing</button>
        {:else if analysis}
          <p class="audio-note">Audio briefing unavailable for this run.</p>
        {/if}
      </div>
    {/if}

    {#if history.warnings?.length}
      <div class="history-warnings">
        {#each history.warnings as warning}
          <p>⚠ {warning}</p>
        {/each}
      </div>
    {/if}
  {:else if !loading && !error}
    <p class="empty-note">
      Select a lake to load its JRC seasonal baseline, shrinkage flag, and briefing.
    </p>
  {/if}
</div>

<style>
  .history {
    display: grid;
    gap: 0.55rem;
  }

  .history-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.6rem;
  }

  h2 {
    margin: 0;
    font-size: 0.9rem;
    letter-spacing: -0.01em;
  }

  .badge {
    padding: 0.2rem 0.5rem;
    border-radius: 999px;
    color: #a5f3fc;
    background: rgba(34, 211, 238, 0.12);
    font-size: 0.66rem;
    font-weight: 650;
  }

  .badge.demo {
    color: #fcd34d;
    background: rgba(251, 191, 36, 0.12);
  }

  .metric-row {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 0.4rem;
  }

  .metric-row div {
    display: grid;
    gap: 0.1rem;
    padding: 0.45rem 0.5rem;
    border: 1px solid rgba(148, 163, 184, 0.12);
    border-radius: 9px;
    background: rgba(148, 163, 184, 0.05);
  }

  .metric-label {
    color: #64748b;
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .metric-row strong {
    color: #e2e8f0;
    font-size: 0.8rem;
  }

  .flag-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.6rem;
    padding: 0.55rem 0.6rem;
    border: 1px solid rgba(52, 211, 153, 0.22);
    border-radius: 10px;
    background: rgba(6, 78, 59, 0.14);
  }

  .flag-row.flagged {
    border-color: rgba(251, 146, 60, 0.4);
    background: rgba(124, 45, 18, 0.18);
  }

  .flag-chip {
    display: inline-block;
    padding: 0.15rem 0.45rem;
    border-radius: 999px;
    color: #6ee7b7;
    background: rgba(16, 185, 129, 0.14);
    font-size: 0.66rem;
    font-weight: 700;
  }

  .flag-chip.seasonal_drought {
    color: #fcd34d;
    background: rgba(251, 191, 36, 0.14);
  }

  .flag-chip.permanent_loss {
    color: #fca5a5;
    background: rgba(248, 113, 113, 0.16);
  }

  .flag-chip.insufficient_data {
    color: #cbd5e1;
    background: rgba(148, 163, 184, 0.16);
  }

  .flag-detail {
    margin: 0.3rem 0 0;
    color: #cbd5e1;
    font-size: 0.72rem;
    line-height: 1.5;
  }

  .confidence {
    color: #94a3b8;
    font-size: 0.66rem;
    white-space: nowrap;
  }

  .briefing {
    display: grid;
    gap: 0.4rem;
    padding-top: 0.1rem;
  }

  .briefing-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
  }

  .eyebrow {
    color: #64748b;
    font-size: 0.64rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .source-tag {
    color: #7f9aa6;
    font-size: 0.62rem;
  }

  .briefing p {
    margin: 0;
    color: #dbeafe;
    font-size: 0.78rem;
    line-height: 1.6;
  }

  .briefing details {
    color: #94a3b8;
    font-size: 0.72rem;
  }

  .briefing details summary {
    cursor: pointer;
  }

  .briefing details p {
    margin-top: 0.3rem;
    color: #94a3b8;
  }

  .evidence {
    display: grid;
    gap: 0.2rem;
    margin: 0;
    padding-left: 1rem;
    color: #94a3b8;
    font-size: 0.7rem;
    line-height: 1.5;
  }

  .play {
    justify-self: start;
    border: 0;
    border-radius: 9px;
    padding: 0.5rem 0.75rem;
    color: #032b33;
    background: #a5f3fc;
    font-weight: 650;
    font-size: 0.74rem;
  }

  .audio-note,
  .empty-note {
    color: #7f9aa6;
    font-size: 0.72rem;
    line-height: 1.55;
  }

  .history-loading {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.7rem;
    border: 1px solid rgba(34, 211, 238, 0.18);
    border-radius: 10px;
    color: #a5f3fc;
    background: rgba(8, 47, 58, 0.4);
    font-size: 0.74rem;
  }

  .history-loading.compact {
    padding: 0.5rem 0.6rem;
    font-size: 0.7rem;
  }

  .history-error {
    display: grid;
    gap: 0.4rem;
    padding: 0.6rem;
    border: 1px solid rgba(248, 113, 113, 0.35);
    border-radius: 10px;
    color: #fecaca;
    background: rgba(127, 29, 29, 0.18);
    font-size: 0.74rem;
  }

  .retry {
    justify-self: start;
    border: 1px solid rgba(248, 113, 113, 0.4);
    border-radius: 8px;
    padding: 0.3rem 0.6rem;
    color: #fecaca;
    background: transparent;
    font-size: 0.7rem;
  }

  .history-warnings {
    display: grid;
    gap: 0.2rem;
  }

  .history-warnings p {
    margin: 0;
    color: #fcd34d;
    font-size: 0.68rem;
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
