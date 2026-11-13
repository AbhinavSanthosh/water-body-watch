<script lang="ts">
  import { onMount } from 'svelte';
  import MapView from '$lib/components/MapView.svelte';
  import HistoryPanel from '$lib/components/HistoryPanel.svelte';
  import { getHealth, getHistory, getLanguages, historySocketUrl, queryText, queryVoice } from '$lib/api';
  import { WavRecorder } from '$lib/recorder';
  import type {
    DiscoveryResponse,
    HealthResponse,
    HistoryStreamMessage,
    HistoryView,
    LanguageOption,
    TrendAnalysis,
    WaterBodyFeature
  } from '$lib/types';

  let language = 'kn';
  let text = 'Map the lakes near Mysore Road, Bangalore';
  let demo = false;
  let loading = false;
  let error = '';
  let result: DiscoveryResponse | null = null;
  let selectedId: string | null = null;
  let backendOnline = false;
  let healthInfo: HealthResponse | null = null;
  let recorder: WavRecorder | null = null;
  let recording = false;
  let elapsed = 0;
  let startedAt = 0;
  let timer: ReturnType<typeof setInterval> | null = null;
  let autoStopTimer: ReturnType<typeof setTimeout> | null = null;
  let confirmationAudio: HTMLAudioElement | null = null;
  let briefingAudio: HTMLAudioElement | null = null;

  let history: HistoryView | null = null;
  let historyLoading = false;
  let historyError = '';
  let historySocket: WebSocket | null = null;

  let languages: LanguageOption[] = [
    { code: 'kn', label: 'Kannada' },
    { code: 'hi', label: 'Hindi' },
    { code: 'ta', label: 'Tamil' },
    { code: 'te', label: 'Telugu' },
    { code: 'ml', label: 'Malayalam' },
    { code: 'mr', label: 'Marathi' },
    { code: 'bn', label: 'Bengali' },
    { code: 'gu', label: 'Gujarati' },
    { code: 'pa', label: 'Punjabi' },
    { code: 'or', label: 'Odia' },
    { code: 'en', label: 'English' }
  ];

  $: features = result?.water_bodies.features ?? [];
  $: selected =
    features.find(
      (feature: WaterBodyFeature) =>
        feature.properties.water_body_id === selectedId
    ) ?? null;

  onMount(() => {
    void initialize();
    return () => {
      clearTimers();
      closeHistorySocket();
      recorder?.cancel();
      confirmationAudio?.pause();
      briefingAudio?.pause();
    };
  });

  async function initialize(): Promise<void> {
    try {
      healthInfo = await getHealth();
      backendOnline = true;
    } catch {
      backendOnline = false;
    }
    try {
      languages = await getLanguages();
    } catch {
      // Keep the built-in language list when the backend is offline.
    }
  }

  function clearTimers(): void {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    if (autoStopTimer) {
      clearTimeout(autoStopTimer);
      autoStopTimer = null;
    }
  }

  function playConfirmation(): void {
    if (!result?.confirmation_audio_base64) {
      return;
    }
    confirmationAudio?.pause();
    const mime = result.confirmation_audio_mime || 'audio/wav';
    confirmationAudio = new Audio(
      `data:${mime};base64,${result.confirmation_audio_base64}`
    );
    confirmationAudio.play().catch(() => {
      // Browsers may block autoplay; the replay button remains available.
    });
  }

  function playBriefing(analysis: TrendAnalysis | null = history?.analysis ?? null): void {
    if (!analysis?.audio_base64) {
      return;
    }
    briefingAudio?.pause();
    const mime = analysis.audio_mime || 'audio/wav';
    briefingAudio = new Audio(`data:${mime};base64,${analysis.audio_base64}`);
    briefingAudio.play().catch(() => {
      // Autoplay may be blocked; the panel keeps a replay button.
    });
  }

  function closeHistorySocket(): void {
    if (!historySocket) {
      return;
    }
    historySocket.onclose = null;
    historySocket.onerror = null;
    historySocket.onmessage = null;
    historySocket.close();
    historySocket = null;
  }

  function emptyHistory(id: string, name: string): HistoryView {
    return {
      water_body_id: id,
      name,
      source: '',
      index_version: '',
      series: [],
      baseline: null,
      trend: null,
      flag: null,
      analysis: null,
      steps: [],
      warnings: [],
      cached: false
    };
  }

  function applyHistoryMessage(message: HistoryStreamMessage, id: string): void {
    if (!history || history.water_body_id !== id) {
      return;
    }
    switch (message.type) {
      case 'historical':
        history = {
          ...history,
          source: message.source,
          index_version: message.index_version,
          series: message.series,
          baseline: message.baseline,
          trend: message.trend,
          steps: message.steps,
          warnings: message.warnings
        };
        break;
      case 'flag':
        history = { ...history, flag: message.flag };
        break;
      case 'briefing':
        history = { ...history, analysis: message.analysis };
        playBriefing(message.analysis);
        break;
      case 'error':
        historyError = message.detail;
        break;
    }
  }

  async function fallbackHistory(id: string): Promise<void> {
    try {
      const profile = await getHistory(id, language, demo, true);
      if (history?.water_body_id !== id) {
        return;
      }
      history = {
        water_body_id: profile.water_body_id,
        name: profile.name,
        source: profile.source,
        index_version: profile.index_version,
        series: profile.series,
        baseline: profile.baseline,
        trend: profile.trend,
        flag: profile.flag,
        analysis: profile.analysis,
        steps: profile.steps,
        warnings: profile.warnings,
        cached: profile.cached
      };
    } catch (err) {
      if (history?.water_body_id === id) {
        historyError =
          err instanceof Error ? err.message : 'Historical profile request failed.';
      }
    }
  }

  function loadHistory(id: string): void {
    const feature = features.find(
      (item: WaterBodyFeature) => item.properties.water_body_id === id
    );
    closeHistorySocket();
    history = emptyHistory(id, feature?.properties.name ?? id);
    historyError = '';
    historyLoading = true;

    let complete = false;
    const socket = new WebSocket(historySocketUrl(id, language, demo, true));
    historySocket = socket;

    socket.onmessage = (event: MessageEvent) => {
      let message: HistoryStreamMessage;
      try {
        message = JSON.parse(String(event.data)) as HistoryStreamMessage;
      } catch {
        return;
      }
      if (history?.water_body_id !== id) {
        return;
      }
      if (message.type === 'briefing') {
        complete = true;
        historyLoading = false;
      }
      applyHistoryMessage(message, id);
    };

    socket.onerror = () => {
      // `onclose` decides whether to fall back to REST.
    };

    socket.onclose = () => {
      if (historySocket !== socket) {
        return;
      }
      historySocket = null;
      if (complete) {
        historyLoading = false;
        return;
      }
      void fallbackHistory(id).finally(() => {
        if (history?.water_body_id === id) {
          historyLoading = false;
        }
      });
    };
  }

  function selectLake(id: string): void {
    if (selectedId === id && history?.water_body_id === id) {
      return;
    }
    selectedId = id;
    loadHistory(id);
  }

  function handleLanguageChange(event: Event): void {
    language = (event.currentTarget as HTMLSelectElement).value;
    if (selectedId) {
      loadHistory(selectedId);
    }
  }

  function afterResult(): void {
    const firstId =
      result?.water_bodies.features[0]?.properties.water_body_id ?? null;
    selectedId = firstId;
    error = '';
    playConfirmation();
    if (firstId) {
      loadHistory(firstId);
    }
  }

  function resetHistory(): void {
    closeHistorySocket();
    history = null;
    historyLoading = false;
    historyError = '';
  }

  async function runQuery(): Promise<void> {
    if (!text.trim() || loading) {
      return;
    }
    loading = true;
    error = '';
    result = null;
    resetHistory();
    try {
      result = await queryText(text.trim(), language, demo);
      afterResult();
    } catch (err) {
      error = err instanceof Error ? err.message : 'Discovery request failed.';
    } finally {
      loading = false;
    }
  }

  async function toggleMic(): Promise<void> {
    if (recording) {
      await stopRecording();
      return;
    }
    error = '';
    result = null;
    resetHistory();
    recorder = new WavRecorder();
    try {
      await recorder.start();
      recording = true;
      elapsed = 0;
      startedAt = Date.now();
      timer = setInterval(() => {
        elapsed = Math.floor((Date.now() - startedAt) / 1000);
      }, 250);
      autoStopTimer = setTimeout(() => {
        void stopRecording();
      }, 20_000);
    } catch (err) {
      recorder = null;
      error =
        err instanceof Error
          ? `Microphone unavailable: ${err.message}`
          : 'Microphone unavailable.';
    }
  }

  async function stopRecording(): Promise<void> {
    if (!recorder || !recording) {
      return;
    }
    recording = false;
    clearTimers();
    const activeRecorder = recorder;
    recorder = null;
    try {
      const audio = await activeRecorder.stop();
      if (audio.size < 1_000) {
        error = 'That recording was too short. Hold the button and speak for a few seconds.';
        return;
      }
      loading = true;
      result = await queryVoice(audio, language, demo);
      afterResult();
    } catch (err) {
      error = err instanceof Error ? err.message : 'Voice request failed.';
    } finally {
      loading = false;
    }
  }

  function runDemo(): void {
    demo = true;
    language = 'en';
    text = 'Map the lakes in Bengaluru';
    void runQuery();
  }

  function runExample(): void {
    language = 'en';
    text = 'Map the lakes near Mysore Road, Bangalore';
    void runQuery();
  }

  function handleTextKeydown(event: KeyboardEvent): void {
    if (event.key === 'Enter') {
      void runQuery();
    }
  }

  function formatHectares(value: number): string {
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(value);
  }

  function languageLabel(code: string): string {
    return languages.find((item) => item.code === code)?.label ?? code;
  }
