<script lang="ts">
  import { onMount } from 'svelte';
  import MapView from '$lib/components/MapView.svelte';
  import HistoryPanel from '$lib/components/HistoryPanel.svelte';
  import LivePanel from '$lib/components/LivePanel.svelte';
  import ForensicsPanel from '$lib/components/ForensicsPanel.svelte';
  import VisionPanel from '$lib/components/VisionPanel.svelte';
  import {
    forensicsSocketUrl,
    getForensics,
    getHealth,
    getHistory,
    getLanguages,
    getLive,
    getVision,
    historySocketUrl,
    liveSocketUrl,
    queryText,
    queryVoice,
    visionSocketUrl
  } from '$lib/api';
  import { WavRecorder } from '$lib/recorder';
  import type {
    DiscoveryResponse,
    ForensicReport,
    ForensicStreamMessage,
    ForensicView,
    HealthResponse,
    HistoryStreamMessage,
    HistoryView,
    LanguageOption,
    LiveStreamMessage,
    LiveView,
    TrendAnalysis,
    VisionInspection,
    VisionStreamMessage,
    VisionView,
    WaterBodyFeature
  } from '$lib/types';

  let language = 'kn';
  let text = 'Map the lakes near Mysore Road, Bangalore';
  let demo = false;
  let loading = false;
  let error = '';
  let result: DiscoveryResponse | null = null;
  let selectedId: string | null = null;
  let workspaceEl: HTMLElement;
  let mapView: MapView;
  let mapPct = 30;
  let resizing = false;
  let resizeFrame = 0;
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

  let live: LiveView | null = null;
  let liveLoading = false;
  let liveError = '';
  let liveSocket: WebSocket | null = null;
  let liveAudio: HTMLAudioElement | null = null;

  let forensics: ForensicView | null = null;
  let forensicsLoading = false;
  let forensicsError = '';
  let forensicsSocket: WebSocket | null = null;
  let forensicsAudio: HTMLAudioElement | null = null;

  let vision: VisionView | null = null;
  let visionLoading = false;
  let visionError = '';
  let visionSocket: WebSocket | null = null;
  let visionAudio: HTMLAudioElement | null = null;

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
  $: pulseIds =
    live?.flag?.flag && live.water_body_id === selectedId ? [live.water_body_id] : [];

  onMount(() => {
    void initialize();
    return () => {
      clearTimers();
      closeHistorySocket();
      closeLiveSocket();
      closeForensicsSocket();
      closeVisionSocket();
      recorder?.cancel();
      confirmationAudio?.pause();
      briefingAudio?.pause();
      liveAudio?.pause();
      forensicsAudio?.pause();
      visionAudio?.pause();
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
        if (!live || live.water_body_id !== id) {
          loadLive(id);
        }
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
    resetLive();
    resetForensics();
    resetVision();
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

  function runFullAnalysis(): void {
    if (!selectedId) {
      return;
    }
    const id = selectedId;
    loadHistory(id);
    loadLive(id);
    loadForensics(id);
    loadVision(id);
  }

  function handleLanguageChange(event: Event): void {
    language = (event.currentTarget as HTMLSelectElement).value;
    if (selectedId) {
      loadHistory(selectedId);
    }
  }

  function closeLiveSocket(): void {
    if (!liveSocket) {
      return;
    }
    liveSocket.onclose = null;
    liveSocket.onerror = null;
    liveSocket.onmessage = null;
    liveSocket.close();
    liveSocket = null;
  }

  function resetLive(): void {
    closeLiveSocket();
    live = null;
    liveLoading = false;
    liveError = '';
  }

  function emptyLive(id: string, name: string): LiveView {
    return {
      water_body_id: id,
      name,
      source: '',
      index_version: '',
      observed_at: '',
      scene: null,
      flag: null,
      alert: null,
      warnings: [],
      cached: false
    };
  }

  function playLiveAlert(alert = live?.alert ?? null): void {
    if (!alert?.audio_base64) {
      return;
    }
    liveAudio?.pause();
    const mime = alert.audio_mime || 'audio/wav';
    liveAudio = new Audio(`data:${mime};base64,${alert.audio_base64}`);
    liveAudio.play().catch(() => {
      // Autoplay may be blocked; the alert panel keeps a replay button.
    });
  }

  function applyLiveMessage(message: LiveStreamMessage, id: string): void {
    if (!live || live.water_body_id !== id) {
      return;
    }
    switch (message.type) {
      case 'live':
        live = {
          ...live,
          source: message.source,
          index_version: message.index_version,
          observed_at: message.observed_at,
          scene: message.scene,
          flag: message.flag,
          warnings: message.warnings
        };
        break;
      case 'flag':
        live = { ...live, flag: message.flag };
        break;
      case 'alert':
        live = { ...live, alert: message.alert };
        if (message.alert.severity === 'critical' && message.alert.audio_base64) {
          playLiveAlert(message.alert);
        }
        if (live.flag?.flag && (!forensics || forensics.water_body_id !== id)) {
          loadForensics(id);
        }
        break;
      case 'error':
        liveError = message.detail;
        break;
    }
  }

  async function fallbackLive(id: string): Promise<void> {
    try {
      const profile = await getLive(id, language, demo, true);
      if (live?.water_body_id !== id) {
        return;
      }
      live = {
        water_body_id: profile.water_body_id,
        name: profile.name,
        source: profile.source,
        index_version: profile.index_version,
        observed_at: profile.observed_at,
        scene: profile.scene,
        flag: profile.flag,
        alert: profile.alert,
        warnings: profile.warnings,
        cached: profile.cached
      };
    } catch (err) {
      if (live?.water_body_id === id) {
        liveError = err instanceof Error ? err.message : 'Live NDWI request failed.';
      }
    }
  }

  function loadLive(id: string): void {
    const feature = features.find(
      (item: WaterBodyFeature) => item.properties.water_body_id === id
    );
    closeLiveSocket();
    live = emptyLive(id, feature?.properties.name ?? id);
    liveError = '';
    liveLoading = true;

    let complete = false;
    const socket = new WebSocket(liveSocketUrl(id, language, demo, true));
    liveSocket = socket;

    socket.onmessage = (event: MessageEvent) => {
      let message: LiveStreamMessage;
      try {
        message = JSON.parse(String(event.data)) as LiveStreamMessage;
      } catch {
        return;
      }
      if (live?.water_body_id !== id) {
        return;
      }
      if (message.type === 'alert') {
        complete = true;
        liveLoading = false;
      }
      applyLiveMessage(message, id);
    };

    socket.onclose = () => {
      if (liveSocket !== socket) {
        return;
      }
      liveSocket = null;
      if (complete) {
        liveLoading = false;
        return;
      }
      void fallbackLive(id).finally(() => {
        if (live?.water_body_id === id) {
          liveLoading = false;
        }
      });
    };
  }

  function closeForensicsSocket(): void {
    if (!forensicsSocket) {
      return;
    }
    forensicsSocket.onclose = null;
    forensicsSocket.onerror = null;
    forensicsSocket.onmessage = null;
    forensicsSocket.close();
    forensicsSocket = null;
  }

  function resetForensics(): void {
    closeForensicsSocket();
    forensics = null;
    forensicsLoading = false;
    forensicsError = '';
  }

  function emptyForensics(id: string, name: string): ForensicView {
    return {
      water_body_id: id,
      name,
      source: '',
      index_version: '',
      recession: null,
      geometry: null,
      ndbi: null,
      diagnosis: null,
      report: null,
      warnings: [],
      cached: false
    };
  }

  function playForensicsReport(report: ForensicReport | null = forensics?.report ?? null): void {
    if (!report?.audio_base64) {
      return;
    }
    forensicsAudio?.pause();
    const mime = report.audio_mime || 'audio/wav';
    forensicsAudio = new Audio(`data:${mime};base64,${report.audio_base64}`);
    forensicsAudio.play().catch(() => {
      // Autoplay may be blocked; the panel keeps a replay button.
    });
  }

  function applyForensicsMessage(message: ForensicStreamMessage, id: string): void {
    if (!forensics || forensics.water_body_id !== id) {
      return;
    }
    switch (message.type) {
      case 'forensic':
        forensics = {
          ...forensics,
          source: message.source,
          index_version: message.index_version,
          recession: message.recession,
          geometry: message.geometry,
          ndbi: message.ndbi,
          diagnosis: message.diagnosis,
          warnings: message.warnings
        };
        break;
      case 'diagnosis':
        forensics = { ...forensics, diagnosis: message.diagnosis };
        break;
      case 'briefing':
        forensics = { ...forensics, report: message.report };
        if (message.report.audio_base64) {
          playForensicsReport(message.report);
        }
        if (!vision || vision.water_body_id !== id) {
          loadVision(id);
        }
        break;
      case 'error':
        forensicsError = message.detail;
        break;
    }
  }

  async function fallbackForensics(id: string): Promise<void> {
    try {
      const profile = await getForensics(id, language, demo, true);
      if (forensics?.water_body_id !== id) {
        return;
      }
      forensics = {
        water_body_id: profile.water_body_id,
        name: profile.name,
        source: profile.source,
        index_version: profile.index_version,
        recession: profile.recession,
        geometry: profile.geometry,
        ndbi: profile.ndbi,
        diagnosis: profile.diagnosis,
        report: profile.report,
        warnings: profile.warnings,
        cached: profile.cached
      };
    } catch (err) {
      if (forensics?.water_body_id === id) {
        forensicsError =
          err instanceof Error ? err.message : 'Forensic diagnosis request failed.';
      }
    }
  }

  function loadForensics(id: string): void {
    const feature = features.find(
      (item: WaterBodyFeature) => item.properties.water_body_id === id
    );
    closeForensicsSocket();
    forensics = emptyForensics(id, feature?.properties.name ?? id);
    forensicsError = '';
    forensicsLoading = true;

    let complete = false;
    const socket = new WebSocket(forensicsSocketUrl(id, language, demo, true));
    forensicsSocket = socket;

    socket.onmessage = (event: MessageEvent) => {
      let message: ForensicStreamMessage;
      try {
        message = JSON.parse(String(event.data)) as ForensicStreamMessage;
      } catch {
        return;
      }
      if (forensics?.water_body_id !== id) {
        return;
      }
      if (message.type === 'briefing') {
        complete = true;
        forensicsLoading = false;
      }
      applyForensicsMessage(message, id);
    };

    socket.onclose = () => {
      if (forensicsSocket !== socket) {
        return;
      }
      forensicsSocket = null;
      if (complete) {
        forensicsLoading = false;
        return;
      }
      void fallbackForensics(id).finally(() => {
        if (forensics?.water_body_id === id) {
          forensicsLoading = false;
        }
      });
    };
  }

  function closeVisionSocket(): void {
    if (!visionSocket) {
      return;
    }
    visionSocket.onclose = null;
    visionSocket.onerror = null;
    visionSocket.onmessage = null;
    visionSocket.close();
    visionSocket = null;
  }

  function resetVision(): void {
    closeVisionSocket();
    vision = null;
    visionLoading = false;
    visionError = '';
  }

  function emptyVision(id: string, name: string): VisionView {
    return {
      water_body_id: id,
      name,
      source: '',
      index_version: '',
      patches: [],
      inspection: null,
      diagnosis: null,
      warnings: [],
      cached: false
    };
  }

  function playVisionBriefing(
    inspection: VisionInspection | null = vision?.inspection ?? null
  ): void {
    if (!inspection?.audio_base64) {
      return;
    }
    visionAudio?.pause();
    const mime = inspection.audio_mime || 'audio/wav';
    visionAudio = new Audio(`data:${mime};base64,${inspection.audio_base64}`);
    visionAudio.play().catch(() => {
      // Autoplay may be blocked; the panel keeps a replay button.
    });
  }

  function applyVisionMessage(message: VisionStreamMessage, id: string): void {
    if (!vision || vision.water_body_id !== id) {
      return;
    }
    switch (message.type) {
      case 'vision':
        vision = {
          ...vision,
          source: message.source,
          index_version: message.index_version,
          patches: message.patches,
          inspection: message.inspection,
          diagnosis: message.diagnosis,
          warnings: message.warnings
        };
        break;
      case 'briefing':
        vision = { ...vision, inspection: message.inspection };
        if (message.inspection.audio_base64) {
          playVisionBriefing(message.inspection);
        }
        break;
      case 'error':
        visionError = message.detail;
        break;
    }
  }

  async function fallbackVision(id: string): Promise<void> {
    try {
      const profile = await getVision(id, language, demo, true);
      if (vision?.water_body_id !== id) {
        return;
      }
      vision = {
        water_body_id: profile.water_body_id,
        name: profile.name,
        source: profile.source,
        index_version: profile.index_version,
        patches: profile.patches,
        inspection: profile.inspection,
        diagnosis: profile.diagnosis,
        warnings: profile.warnings,
        cached: profile.cached
      };
    } catch (err) {
      if (vision?.water_body_id === id) {
        visionError = err instanceof Error ? err.message : 'Vision request failed.';
      }
    }
  }

  function loadVision(id: string): void {
    const feature = features.find(
      (item: WaterBodyFeature) => item.properties.water_body_id === id
    );
    closeVisionSocket();
    vision = emptyVision(id, feature?.properties.name ?? id);
    visionError = '';
    visionLoading = true;

    let complete = false;
    const socket = new WebSocket(visionSocketUrl(id, language, demo, true));
    visionSocket = socket;

    socket.onmessage = (event: MessageEvent) => {
      let message: VisionStreamMessage;
      try {
        message = JSON.parse(String(event.data)) as VisionStreamMessage;
      } catch {
        return;
      }
      if (vision?.water_body_id !== id) {
        return;
      }
      if (message.type === 'briefing') {
        complete = true;
        visionLoading = false;
      }
      applyVisionMessage(message, id);
    };

    socket.onclose = () => {
      if (visionSocket !== socket) {
        return;
      }
      visionSocket = null;
      if (complete) {
        visionLoading = false;
        return;
      }
      void fallbackVision(id).finally(() => {
        if (vision?.water_body_id === id) {
          visionLoading = false;
        }
      });
    };
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
    resetLive();
    resetForensics();
    resetVision();
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

  function causeLabel(classification: string | undefined | null): string {
    switch (classification) {
      case 'siltation_debris':
        return 'Siltation';
      case 'encroachment':
        return 'Encroachment';
      case 'encroachment_suspected':
        return 'Suspected encroachment';
      case 'seasonal_drought':
        return 'Seasonal drought';
      case 'inconclusive':
        return 'Inconclusive';
      default:
        return classification ?? '—';
    }
  }

  const MIN_MAP_PCT = 20;
  const MAX_MAP_PCT = 55;

  function clampMapPct(value: number): number {
    return Math.min(MAX_MAP_PCT, Math.max(MIN_MAP_PCT, Math.round(value * 10) / 10));
  }

  /** Mapbox needs a resize after the column width changes; batch it per frame. */
  function scheduleMapResize(): void {
    if (resizeFrame) {
      return;
    }
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = 0;
      mapView?.resizeMap();
    });
  }

  function mapPctFromPointer(clientX: number): void {
    if (!workspaceEl) {
      return;
    }
    const rect = workspaceEl.getBoundingClientRect();
    if (rect.width === 0) {
      return;
    }
    mapPct = clampMapPct(((rect.right - clientX) / rect.width) * 100);
    scheduleMapResize();
  }

  function startResize(event: PointerEvent): void {
    if (event.button !== 0) {
      return;
    }
    resizing = true;
    try {
      (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture is a nicety; dragging still works without it.
    }
    event.preventDefault();
  }

  function resizeMove(event: PointerEvent): void {
    if (!resizing) {
      return;
    }
    mapPctFromPointer(event.clientX);
  }

  function stopResize(event: PointerEvent): void {
    if (!resizing) {
      return;
    }
    resizing = false;
    const target = event.currentTarget as HTMLElement;
    if (target.hasPointerCapture(event.pointerId)) {
      target.releasePointerCapture(event.pointerId);
    }
    scheduleMapResize();
  }

  function resizeKey(event: KeyboardEvent): void {
    const step = event.shiftKey ? 5 : 1;
    switch (event.key) {
      case 'ArrowLeft':
        mapPct = clampMapPct(mapPct + step);
        break;
      case 'ArrowRight':
        mapPct = clampMapPct(mapPct - step);
        break;
      case 'Home':
        mapPct = MIN_MAP_PCT;
        break;
      case 'End':
        mapPct = MAX_MAP_PCT;
        break;
      default:
        return;
    }
    event.preventDefault();
    scheduleMapResize();
  }
</script>

<svelte:head>
  <title>Water Body Watch · Vision-Validated Lake Monitoring</title>
  <meta
    name="description"
    content="Speak a location, discover urban lakes, and get a vision-validated, multilingual forensic briefing on why each lake is shrinking."
  />
</svelte:head>

<div class="page">
  <header class="topbar">
    <div class="brand">
      <span class="brand-mark">◉</span>
      <div>
        <h1>Water Body Watch</h1>
        <p>Urban lake monitoring and forensic intelligence</p>
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
          · Voice {healthInfo.bhashini_configured ? 'ready' : 'off'} · AI
          {healthInfo.nvidia_configured ? 'ready' : 'fallback'} · History
          {healthInfo.gee_configured ? 'live' : 'demo'} · Satellite
          {healthInfo.sentinel_hub_configured ? 'live' : 'demo'}
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
      Resolving the location and loading lake boundaries…
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
        <span class="stat-label">Query engine</span>
        <strong>{result.steps.find((step) => step.name === 'intent')?.status === 'warning' ? 'Local parser' : 'AI model'}</strong>
        <span class="stat-note">{result.intent.target_entity.replace('_', ' ')}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Audio briefing</span>
        <strong>{result.confirmation_audio_base64 ? 'Voice ready' : 'Text only'}</strong>
        <span class="stat-note">{languageLabel(result.language)}</span>
      </div>
      <div class="stat">
        <span class="stat-label">Live water check</span>
        <strong class:alarm={live?.flag?.flag}>
          {live?.flag ? (live.flag.flag ? 'Shrinkage flagged' : 'Within baseline') : '—'}
        </strong>
        <span class="stat-note">
          {live?.source === 'sentinel-hub'
            ? 'Sentinel-2 L2A'
            : live?.source
              ? 'Synthetic scene'
              : 'select a lake'}
        </span>
      </div>
      <div class="stat">
        <span class="stat-label">Likely cause</span>
        <strong
          class:alarm={forensics?.diagnosis?.classification === 'encroachment' ||
            forensics?.diagnosis?.classification === 'siltation_debris'}
        >
          {causeLabel(forensics?.diagnosis?.classification)}
        </strong>
        <span class="stat-note">{forensics ? 'forensic diagnosis' : 'flagged lakes only'}</span>
      </div>
    </section>

    {#if live?.flag?.flag && live.alert}
      <div class="critical-strip" class:critical={live.alert.severity === 'critical'}>
        <span class="strip-pill">{live.flag.classification.replaceAll('_', ' ')}</span>
        <strong>{live.alert.headline}</strong>
        <span class="strip-action">{live.alert.recommended_action}</span>
      </div>
    {/if}

    <section
      class="workspace"
      class:resizing
      bind:this={workspaceEl}
      style={`--map-width: ${mapPct}%`}
    >
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
            </dl>
            <div class="detail-actions">
              <button class="search-button compact" on:click={runFullAnalysis}>
                Run full analysis
              </button>
              <span class="detail-hint">Baseline → live check → cause → visual review</span>
            </div>
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
            onRun={() => selectedId && loadHistory(selectedId)}
          />
        </div>

        <div class="panel live-panel">
          <LivePanel
            live={live}
            loading={liveLoading}
            error={liveError}
            {languageLabel}
            onPlay={() => playLiveAlert()}
            onRetry={() => selectedId && loadLive(selectedId)}
            onCheckNow={() => selectedId && loadLive(selectedId)}
          />
        </div>

        <div class="panel forensics-panel">
          <ForensicsPanel
            forensics={forensics}
            loading={forensicsLoading}
            error={forensicsError}
            {languageLabel}
            onPlay={() => playForensicsReport()}
            onRetry={() => selectedId && loadForensics(selectedId)}
            onRun={() => selectedId && loadForensics(selectedId)}
          />
        </div>

        <div class="panel vision-panel">
          <VisionPanel
            vision={vision}
            loading={visionLoading}
            error={visionError}
            {languageLabel}
            onPlay={() => playVisionBriefing()}
            onRetry={() => selectedId && loadVision(selectedId)}
            onRun={() => selectedId && loadVision(selectedId)}
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
              The voice service is configured but did not return audio for this request.
            </p>
          {:else}
            <p class="empty-note">Audio is unavailable. Add a voice provider to hear this message.</p>
          {/if}
        </div>
      </aside>

      <!-- svelte-ignore a11y_no_noninteractive_tabindex a11y_no_noninteractive_element_interactions -->
      <div
        class="splitter"
        class:active={resizing}
        role="separator"
        aria-label="Resize map panel"
        aria-orientation="vertical"
        aria-valuemin={MIN_MAP_PCT}
        aria-valuemax={MAX_MAP_PCT}
        aria-valuenow={Math.round(mapPct)}
        tabindex="0"
        on:pointerdown={startResize}
        on:pointermove={resizeMove}
        on:pointerup={stopResize}
        on:pointercancel={stopResize}
        on:keydown={resizeKey}
      >
        <span class="splitter-grip"></span>
      </div>

      <div class="map-panel">
        <MapView
          bind:this={mapView}
          data={result.water_bodies}
          {selectedId}
          {pulseIds}
          onselect={selectLake}
        />
      </div>
    </section>
  {:else if !loading}
    <section class="empty-state">
      <div class="empty-mark">◎</div>
      <h2>Start with a place, not a coordinate</h2>
      <p>
        Speak in Kannada, Hindi, Tamil, or another supported language. The request is translated,
        resolved to a place, and matched to mapped lake and pond boundaries.
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
    width: min(1600px, calc(100% - 3rem));
    margin: 0 auto;
    padding: 1.25rem 0 3rem;
  }

  .topbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 0 0 1rem;
    border-bottom: 1px solid #2e2e2e;
    margin-bottom: 1rem;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 0.85rem;
  }

  .brand-mark {
    display: grid;
    place-items: center;
    width: 2.5rem;
    height: 2.5rem;
    border: 1px solid #2e2e2e;
    border-radius: 0;
    color: #fafafa;
    background: #161616;
    font-size: 1.05rem;
  }

  h1,
  h2,
  p {
    margin: 0;
  }

  h1 {
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 1.1rem;
    font-weight: 400;
    letter-spacing: 0.01em;
    color: #fafafa;
  }

  .brand p {
    color: #8f8f8f;
    font-size: 0.76rem;
    margin-top: 0.2rem;
  }

  .health {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.45rem;
    padding: 0.4rem 0.7rem;
    border: 1px solid #2e2e2e;
    border-radius: 0;
    color: #8f8f8f;
    background: #161616;
    font-size: 0.72rem;
  }

  .health .dot {
    width: 0.45rem;
    height: 0.45rem;
    border-radius: 999px;
    background: #ff453a;
  }

  .health.online .dot {
    background: #30d158;
  }

  .health-detail {
    color: #6a6a6a;
  }

  .query-card,
  .panel,
  .stat,
  .empty-state {
    border: 1px solid #2e2e2e;
    border-radius: 0;
    background: #161616;
  }

  .query-card {
    padding: 1rem 1.1rem;
    margin-bottom: 1rem;
  }

  .query-row {
    display: grid;
    grid-template-columns: auto auto minmax(180px, 1fr) auto;
    gap: 0.6rem;
    align-items: end;
  }

  .language-field {
    display: grid;
    gap: 0.35rem;
    color: #8f8f8f;
    font-size: 0.64rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }

  .language-field select {
    min-width: 9rem;
    padding: 0.66rem 0.75rem;
    border: 1px solid #2e2e2e;
    border-radius: 0;
    color: #dbdbdb;
    background: #1a1a1a;
  }

  .mic-button,
  .search-button,
  .play-button,
  .ghost-button {
    border: 1px solid transparent;
    border-radius: 0;
    padding: 0.68rem 0.95rem;
    font-size: 0.78rem;
    font-weight: 500;
    transition:
      background 120ms ease,
      border-color 120ms ease,
      color 120ms ease;
  }

  .mic-button {
    color: #fafafa;
    background: #242424;
    border-color: #2e2e2e;
  }

  .mic-button:hover:not(:disabled) {
    background: #2e2e2e;
  }

  .mic-button.recording {
    color: #fafafa;
    background: #ff453a;
    border-color: #ff453a;
  }

  .mic-icon {
    margin-right: 0.35rem;
  }

  .text-input {
    width: 100%;
    padding: 0.66rem 0.8rem;
    border: 1px solid #2e2e2e;
    border-radius: 0;
    color: #dbdbdb;
    background: #1a1a1a;
    outline: none;
  }

  .text-input::placeholder {
    color: #6a6a6a;
  }

  .text-input:focus {
    border-color: #6b8fff;
  }

  .search-button {
    color: #0c0c0c;
    background: #fafafa;
    border-color: #fafafa;
  }

  .search-button:hover:not(:disabled) {
    background: #dbdbdb;
    border-color: #dbdbdb;
  }

  .ghost-button {
    color: #dbdbdb;
    background: transparent;
    border-color: #2e2e2e;
  }

  .ghost-button:hover:not(:disabled) {
    background: #242424;
  }

  .query-meta {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: 0.6rem;
    margin-top: 0.8rem;
    color: #8f8f8f;
    font-size: 0.72rem;
  }

  .demo-toggle {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    color: #aeaeae;
    cursor: pointer;
  }

  .demo-toggle input {
    accent-color: #6b8fff;
  }

  .recording-bar {
    display: flex;
    align-items: center;
    gap: 0.55rem;
    margin-top: 0.8rem;
    padding: 0.65rem 0.8rem;
    border: 1px solid rgba(255, 69, 58, 0.35);
    border-radius: 0;
    color: #ffb4ab;
    background: rgba(255, 69, 58, 0.08);
    font-size: 0.8rem;
  }

  .pulse {
    width: 0.5rem;
    height: 0.5rem;
    border-radius: 999px;
    background: #ff453a;
    animation: mic-pulse 1.2s infinite;
  }

  .error-box {
    margin-top: 0.8rem;
    padding: 0.7rem 0.85rem;
    border: 1px solid rgba(255, 69, 58, 0.4);
    border-radius: 0;
    color: #ffb4ab;
    background: rgba(255, 69, 58, 0.08);
    font-size: 0.82rem;
    line-height: 1.5;
  }

  .loading-box {
    display: flex;
    align-items: center;
    gap: 0.7rem;
    margin: 1rem 0;
    padding: 0.9rem 1rem;
    border: 1px solid #2e2e2e;
    border-radius: 0;
    color: #dbdbdb;
    background: #1a1a1a;
    font-size: 0.84rem;
  }

  .spinner {
    width: 0.95rem;
    height: 0.95rem;
    border: 2px solid #2e2e2e;
    border-top-color: #6b8fff;
    border-radius: 999px;
    animation: spin 0.8s linear infinite;
  }

  .stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 1px;
    margin-bottom: 1rem;
    border: 1px solid #2e2e2e;
    background: #2e2e2e;
  }

  .stat {
    display: grid;
    gap: 0.25rem;
    padding: 0.85rem 1rem;
    border: 0;
  }

  .stat-label {
    color: #8f8f8f;
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }

  .stat strong {
    color: #fafafa;
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 1.1rem;
    font-weight: 400;
  }

  .stat .stat-place {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.95rem;
  }

  .stat-note {
    color: #8f8f8f;
    font-size: 0.7rem;
  }

  .stat strong.alarm {
    color: #ffb4ab;
  }

  .critical-strip {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.6rem;
    margin-bottom: 1rem;
    padding: 0.7rem 0.9rem;
    border: 1px solid rgba(255, 159, 10, 0.4);
    border-radius: 0;
    color: #ffd60a;
    background: rgba(255, 159, 10, 0.07);
    font-size: 0.76rem;
  }

  .critical-strip.critical {
    border-color: rgba(255, 69, 58, 0.45);
    color: #ffb4ab;
    background: rgba(255, 69, 58, 0.08);
  }

  .strip-pill {
    padding: 0.16rem 0.5rem;
    border: 1px solid rgba(255, 69, 58, 0.45);
    border-radius: 999px;
    background: transparent;
    font-size: 0.6rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .critical-strip strong {
    font-size: 0.8rem;
    color: #fafafa;
  }

  .strip-action {
    color: #ffb4ab;
    font-size: 0.72rem;
  }

  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 14px var(--map-width, 30%);
    align-items: start;
  }

  .splitter {
    display: flex;
    align-items: center;
    justify-content: center;
    align-self: stretch;
    height: 680px;
    cursor: col-resize;
    touch-action: none;
    background: transparent;
    outline: none;
  }

  .splitter-grip {
    width: 1px;
    height: 100%;
    background: #2e2e2e;
    transition: background 120ms ease;
  }

  .splitter:hover .splitter-grip,
  .splitter:focus-visible .splitter-grip,
  .splitter.active .splitter-grip {
    background: #6b8fff;
  }

  .splitter:focus-visible {
    box-shadow: inset 0 0 0 1px rgba(107, 143, 255, 0.6);
  }

  .workspace.resizing {
    cursor: col-resize;
    user-select: none;
  }

  .map-panel {
    height: 680px;
    border: 1px solid #2e2e2e;
    background: #161616;
    overflow: hidden;
  }

  .workspace > * {
    min-width: 0;
  }

  .side-panel {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0.75rem;
    align-content: start;
    max-height: 680px;
    overflow: auto;
    padding-right: 0.6rem;
    scrollbar-width: thin;
    scrollbar-color: #4b4b4b transparent;
  }

  .side-panel::-webkit-scrollbar,
  .result-list::-webkit-scrollbar {
    width: 8px;
  }

  .side-panel::-webkit-scrollbar-thumb,
  .result-list::-webkit-scrollbar-thumb {
    background: #4b4b4b;
  }

  .panel {
    min-width: 0;
    padding: 1rem;
  }

  .panel-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.7rem;
    margin-bottom: 0.65rem;
  }

  .panel h2 {
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
    font-size: 0.64rem;
    font-weight: 400;
  }

  .badge.muted {
    color: #8f8f8f;
    background: #1a1a1a;
  }

  .place-line {
    margin-bottom: 0.65rem;
    color: #8f8f8f;
    font-size: 0.72rem;
    line-height: 1.5;
  }

  .result-list {
    display: grid;
    gap: 1px;
    max-height: 260px;
    margin: 0;
    padding: 0;
    overflow: auto;
    list-style: none;
    background: #2e2e2e;
    border: 1px solid #2e2e2e;
    scrollbar-width: thin;
    scrollbar-color: #4b4b4b transparent;
  }

  .result-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    width: 100%;
    padding: 0.6rem 0.7rem;
    border: 1px solid transparent;
    border-radius: 0;
    color: #aeaeae;
    background: #1a1a1a;
    text-align: left;
  }

  .result-item:hover {
    background: #242424;
  }

  .result-item.active {
    border-color: #6b8fff;
    background: #242424;
    color: #fafafa;
  }

  .result-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.78rem;
  }

  .result-area {
    color: #8f8f8f;
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 0.66rem;
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
    color: #6a6a6a;
    font-size: 0.62rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
  }

  .detail-list dd {
    margin: 0;
    color: #aeaeae;
    font-size: 0.76rem;
    overflow-wrap: anywhere;
  }

  .empty-note {
    color: #8f8f8f;
    font-size: 0.72rem;
    line-height: 1.55;
  }

  .detail-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.6rem;
    margin-top: 0.7rem;
  }

  .search-button.compact {
    padding: 0.5rem 0.7rem;
    font-size: 0.68rem;
  }

  .detail-hint {
    color: #6a6a6a;
    font-size: 0.64rem;
  }

  .confirmation-panel p {
    color: #aeaeae;
    font-size: 0.78rem;
    line-height: 1.55;
  }

  .play-button {
    margin-top: 0.65rem;
    color: #0c0c0c;
    background: #fafafa;
    border-color: #fafafa;
  }

  .play-button:hover:not(:disabled) {
    background: #dbdbdb;
    border-color: #dbdbdb;
  }

  .empty-state {
    display: grid;
    place-items: center;
    gap: 0.75rem;
    padding: 4.5rem 1.5rem;
    text-align: center;
  }

  .empty-mark {
    color: #6b8fff;
    font-family:
      'OpenTUI Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas,
      'Liberation Mono', monospace;
    font-size: 2.75rem;
    line-height: 1;
  }

  .empty-state h2 {
    font-size: 1.15rem;
    font-weight: 400;
    color: #fafafa;
  }

  .empty-state p {
    max-width: 44rem;
    color: #8f8f8f;
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
      box-shadow: 0 0 0 0 rgba(255, 69, 58, 0.4);
    }
    50% {
      box-shadow: 0 0 0 6px rgba(255, 69, 58, 0);
    }
  }

  @media (max-width: 1100px) {
    .workspace {
      grid-template-columns: 1fr;
      gap: 1rem;
    }

    .splitter {
      display: none;
    }

    .side-panel {
      max-height: none;
      padding-right: 0;
    }

    .map-panel {
      order: -1;
      height: 520px;
    }
  }

  @media (max-width: 820px) {
    .page {
      width: min(100% - 1.2rem, 1600px);
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
