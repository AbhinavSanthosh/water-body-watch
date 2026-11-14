<script lang="ts">
  import type { AlertSeverity, LiveView } from '$lib/types';

  export let live: LiveView | null = null;
  export let loading = false;
  export let error = '';
  export let languageLabel: (code: string) => string = (code) => code;
  export let onPlay: () => void = () => {};
  export let onRetry: () => void = () => {};
  export let onCheckNow: () => void = () => {};

  const SEVERITY_LABEL: Record<AlertSeverity, string> = {
    critical: 'Critical alert',
    watch: 'Watch',
    normal: 'No anomaly'
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

  function dateLabel(value: string | undefined): string {
    if (!value) {
      return '—';
    }
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleDateString('en-IN', {
      day: 'numeric',
      month: 'short',
      year: 'numeric'
    });
  }

  $: alert = live?.alert ?? null;
  $: summary = alert?.localized_summary || alert?.summary_en || '';
  let severity: AlertSeverity = 'normal';
  $: severity = alert?.severity ?? 'normal';
</script>

<div class="live">
  <div class="live-heading">
    <div>
      <h2>Live NDWI check</h2>
      <p class="live-sub">Satellite water detection over the exact lakebed</p>
    </div>
    <div class="live-actions">
      <span class="badge" class:demo={live?.source === 'synthetic-demo'}>
        {live?.source === 'sentinel-hub' ? 'Sentinel-2 L2A' : live?.source ? 'Synthetic scene' : 'idle'}
      </span>
      <button class="refresh" on:click={onCheckNow} disabled={loading}>
        {loading ? 'Checking…' : 'Check now'}
      </button>
    </div>
  </div>

  {#if error}
    <div class="live-error">
      <p>{error}</p>
      <button class="retry" on:click={onRetry}>Retry</button>
    </div>
  {/if}

  {#if loading && !live?.scene}
    <div class="live-loading">
      <span class="spinner"></span>
      Fetching the latest scene and counting NDWI pixels…
    </div>
  {/if}

  {#if live?.scene}
    {#if alert}
      <div class="alert {severity}" class:pulsing={live.flag?.flag}>
        <div class="alert-top">
          <span class="alert-pill">{SEVERITY_LABEL[severity]}</span>
          {#if live.flag?.flag}
            <span class="pulse-dot"></span>
          {/if}
        </div>
        <h3>{alert.headline}</h3>
        <p>{summary}</p>
        <p class="action">→ {alert.recommended_action}</p>
        {#if alert.localized_summary}
          <details>
            <summary>English original ({languageLabel(alert.language)})</summary>
            <p>{alert.summary_en}</p>
          </details>
        {/if}
        {#if alert.audio_base64}
          <button class="play" on:click={onPlay}>▶ Play alert</button>
        {/if}
      </div>
    {/if}

    {#if live.flag}
      <div class="live-metrics">
        <div>
          <span class="metric-label">Live area</span>
          <strong>{hectares(live.flag.area_m2)} ha</strong>
        </div>
        <div>
          <span class="metric-label">Same-month baseline</span>
          <strong>{hectares(live.flag.baseline_area_m2)} ha</strong>
        </div>
        <div>
          <span class="metric-label">Change</span>
          <strong class:below={live.flag.drop_pct > live.flag.threshold_pct}>
            {changePercent(live.flag.drop_pct)}
          </strong>
        </div>
        <div>
          <span class="metric-label">NDWI pixels</span>
          <strong>{live.flag.water_pixels.toLocaleString('en-IN')}</strong>
        </div>
      </div>

      <p class="scene-line">
        {dateLabel(live.scene.observed_at)} · {live.scene.cloud_pct.toFixed(0)}% cloud
      </p>

      {#if live.flag.evidence.length}
        <ul class="evidence">
          {#each live.flag.evidence as item}
            <li>{item}</li>
          {/each}
        </ul>
      {/if}
    {/if}
  {:else if !loading && !error}
    <p class="empty-note">Select a lake to run a live NDWI check against its seasonal baseline.</p>
    <button class="run" on:click={onCheckNow}>Run live check</button>
  {/if}
</div>

<style>
  .live {
    display: grid;
    gap: 0.55rem;
  }

  .live-heading {
    display: flex;
    align-items: flex-start;
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

  .live-sub {
    margin: 0.15rem 0 0;
    color: #6A6A6A;
    font-size: 0.66rem;
  }

  .live-actions {
    display: flex;
    align-items: center;
    gap: 0.4rem;
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

  .refresh,
  .retry,
  .play {
    border: 1px solid #2e2e2e;
    border-radius: 0;
    font-weight: 500;
    cursor: pointer;
  }

  .refresh {
    padding: 0.32rem 0.55rem;
    color: #fafafa;
    background: #242424;
    font-size: 0.62rem;
  }

  .refresh:hover:not(:disabled) {
    background: #2e2e2e;
  }

  .refresh:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  .alert {
    display: grid;
    gap: 0.35rem;
    padding: 0.7rem;
    border: 1px solid #2E2E2E;
    border-radius: 0;
    background: #1A1A1A;
  }

  .alert.critical {
    border-color: rgba(255, 69, 58, 0.5);
    background: rgba(255, 69, 58, 0.1);
  }

  .alert.watch {
    border-color: rgba(255, 159, 10, 0.4);
    background: rgba(255, 159, 10, 0.07);
  }

  .alert-top {
    display: flex;
    align-items: center;
    gap: 0.4rem;
  }

  .alert-pill {
    padding: 0.15rem 0.45rem;
    border-radius: 999px;
    color: #ffb4ab;
    background: rgba(255, 69, 58, 0.18);
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.58rem;
    font-weight: 400;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  .alert.watch .alert-pill {
    color: #FFD60A;
    background: rgba(255, 159, 10, 0.18);
  }

  .alert.normal .alert-pill {
    color: #30D158;
    background: rgba(48, 209, 88, 0.16);
  }

  .pulse-dot {
    width: 0.45rem;
    height: 0.45rem;
    border-radius: 999px;
    background: #FF453A;
    animation: dot-pulse 1.1s infinite;
  }

  .alert h3 {
    margin: 0;
    color: #FFB4AB;
    font-size: 0.82rem;
    line-height: 1.35;
  }

  .alert.watch h3,
  .alert.normal h3 {
    color: #FAFAFA;
  }

  .alert p {
    margin: 0;
    color: #AEAEAE;
    font-size: 0.74rem;
    line-height: 1.55;
  }

  .alert .action {
    color: #FAFAFA;
    font-size: 0.7rem;
  }

  .alert details {
    color: #8F8F8F;
    font-size: 0.68rem;
  }

  .alert details summary {
    cursor: pointer;
  }

  .alert details p {
    margin-top: 0.25rem;
    color: #8F8F8F;
  }

  .play {
    justify-self: start;
    margin-top: 0.15rem;
    padding: 0.42rem 0.68rem;
    color: #0c0c0c;
    background: #fafafa;
    border-color: #fafafa;
    font-size: 0.66rem;
  }

  .play:hover {
    background: #dbdbdb;
    border-color: #dbdbdb;
  }

  .live-metrics {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.35rem;
  }

  .live-metrics div {
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

  .live-metrics strong {
    color: #DBDBDB;
    font-size: 0.76rem;
  }

  .live-metrics strong.below {
    color: #FFB4AB;
  }

  .scene-line {
    margin: 0;
    color: #8F8F8F;
    font-size: 0.66rem;
    line-height: 1.5;
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

  .live-loading {
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

  .live-error {
    display: grid;
    gap: 0.4rem;
    padding: 0.6rem;
    border: 1px solid rgba(255, 69, 58, 0.35);
    border-radius: 0;
    color: #FFB4AB;
    background: rgba(255, 69, 58, 0.08);
    font-size: 0.72rem;
  }

  .live-error p {
    margin: 0;
  }

  .retry {
    justify-self: start;
    padding: 0.3rem 0.6rem;
    border: 1px solid rgba(255, 69, 58, 0.4);
    color: #FFB4AB;
    background: transparent;
    font-size: 0.68rem;
  }

  .run {
    justify-self: start;
    padding: 0.4rem 0.7rem;
    border: 1px solid #2e2e2e;
    border-radius: 0;
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

  @keyframes dot-pulse {
    0%,
    100% {
      box-shadow: 0 0 0 0 rgba(255, 69, 58, 0.5);
      opacity: 1;
    }
    50% {
      box-shadow: 0 0 0 7px rgba(255, 69, 58, 0);
      opacity: 0.6;
    }
  }

  @media (max-width: 520px) {
    .live-metrics {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }
</style>
