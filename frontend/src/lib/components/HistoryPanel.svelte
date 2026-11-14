<script lang="ts">
  import AreaChart from '$lib/charts/AreaChart.svelte';
  import type { HistoryClassification, HistoryView } from '$lib/types';

  export let history: HistoryView | null = null;
  export let loading = false;
  export let error = '';
  export let languageLabel: (code: string) => string = (code) => code;
  export let onPlay: () => void = () => {};
  export let onRetry: () => void = () => {};
  export let onRun: () => void = () => {};

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
      </div>
    {/if}

    {#if history.trend}
      <div class="trend-row">
        <div>
          <span class="metric-label">Trend</span>
          <strong class:negative={history.trend.slope_pct_per_year < 0}>
            {percent(history.trend.slope_pct_per_year)}/yr
          </strong>
        </div>
        <div>
          <span class="metric-label">Multi-year change</span>
          <strong class:negative={history.trend.permanent_change_pct < 0}>
            {percent(history.trend.permanent_change_pct)}
          </strong>
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
            {analysis?.source === 'nvidia' ? 'AI narration' : 'Rule-based'}
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
  {:else if !loading && !error}
    <p class="empty-note">
      Select a lake to load its JRC seasonal baseline, shrinkage flag, and briefing.
    </p>
    <button class="run" on:click={onRun}>Load historical profile</button>
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
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.78rem;
    font-weight: 400;
    letter-spacing: 0.01em;
    color: #fafafa;
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
    font-size: 0.62rem;
    font-weight: 400;
  }

  .badge.demo {
    color: #ffd60a;
    border-color: rgba(255, 159, 10, 0.35);
    background: rgba(255, 159, 10, 0.08);
  }

  .metric-row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.4rem;
  }

  .metric-row div {
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
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .metric-row strong {
    color: #DBDBDB;
    font-size: 0.8rem;
  }

  .trend-row {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 0.4rem;
  }

  .trend-row div {
    display: grid;
    gap: 0.1rem;
    padding: 0.45rem 0.5rem;
    border: 1px solid #2E2E2E;
    border-radius: 0;
    background: #1A1A1A;
  }

  .trend-row strong {
    color: #DBDBDB;
    font-size: 0.8rem;
  }

  .trend-row strong.negative {
    color: #FFB4AB;
  }

  .flag-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 0.6rem;
    padding: 0.55rem 0.6rem;
    border: 1px solid rgba(48, 209, 88, 0.22);
    border-radius: 0;
    background: rgba(48, 209, 88, 0.14);
  }

  .flag-row.flagged {
    border-color: rgba(255, 159, 10, 0.45);
    background: rgba(255, 159, 10, 0.08);
  }

  .flag-chip {
    display: inline-block;
    padding: 0.15rem 0.45rem;
    border-radius: 999px;
    color: #30d158;
    background: rgba(48, 209, 88, 0.14);
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.62rem;
    font-weight: 400;
  }

  .flag-chip.seasonal_drought {
    color: #FFD60A;
    background: rgba(255, 159, 10, 0.14);
  }

  .flag-chip.permanent_loss {
    color: #FFB4AB;
    background: rgba(255, 69, 58, 0.16);
  }

  .flag-chip.insufficient_data {
    color: #AEAEAE;
    background: #2E2E2E;
  }

  .flag-detail {
    margin: 0.3rem 0 0;
    color: #AEAEAE;
    font-size: 0.72rem;
    line-height: 1.5;
  }

  .confidence {
    color: #8F8F8F;
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
    color: #6a6a6a;
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .source-tag {
    color: #8F8F8F;
    font-size: 0.62rem;
  }

  .briefing p {
    margin: 0;
    color: #FAFAFA;
    font-size: 0.78rem;
    line-height: 1.6;
  }

  .briefing details {
    color: #8F8F8F;
    font-size: 0.72rem;
  }

  .briefing details summary {
    cursor: pointer;
  }

  .briefing details p {
    margin-top: 0.3rem;
    color: #8F8F8F;
  }

  .evidence {
    display: grid;
    gap: 0.2rem;
    margin: 0;
    padding-left: 1rem;
    color: #8F8F8F;
    font-size: 0.7rem;
    line-height: 1.5;
  }

  .play {
    justify-self: start;
    border: 1px solid #fafafa;
    border-radius: 0;
    padding: 0.5rem 0.75rem;
    color: #0c0c0c;
    background: #fafafa;
    font-weight: 500;
    font-size: 0.7rem;
  }

  .play:hover {
    background: #dbdbdb;
    border-color: #dbdbdb;
  }

  .audio-note,
  .empty-note {
    color: #8F8F8F;
    font-size: 0.72rem;
    line-height: 1.55;
  }

  .history-loading {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.7rem;
    border: 1px solid #2e2e2e;
    border-radius: 0;
    color: #dbdbdb;
    background: #1a1a1a;
    font-size: 0.72rem;
  }

  .history-loading.compact {
    padding: 0.5rem 0.6rem;
    font-size: 0.7rem;
  }

  .history-error {
    display: grid;
    gap: 0.4rem;
    padding: 0.6rem;
    border: 1px solid rgba(255, 69, 58, 0.35);
    border-radius: 0;
    color: #FFB4AB;
    background: rgba(255, 69, 58, 0.08);
    font-size: 0.74rem;
  }

  .retry {
    justify-self: start;
    border: 1px solid rgba(255, 69, 58, 0.4);
    border-radius: 0;
    padding: 0.3rem 0.6rem;
    color: #FFB4AB;
    background: transparent;
    font-size: 0.7rem;
  }

  .run {
    justify-self: start;
    border: 1px solid #2e2e2e;
    border-radius: 0;
    padding: 0.4rem 0.7rem;
    color: #dbdbdb;
    background: transparent;
    font-size: 0.68rem;
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