</script>

<svelte:head>
  <title>Water Body Watch · Discovery &amp; History</title>
  <meta
    name="description"
    content="Speak a location, discover urban lakes, and read a decade of JRC seasonal baselines and shrinkage briefings."
  />
</svelte:head>

<div class="page">
  <header class="topbar">
    <div class="brand">
      <span class="brand-mark">◉</span>
      <div>
        <h1>Water Body Watch</h1>
        <p>Phase 2 · discovery, JRC baselines &amp; trend briefings</p>
      </div>
    </div>
    <div class="health" class:online={backendOnline}>
      <span class="dot"></span>
      {#if backendOnline}
        Backend online
      {:else}
        Backend offline
      {/if}
      {#if healthInfo}
        <span class="health-detail">
          · Bhashini {healthInfo.bhashini_configured ? 'ready' : 'not configured'} · NVIDIA
          {healthInfo.nvidia_configured ? 'ready' : 'fallback parser'} · JRC
          {healthInfo.gee_configured ? 'ready' : 'synthetic demo'}
        </span>
      {/if}
    </div>
  </header>

  <section class="query-card">
    <div class="query-row">
      <label class="language-field">
        <span>Speak in</span>
        <select bind:value={language} on:change={handleLanguageChange} disabled={recording || loading}>
          {#each languages as option}
            <option value={option.code}>{option.label}</option>
          {/each}
        </select>
      </label>

      <button
        class="mic-button"
        class:recording
        on:click={toggleMic}
        disabled={loading}
        title={recording ? 'Stop and send' : 'Record a voice request'}
      >
        <span class="mic-icon">{recording ? '■' : '●'}</span>
        {recording ? `Stop (${elapsed}s)` : 'Speak'}
      </button>

      <input
        class="text-input"
        bind:value={text}
        on:keydown={handleTextKeydown}
        placeholder="e.g. Map the lakes near Mysore Road, Bangalore"
        disabled={loading || recording}
      />

      <button class="search-button" on:click={runQuery} disabled={loading || recording || !text.trim()}>
        {loading ? 'Discovering…' : 'Map water bodies'}
      </button>
    </div>

    <div class="query-meta">
      <label class="demo-toggle">
        <input type="checkbox" bind:checked={demo} disabled={recording || loading} />
        Use bundled Bengaluru OSM demo data
      </label>
      <span>Type in English, or record in the selected language; Bhashini translates voice requests.</span>
    </div>

    {#if recording}
      <div class="recording-bar">
        <span class="pulse"></span>
        Listening in {languageLabel(language)}… speak naturally, then press stop.
      </div>
    {/if}

    {#if error}
      <div class="error-box">{error}</div>
    {/if}
  </section>

  {#if loading}
    <div class="loading-box">
      <span class="spinner"></span>
      Transcribing, parsing intent, querying OSM, and building lakebed masks…
    </div>
  {/if}

  {#if result}
    <section class="stat-grid">
      <div class="stat">
        <span class="stat-label">Water bodies</span>
        <strong>{result.count}</strong>
        <span class="stat-note">polygons ready to monitor</span>
      </div>
      <div class="stat">
        <span class="stat-label">Resolved place</span>
        <strong class="stat-place">{result.location.display_name.split(',')[0]}</strong>
        <span class="stat-note">{result.location.result_type || 'location'}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Intent source</span>
        <strong>{result.steps.find((step) => step.name === 'intent')?.status === 'warning' ? 'Local parser' : 'NVIDIA NeMo'}</strong>
        <span class="stat-note">{result.intent.target_entity.replace('_', ' ')}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Confirmation</span>
        <strong>{result.confirmation_audio_base64 ? 'Voice ready' : 'Text only'}</strong>
        <span class="stat-note">{languageLabel(result.language)}</span>
      </div>
    </section>

    <section class="workspace">
      <div class="map-panel">
        <MapView
          data={result.water_bodies}
          {selectedId}
          onselect={(id) => (selectedId = id)}
        />
      </div>

      <aside class="side-panel">
        <div class="panel">
          <div class="panel-heading">
            <h2>Discovered lakes</h2>
            <span class="badge">{result.count}</span>
          </div>
          <p class="place-line">{result.location.display_name}</p>

          {#if features.length === 0}
            <p class="empty-note">No water bodies matched. Try a wider area or a nearby landmark.</p>
          {:else}
            <ul class="result-list">
              {#each features as feature (feature.properties.water_body_id)}
                <li>
                  <button
                    class="result-item"
                    class:active={feature.properties.water_body_id === selectedId}
                    on:click={() => selectLake(feature.properties.water_body_id)}
                  >
                    <span class="result-name">{feature.properties.name}</span>
                    <span class="result-area">
                      {formatHectares(feature.properties.area_hectares)} ha
                    </span>
                  </button>
                </li>
              {/each}
            </ul>
          {/if}
        </div>

        {#if selected}
          <div class="panel detail-panel">
            <div class="panel-heading">
              <h2>{selected.properties.name}</h2>
              <span class="badge muted">{selected.properties.water_type}</span>
            </div>
            <dl class="detail-list">
              <div>
                <dt>Surface area</dt>
                <dd>{formatHectares(selected.properties.area_hectares)} hectares</dd>
              </div>
              <div>
                <dt>OSM feature</dt>
                <dd>
                  <a
                    href={`https://www.openstreetmap.org/${selected.properties.osm_type}/${selected.properties.osm_id}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {selected.properties.water_body_id}
                  </a>
                </dd>
              </div>
              <div>
                <dt>Mask bbox</dt>
                <dd class="mono">
                  {selected.properties.bbox.map((value) => value.toFixed(5)).join(', ')}
                </dd>
              </div>
              <div>
                <dt>Centroid</dt>
                <dd class="mono">
                  {selected.properties.centroid.map((value) => value.toFixed(5)).join(', ')}
                </dd>
              </div>
            </dl>
            <p class="mask-note">
              The exact lakebed polygon is stored as an EPSG:4326 mask; the bbox is only for
              satellite request extents. The history panel clips JRC monthly water to this mask.
            </p>
          </div>
        {/if}

        <div class="panel history-panel">
          <HistoryPanel
            history={history}
            loading={historyLoading}
            error={historyError}
            {languageLabel}
            onPlay={() => playBriefing()}
            onRetry={() => selectedId && loadHistory(selectedId)}
          />
        </div>

        <div class="panel confirmation-panel">
          <div class="panel-heading">
            <h2>Voice confirmation</h2>
          </div>
          <p>{result.confirmation_text}</p>
          {#if result.confirmation_audio_base64}
            <button class="play-button" on:click={playConfirmation}>▶ Play confirmation</button>
          {:else if healthInfo?.bhashini_configured}
            <p class="empty-note">
              Bhashini is configured but did not return audio. Check the pipeline warnings below.
            </p>
          {:else}
            <p class="empty-note">Audio is unavailable. Configure Bhashini to hear this message.</p>
          {/if}
        </div>
      </aside>
    </section>

    <section class="trace-panel">
      <div class="panel-heading">
        <h2>Pipeline trace</h2>
        <span class="badge muted">Phase 1</span>
      </div>
      <div class="trace-grid">
        {#each result.steps as step}
          <div class="trace-step" class:warning={step.status === 'warning'}>
            <span class="trace-dot"></span>
            <div>
              <strong>{step.name.replaceAll('_', ' ')}</strong>
              <p>{step.detail}</p>
            </div>
          </div>
        {/each}
      </div>

      <div class="transcript-grid">
        {#if result.transcript}
          <div>
            <span class="eyebrow">Bhashini transcript</span>
            <p class="transcript">{result.transcript}</p>
          </div>
        {/if}
        <div>
          <span class="eyebrow">English query</span>
          <p class="transcript">{result.english_text}</p>
        </div>
      </div>

      <details class="intent-details">
        <summary>Structured intent JSON</summary>
        <pre>{JSON.stringify(result.intent, null, 2)}</pre>
      </details>

      {#if result.warnings.length > 0}
        <div class="warning-list">
          {#each result.warnings as warning}
            <p>⚠ {warning}</p>
          {/each}
        </div>
      {/if}
    </section>
  {:else if !loading}
    <section class="empty-state">
      <div class="empty-mark">◎</div>
      <h2>Start with a place, not a coordinate</h2>
      <p>
        Speak in Kannada, Hindi, Tamil, or another supported language. The request is translated,
        parsed into a location, and matched to OSM lake and pond polygons.
      </p>
      <div class="empty-actions">
        <button class="search-button" on:click={runExample} disabled={!text.trim()}>
          Try the example query
        </button>
        <button class="ghost-button" on:click={runDemo}>Load Bengaluru demo</button>
      </div>
    </section>
  {/if}
</div>

<style>
  .page {
    width: min(1500px, calc(100% - 2.5rem));
    margin: 0 auto;
    padding: 1.5rem 0 3rem;
  }

  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 0.4rem 0 1.25rem;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 0.85rem;
  }

  .brand-mark {
    display: grid;
    place-items: center;
    width: 2.8rem;
    height: 2.8rem;
    border: 1px solid rgba(34, 211, 238, 0.45);
    border-radius: 14px;
    color: #22d3ee;
    background: rgba(8, 47, 58, 0.65);
    box-shadow: 0 0 28px rgba(34, 211, 238, 0.12);
  }

  h1,
  h2,
  p {
    margin: 0;
  }

  h1 {
    font-size: 1.25rem;
    letter-spacing: -0.02em;
  }

  .brand p {
    color: #7f9aa6;
    font-size: 0.78rem;
    margin-top: 0.18rem;
  }

  .health {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    padding: 0.5rem 0.75rem;
    border: 1px solid rgba(148, 163, 184, 0.18);
    border-radius: 999px;
    color: #94a3b8;
    font-size: 0.75rem;
    white-space: nowrap;
  }

  .health .dot {
    width: 0.48rem;
    height: 0.48rem;
    border-radius: 999px;
    background: #f87171;
  }

  .health.online .dot {
    background: #34d399;
    box-shadow: 0 0 12px rgba(52, 211, 153, 0.8);
  }

  .health-detail {
    color: #64748b;
  }

  .query-card,
  .panel,
  .trace-panel,
  .stat,
  .empty-state {
    border: 1px solid rgba(148, 163, 184, 0.16);
    border-radius: 18px;
    background: linear-gradient(180deg, rgba(15, 38, 46, 0.92), rgba(8, 24, 30, 0.92));
    box-shadow: 0 24px 60px rgba(0, 0, 0, 0.18);
  }

  .query-card {
    padding: 1rem;
    margin-bottom: 1rem;
  }

  .query-row {
    display: grid;
    grid-template-columns: auto auto minmax(180px, 1fr) auto;
    gap: 0.65rem;
    align-items: end;
  }

  .language-field {
    display: grid;
    gap: 0.3rem;
    color: #7f9aa6;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .language-field select {
    min-width: 9rem;
    padding: 0.72rem 0.8rem;
    border: 1px solid rgba(148, 163, 184, 0.22);
    border-radius: 11px;
    color: #e2e8f0;
    background: #0b2028;
  }

  .mic-button,
  .search-button,
  .play-button,
  .ghost-button {
    border: 0;
    border-radius: 11px;
    padding: 0.78rem 1rem;
    font-weight: 650;
    transition: transform 160ms ease, box-shadow 160ms ease, background 160ms ease;
  }

  .mic-button {
    color: #042f2e;
    background: #67e8f9;
  }

  .mic-button:hover:not(:disabled),
  .search-button:hover:not(:disabled),
  .play-button:hover:not(:disabled) {
    transform: translateY(-1px);
  }

  .mic-button.recording {
    color: #fff;
    background: #ef4444;
    animation: mic-pulse 1.4s infinite;
  }

  .mic-icon {
    margin-right: 0.35rem;
  }

  .text-input {
    width: 100%;
    padding: 0.78rem 0.9rem;
    border: 1px solid rgba(148, 163, 184, 0.22);
    border-radius: 11px;
    color: #e2e8f0;
    background: #081b22;
    outline: none;
  }

  .text-input:focus {
    border-color: rgba(34, 211, 238, 0.7);
    box-shadow: 0 0 0 3px rgba(34, 211, 238, 0.12);
  }

  .search-button {
    color: #032b33;
    background: linear-gradient(135deg, #22d3ee, #2dd4bf);
    box-shadow: 0 10px 30px rgba(34, 211, 238, 0.18);
  }

  .ghost-button {
    color: #cffafe;
    background: rgba(34, 211, 238, 0.1);
    border: 1px solid rgba(34, 211, 238, 0.3);
  }

  .query-meta {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 0.6rem;
    margin-top: 0.8rem;
    color: #64748b;
    font-size: 0.75rem;
  }

  .demo-toggle {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    color: #94a3b8;
    cursor: pointer;
  }

  .demo-toggle input {
    accent-color: #22d3ee;
  }

  .recording-bar {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin-top: 0.8rem;
    padding: 0.7rem 0.85rem;
    border-radius: 10px;
    color: #fecaca;
    background: rgba(127, 29, 29, 0.22);
    font-size: 0.82rem;
  }

  .pulse {
    width: 0.55rem;
    height: 0.55rem;
    border-radius: 999px;
    background: #f87171;
    animation: mic-pulse 1.2s infinite;
  }

  .error-box {
    margin-top: 0.8rem;
    padding: 0.75rem 0.9rem;
    border: 1px solid rgba(248, 113, 113, 0.35);
    border-radius: 10px;
    color: #fecaca;
    background: rgba(127, 29, 29, 0.2);
    font-size: 0.84rem;
    line-height: 1.5;
  }

  .loading-box {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    margin: 1rem 0;
    padding: 1rem;
    border: 1px solid rgba(34, 211, 238, 0.2);
    border-radius: 14px;
    color: #a5f3fc;
    background: rgba(8, 47, 58, 0.5);
    font-size: 0.88rem;
  }

  .spinner {
    width: 1rem;
    height: 1rem;
    border: 2px solid rgba(165, 243, 252, 0.25);
    border-top-color: #67e8f9;
    border-radius: 999px;
    animation: spin 0.8s linear infinite;
  }

  .stat-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.7rem;
    margin-bottom: 0.9rem;
  }

  .stat {
    display: grid;
    gap: 0.2rem;
    padding: 0.9rem 1rem;
  }

  .stat-label {
    color: #64748b;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.09em;
  }

  .stat strong {
    color: #e2e8f0;
    font-size: 1.25rem;
  }

  .stat .stat-place {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 1rem;
  }

  .stat-note {
    color: #7f9aa6;
    font-size: 0.72rem;
  }

  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 1.75fr) minmax(320px, 0.9fr);
    gap: 0.9rem;
    align-items: stretch;
  }

  .map-panel {
    min-height: 560px;
  }

  .side-panel {
    display: grid;
    gap: 0.9rem;
    align-content: start;
    max-height: 560px;
    overflow: auto;
    padding-right: 0.15rem;
  }

  .panel {
    padding: 0.95rem;
  }

  .panel-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.7rem;
    margin-bottom: 0.55rem;
  }

  .panel h2 {
    font-size: 0.9rem;
    letter-spacing: -0.01em;
  }

  .badge {
    padding: 0.2rem 0.5rem;
    border-radius: 999px;
    color: #a5f3fc;
    background: rgba(34, 211, 238, 0.12);
    font-size: 0.7rem;
    font-weight: 650;
  }

  .badge.muted {
    color: #94a3b8;
    background: rgba(148, 163, 184, 0.1);
  }

  .place-line {
    margin-bottom: 0.6rem;
    color: #7f9aa6;
    font-size: 0.72rem;
    line-height: 1.45;
  }

  .result-list {
    display: grid;
    gap: 0.35rem;
    max-height: 245px;
    margin: 0;
    padding: 0;
    overflow: auto;
    list-style: none;
  }

  .result-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    width: 100%;
    padding: 0.6rem 0.65rem;
    border: 1px solid transparent;
    border-radius: 9px;
    color: #cbd5e1;
    background: rgba(148, 163, 184, 0.06);
    text-align: left;
  }

  .result-item:hover {
    border-color: rgba(34, 211, 238, 0.3);
    background: rgba(34, 211, 238, 0.08);
  }

  .result-item.active {
    border-color: rgba(251, 146, 60, 0.55);
    background: rgba(251, 146, 60, 0.12);
  }

  .result-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.8rem;
  }

  .result-area {
    color: #67e8f9;
    font-size: 0.7rem;
    white-space: nowrap;
  }

  .detail-list {
    display: grid;
    gap: 0.55rem;
    margin: 0.6rem 0;
  }

  .detail-list div {
    display: grid;
    grid-template-columns: 7.5rem minmax(0, 1fr);
    gap: 0.5rem;
    align-items: baseline;
  }

  .detail-list dt {
    color: #64748b;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .detail-list dd {
    margin: 0;
    color: #cbd5e1;
    font-size: 0.78rem;
    overflow-wrap: anywhere;
  }

  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.7rem !important;
    color: #94a3b8 !important;
  }

  .mask-note,
  .empty-note {
    color: #7f9aa6;
    font-size: 0.74rem;
    line-height: 1.55;
  }

  .confirmation-panel p {
    color: #cbd5e1;
    font-size: 0.8rem;
    line-height: 1.55;
  }

  .play-button {
    margin-top: 0.65rem;
    color: #032b33;
    background: #a5f3fc;
  }

  .trace-panel {
    margin-top: 0.9rem;
    padding: 1rem;
  }

  .trace-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 0.65rem;
  }

  .trace-step {
    display: flex;
    gap: 0.55rem;
    padding: 0.65rem;
    border: 1px solid rgba(52, 211, 153, 0.18);
    border-radius: 10px;
    background: rgba(6, 78, 59, 0.12);
  }

  .trace-step.warning {
    border-color: rgba(251, 191, 36, 0.3);
    background: rgba(120, 53, 15, 0.14);
  }

  .trace-dot {
    width: 0.45rem;
    height: 0.45rem;
    margin-top: 0.3rem;
    border-radius: 999px;
    background: #34d399;
    flex: none;
  }

  .trace-step.warning .trace-dot {
    background: #fbbf24;
  }

  .trace-step strong {
    color: #cbd5e1;
    font-size: 0.72rem;
    text-transform: capitalize;
  }

  .trace-step p {
    margin-top: 0.15rem;
    color: #7f9aa6;
    font-size: 0.7rem;
    line-height: 1.45;
  }

  .transcript-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 0.8rem;
    margin-top: 0.9rem;
  }

  .eyebrow {
    color: #64748b;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .transcript {
    margin-top: 0.25rem;
    color: #e2e8f0;
    font-size: 0.9rem;
    line-height: 1.5;
  }

  .intent-details {
    margin-top: 0.8rem;
    color: #94a3b8;
    font-size: 0.78rem;
  }

  .intent-details summary {
    cursor: pointer;
  }

  .intent-details pre {
    margin: 0.5rem 0 0;
    padding: 0.75rem;
    border-radius: 10px;
    background: #06141a;
    color: #a5f3fc;
    overflow: auto;
  }

  .warning-list {
    display: grid;
    gap: 0.35rem;
    margin-top: 0.75rem;
  }

  .warning-list p {
    color: #fcd34d;
    font-size: 0.75rem;
  }

  .empty-state {
    display: grid;
    place-items: center;
    gap: 0.75rem;
    padding: 4.5rem 1.5rem;
    text-align: center;
  }

  .empty-mark {
    color: #22d3ee;
    font-size: 3.5rem;
    line-height: 1;
    text-shadow: 0 0 40px rgba(34, 211, 238, 0.4);
  }

  .empty-state h2 {
    font-size: 1.35rem;
  }

  .empty-state p {
    max-width: 44rem;
    color: #94a3b8;
    font-size: 0.9rem;
    line-height: 1.65;
  }

  .empty-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 0.6rem;
    margin-top: 0.5rem;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  @keyframes mic-pulse {
    0%,
    100% {
      box-shadow: 0 0 0 0 rgba(248, 113, 113, 0.45);
    }
    50% {
      box-shadow: 0 0 0 8px rgba(248, 113, 113, 0);
    }
  }

  @media (max-width: 1100px) {
    .workspace {
      grid-template-columns: 1fr;
    }

    .side-panel {
      max-height: none;
    }

    .map-panel {
      min-height: 480px;
    }
  }

  @media (max-width: 820px) {
    .page {
      width: min(100% - 1.2rem, 1500px);
    }

    .topbar {
      align-items: flex-start;
      flex-direction: column;
    }

    .query-row {
      grid-template-columns: 1fr 1fr;
    }

    .text-input,
    .search-button {
      grid-column: 1 / -1;
    }

    .stat-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 520px) {
    .query-row,
    .stat-grid {
      grid-template-columns: 1fr;
    }

    .health-detail {
      display: none;
    }
  }
</style>
