"""Phase 2 orchestration: mask -> JRC series -> baseline -> flag -> briefing.

The deterministic metrics are always computed before the LLM runs, and both are
returned. The WebSocket stream emits typed ``historical``, ``flag``, and
``briefing`` messages in that order so the UI can render progressively.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.history.analysis import analyze_history, metrics_payload
from app.history.baseline import (
    Baseline,
    FlagResult,
    Observation,
    Trend,
    build_baseline,
    build_trend,
    evaluate_flag,
)
from app.history.cache import HistoryCache
from app.history.jrc import (
    JRC_SOURCE,
    SYNTHETIC_SOURCE,
    HistorySourceError,
    fetch_jrc_series,
    last_complete_month,
    synthetic_series,
)
from app.models import (
    BriefingMessage,
    FlagMessage,
    HistoricalMessage,
    HistoricalProfile,
    HistoryFlag,
    HistoryPoint,
    MonthlyClimatology,
    SeasonalBaseline,
    Step,
    TrendAnalysis,
    TrendMetrics,
)
from app.services.bhashini import BhashiniClient, BhashiniError
from app.services.osm import WaterBodyRecord, load_demo_records
from app.services.store import WaterBodyStore

logger = logging.getLogger(__name__)


class HistoryNotFound(LookupError):
    """The water body is not in the in-process store."""


@dataclass
class HistoryMetrics:
    """Deterministic Phase 2 result, reused by Phase 3."""

    record: WaterBodyRecord
    name: str
    series: list[Observation]
    baseline: Baseline
    trend: Trend
    current: Observation | None
    flag: FlagResult
    source: str
    index_version: str
    steps: list[Step]
    warnings: list[str]


class HistoryService:
    def __init__(
        self,
        settings: Settings,
        store: WaterBodyStore,
        bhashini: BhashiniClient | None = None,
        cache: HistoryCache | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.bhashini = bhashini or BhashiniClient(settings)
        self.cache = cache or HistoryCache(settings.history_cache_ttl_s)

    # -- resolution ---------------------------------------------------------

    def _resolve(self, water_body_id: str, demo: bool) -> WaterBodyRecord:
        record = self.store.get(water_body_id)
        if record is None and demo:
            self.store.put_many(load_demo_records(self.settings))
            record = self.store.get(water_body_id)
        if record is None:
            raise HistoryNotFound(
                "Water body not found in this process. Run a discovery query first."
            )
        return record

    def _cache_key(
        self, water_body_id: str, language: str, demo: bool, speak: bool
    ) -> tuple[object, ...]:
        return (
            water_body_id,
            last_complete_month().isoformat(),
            self.settings.history_index_version,
            language,
            speak,
            demo,
            self.settings.gee_configured,
        )

    # -- data source --------------------------------------------------------

    async def _load_series(
        self,
        record: WaterBodyRecord,
        demo: bool,
        warnings: list[str],
        steps: list[Step],
    ) -> tuple[list[Observation], str, str]:
        synthetic_version = f"{self.settings.history_index_version}-demo"

        def use_synthetic(reason: str, detail: str) -> tuple[list[Observation], str, str]:
            warnings.append(reason)
            steps.append(Step(name="jrc_baseline", status="warning", detail=detail))
            return synthetic_series(record, self.settings), SYNTHETIC_SOURCE, synthetic_version

        if demo:
            return use_synthetic(
                "Demo mode uses a deterministic synthetic history series, not JRC.",
                f"Generated {self.settings.history_years} years of synthetic monthly area.",
            )

        if self.settings.gee_configured:
            try:
                series = await fetch_jrc_series(record, self.settings)
            except HistorySourceError as exc:
                if not self.settings.history_allow_synthetic:
                    raise
                logger.warning("JRC series failed for %s: %s", record.water_body_id, exc)
                return use_synthetic(
                    f"JRC query failed; using the synthetic demo series ({exc}).",
                    "JRC was unavailable, so the offline synthetic series was used.",
                )

            observed = [item for item in series if item.observed]
            if len(observed) < self.settings.history_min_months:
                if not self.settings.history_allow_synthetic:
                    raise HistorySourceError(
                        f"Only {len(observed)} JRC observations were returned; "
                        f"{self.settings.history_min_months} are needed for a baseline."
                    )
                return use_synthetic(
                    (
                        f"JRC returned only {len(observed)} valid months; "
                        "using the synthetic demo series."
                    ),
                    "Too few JRC observations for a stable baseline.",
                )

            steps.append(
                Step(
                    name="jrc_baseline",
                    status="ok",
                    detail=(
                        f"Loaded {len(observed)} monthly JRC Global Surface Water "
                        f"observations at {self.settings.jrc_scale_m} m."
                    ),
                )
            )
            return series, JRC_SOURCE, self.settings.history_index_version

        if not self.settings.history_allow_synthetic:
            raise HistorySourceError(
                "Google Earth Engine is not configured. Set GEE_SERVICE_ACCOUNT_JSON "
                "and GEE_PROJECT, or set HISTORY_ALLOW_SYNTHETIC=true."
            )

        return use_synthetic(
            "Google Earth Engine is not configured; using a deterministic synthetic series. "
            "Set GEE_SERVICE_ACCOUNT_JSON and GEE_PROJECT for real JRC data.",
            f"Generated {self.settings.history_years} years of synthetic monthly area.",
        )

    # -- serialization ------------------------------------------------------

    @staticmethod
    def _baseline_model(baseline: Baseline) -> SeasonalBaseline:
        return SeasonalBaseline(
            window_years=baseline.window_years,
            start_date=baseline.start_date.isoformat(),
            end_date=baseline.end_date.isoformat(),
            annual_median_m2=baseline.annual_median_m2,
            survival_threshold_m2=baseline.survival_threshold_m2,
            amplitude_pct=baseline.amplitude_pct,
            drought_months=list(baseline.drought_months),
            peak_months=list(baseline.peak_months),
            climatology=[
                MonthlyClimatology(
                    month=entry.month,
                    median_area_m2=entry.median_area_m2,
                    min_area_m2=entry.min_area_m2,
                    max_area_m2=entry.max_area_m2,
                    samples=entry.samples,
                )
                for entry in baseline.climatology
            ],
        )

    @staticmethod
    def _trend_model(trend: Trend) -> TrendMetrics:
        return TrendMetrics(
            slope_m2_per_year=trend.slope_m2_per_year,
            slope_pct_per_year=trend.slope_pct_per_year,
            recent_median_m2=trend.recent_median_m2,
            prior_median_m2=trend.prior_median_m2,
            permanent_change_pct=trend.permanent_change_pct,
            months_observed=trend.months_observed,
            months_missing=trend.months_missing,
        )

    def _flag_model(
        self, flag: FlagResult, water_body_id: str, source: str, index_version: str
    ) -> HistoryFlag:
        return HistoryFlag(
            water_body_id=water_body_id,
            area_m2=flag.area_m2,
            baseline_area_m2=flag.baseline_area_m2,
            drop_pct=flag.drop_pct,
            flag=flag.flag,
            classification=flag.classification,
            confidence=flag.confidence,
            evidence=list(flag.evidence),
            evaluated_month=flag.evaluated_month,
            threshold_pct=self.settings.flag_drop_pct,
            index_version=index_version,
            source=source,
        )

    @staticmethod
    def _series_model(series: list[Observation]) -> list[HistoryPoint]:
        return [
            HistoryPoint(
                date=item.date.isoformat(),
                area_m2=item.area_m2,
                observed=item.observed,
            )
            for item in series
        ]

    # -- pipeline -----------------------------------------------------------

    async def metrics(self, water_body_id: str, *, demo: bool = False) -> HistoryMetrics:
        """Deterministic Phase 2 metrics without LLM narration or TTS.

        Phase 3 reuses this for the same-calendar-month baseline.
        """

        record = self._resolve(water_body_id, demo)
        name = str(record.feature["properties"].get("name") or water_body_id)
        warnings: list[str] = []
        steps: list[Step] = []

        series, source, index_version = await self._load_series(record, demo, warnings, steps)
        baseline = build_baseline(series, years=self.settings.history_years)
        if baseline is None:
            raise HistorySourceError(
                "No valid monthly observations were available, so no baseline could be built."
            )
        trend = build_trend(series)
        current = next((item for item in reversed(series) if item.observed), None)
        flag = evaluate_flag(
            series,
            baseline,
            current=current,
            drop_threshold_pct=self.settings.flag_drop_pct,
            min_months=self.settings.history_min_months,
            min_month_samples=3,
        )

        steps.append(
            Step(
                name="seasonal_baseline",
                status="ok",
                detail=(
                    f"Built a {baseline.window_years}-year monthly climatology; the "
                    f"natural low is {baseline.survival_threshold_m2:,.0f} m²."
                ),
            )
        )
        if flag.classification == "insufficient_data":
            steps.append(
                Step(
                    name="flag",
                    status="warning",
                    detail="Not enough same-month history to raise or clear a flag.",
                )
            )
        else:
            steps.append(
                Step(
                    name="flag",
                    status="warning" if flag.flag else "ok",
                    detail=(
                        f"{flag.drop_pct:+.1f}% versus the same-month baseline; "
                        f"{'flagged' if flag.flag else 'inside tolerance'} "
                        f"({self.settings.flag_drop_pct:.0f}% threshold)."
                    ),
                )
            )

        return HistoryMetrics(
            record=record,
            name=name,
            series=series,
            baseline=baseline,
            trend=trend,
            current=current,
            flag=flag,
            source=source,
            index_version=index_version,
            steps=steps,
            warnings=warnings,
        )

    async def _pipeline(
        self,
        water_body_id: str,
        language: str,
        *,
        demo: bool,
        speak: bool,
    ) -> AsyncIterator[tuple[Any, HistoricalProfile | None]]:
        m = await self.metrics(water_body_id, demo=demo)
        name = m.name
        series = m.series
        baseline = m.baseline
        trend = m.trend
        current = m.current
        flag = m.flag
        source = m.source
        index_version = m.index_version
        warnings = m.warnings
        steps = m.steps

        baseline_model = self._baseline_model(baseline)
        trend_model = self._trend_model(trend)
        flag_model = self._flag_model(flag, water_body_id, source, index_version)
        points = self._series_model(series)

        yield (
            HistoricalMessage(
                water_body_id=water_body_id,
                source=source,
                index_version=index_version,
                series=points,
                baseline=baseline_model,
                trend=trend_model,
                steps=list(steps),
                warnings=list(warnings),
            ),
            None,
        )
        yield (FlagMessage(water_body_id=water_body_id, flag=flag_model), None)

        current_area = (
            flag.area_m2 if flag.evaluated_month else (current.area_m2 if current else 0.0)
        )
        payload = metrics_payload(
            name=name,
            baseline=baseline,
            trend=trend,
            flag=flag,
            current_area_m2=current_area,
            current_date=current.date.isoformat() if current else "",
            drop_threshold_pct=self.settings.flag_drop_pct,
        )
        analysis, analysis_source, warning = await analyze_history(payload, self.settings)
        if warning:
            warnings.append(warning)
        steps.append(
            Step(
                name="trend_narration",
                status="ok" if analysis_source == "nvidia" else "warning",
                detail=(
                    "NVIDIA NeMo wrote the trend briefing from the computed metrics."
                    if analysis_source == "nvidia"
                    else "Used the deterministic briefing because the LLM was unavailable."
                ),
            )
        )

        localized: str | None = None
        audio: str | None = None
        audio_mime: str | None = None

        if language != "en":
            if self.settings.bhashini_configured:
                try:
                    localized = await self.bhashini.translate(
                        analysis["summary_en"], "en", language
                    )
                    steps.append(
                        Step(
                            name="localization",
                            status="ok",
                            detail="Bhashini translated the briefing.",
                        )
                    )
                except BhashiniError as exc:
                    warnings.append(f"Briefing translation failed: {exc}")
                    steps.append(Step(name="localization", status="warning", detail=str(exc)))
            else:
                warnings.append("Bhashini is not configured; the briefing stays in English.")

        if speak:
            if self.settings.bhashini_configured:
                try:
                    audio, audio_mime = await self.bhashini.text_to_speech(
                        localized or analysis["summary_en"], language
                    )
                    steps.append(
                        Step(
                            name="voice_briefing",
                            status="ok",
                            detail="Bhashini TTS briefing audio is ready.",
                        )
                    )
                except BhashiniError as exc:
                    warnings.append(f"Briefing audio failed: {exc}")
                    steps.append(Step(name="voice_briefing", status="warning", detail=str(exc)))
            else:
                warnings.append("Bhashini is not configured; briefing audio was skipped.")

        analysis_model = TrendAnalysis(
            summary_en=analysis["summary_en"],
            localized_summary=localized,
            language=language,
            seasonal_pattern=analysis.get("seasonal_pattern", ""),
            drought_window=analysis.get("drought_window", ""),
            anomaly=analysis.get("anomaly", ""),
            confidence=analysis.get("confidence", 0.5),
            evidence=list(analysis.get("evidence") or []),
            source="nvidia" if analysis_source == "nvidia" else "deterministic",
            audio_base64=audio,
            audio_mime=audio_mime,
        )

        profile = HistoricalProfile(
            water_body_id=water_body_id,
            name=name,
            language=language,
            source=source,
            index_version=index_version,
            generated_at=datetime.now(UTC).isoformat(),
            series=points,
            baseline=baseline_model,
            trend=trend_model,
            flag=flag_model,
            analysis=analysis_model,
            steps=steps,
            warnings=warnings,
        )
        yield (
            BriefingMessage(
                water_body_id=water_body_id, language=language, analysis=analysis_model
            ),
            None,
        )
        yield (None, profile)

    async def profile(
        self,
        water_body_id: str,
        language: str = "en",
        *,
        demo: bool = False,
        speak: bool = True,
    ) -> HistoricalProfile:
        key = self._cache_key(water_body_id, language, demo, speak)
        cached = self.cache.get(key)
        if cached is not None:
            return cached.model_copy(update={"cached": True})

        result: HistoricalProfile | None = None
        async for _, profile in self._pipeline(water_body_id, language, demo=demo, speak=speak):
            if profile is not None:
                result = profile
        if result is None:  # pragma: no cover - defensive
            raise HistorySourceError("The history pipeline produced no profile.")
        self.cache.put(key, result)
        return result

    async def stream(
        self,
        water_body_id: str,
        language: str = "en",
        *,
        demo: bool = False,
        speak: bool = True,
    ) -> AsyncIterator[HistoricalMessage | FlagMessage | BriefingMessage]:
        key = self._cache_key(water_body_id, language, demo, speak)
        cached = self.cache.get(key)
        if cached is not None:
            yield HistoricalMessage(
                water_body_id=cached.water_body_id,
                source=cached.source,
                index_version=cached.index_version,
                series=cached.series,
                baseline=cached.baseline,
                trend=cached.trend,
                steps=cached.steps,
                warnings=[*cached.warnings, "Served from the in-process history cache."],
            )
            yield FlagMessage(water_body_id=cached.water_body_id, flag=cached.flag)
            yield BriefingMessage(
                water_body_id=cached.water_body_id,
                language=cached.language,
                analysis=cached.analysis,
            )
            return

        result: HistoricalProfile | None = None
        async for message, profile in self._pipeline(
            water_body_id, language, demo=demo, speak=speak
        ):
            if message is not None:
                yield message
            if profile is not None:
                result = profile
        if result is not None:
            self.cache.put(key, result)
