"""Phase 3 orchestration: Sentinel-2 -> NDWI -> flag -> multilingual alert.

The NDWI pixel count is deterministic and always stored. The LLM writes the
alert text but cannot change the flag. A result is cached per lake per day so
repeat selections do not re-query Sentinel Hub.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.config import Settings
from app.geo.indices import masked_area_m2, ndwi, pixel_area_m2, valid_pixel_mask
from app.geo.raster import polygon_pixel_mask
from app.history.baseline import MONTH_NAMES
from app.history.cache import HistoryCache
from app.history.service import HistoryMetrics, HistoryService
from app.live.alert import analyze_live
from app.live.sentinel import (
    LiveScene,
    SentinelHubError,
    fetch_sentinel_scene,
    scene_cloud_pct,
    synthetic_scene,
)
from app.models import (
    LiveAlert,
    LiveAlertMessage,
    LiveFlag,
    LiveFlagMessage,
    LiveMessage,
    LiveProfile,
    LiveSceneInfo,
    SeasonalBaseline,
    Step,
)
from app.services.bhashini import BhashiniClient, BhashiniError
from app.services.osm import WaterBodyRecord, load_demo_records
from app.services.store import WaterBodyStore

logger = logging.getLogger(__name__)


class LiveNotFound(LookupError):
    """The water body is not in the in-process store."""


@dataclass
class _LiveResult:
    record: WaterBodyRecord
    name: str
    scene: LiveScene
    scene_info: LiveSceneInfo
    flag: LiveFlag
    baseline: SeasonalBaseline
    permanent_change_pct: float
    steps: list[Step]
    warnings: list[str]
    index_version: str


def _seed_for(water_body_id: str, today: date) -> int:
    key = f"{water_body_id}:{today.year}-{today.month:02d}"
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")


class LiveService:
    def __init__(
        self,
        settings: Settings,
        store: WaterBodyStore,
        history: HistoryService | None = None,
        bhashini: BhashiniClient | None = None,
        cache: HistoryCache | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.bhashini = bhashini or BhashiniClient(settings)
        self.history = history or HistoryService(settings, store, self.bhashini)
        self.cache = cache or HistoryCache(ttl_s=900.0)

    # -- resolution ---------------------------------------------------------

    def _resolve(self, water_body_id: str, demo: bool) -> WaterBodyRecord:
        record = self.store.get(water_body_id)
        if record is None and demo:
            self.store.put_many(load_demo_records(self.settings))
            record = self.store.get(water_body_id)
        if record is None:
            raise LiveNotFound("Water body not found in this process. Run a discovery query first.")
        return record

    def _cache_key(
        self, water_body_id: str, language: str, demo: bool, speak: bool
    ) -> tuple[object, ...]:
        return (
            water_body_id,
            date.today().isoformat(),
            self.settings.live_index_version,
            language,
            speak,
            demo,
            self.settings.sentinel_hub_configured,
        )

    # -- scene --------------------------------------------------------------

    def _synthetic_target(self, baseline_area_m2: float, water_body_id: str) -> float:
        """Pick a realistic synthetic reading, with roughly a third flagged."""

        rng = random.Random(_seed_for(water_body_id, date.today()))
        return baseline_area_m2 * rng.uniform(0.72, 1.06)

    async def _load_scene(
        self,
        record: WaterBodyRecord,
        demo: bool,
        target_area_m2: float,
        warnings: list[str],
        steps: list[Step],
    ) -> LiveScene:
        def use_synthetic(reason: str, detail: str) -> LiveScene:
            warnings.append(reason)
            steps.append(Step(name="sentinel_scene", status="warning", detail=detail))
            return synthetic_scene(record, self.settings, target_area_m2=target_area_m2)

        if demo:
            return use_synthetic(
                "Demo mode uses a synthetic Sentinel-2 scene, not Sentinel Hub.",
                "Generated a synthetic B03/B08/SCL scene for the NDWI demo.",
            )

        if self.settings.sentinel_hub_configured:
            try:
                scene = await fetch_sentinel_scene(record, self.settings)
            except SentinelHubError as exc:
                if not self.settings.live_allow_synthetic:
                    raise
                logger.warning("Sentinel Hub failed for %s: %s", record.water_body_id, exc)
                return use_synthetic(
                    f"Sentinel Hub was unavailable; using the synthetic scene ({exc}).",
                    "Sentinel Hub failed, so the offline synthetic scene was used.",
                )
            steps.append(
                Step(
                    name="sentinel_scene",
                    status="ok",
                    detail=(
                        f"Fetched a {scene.width}×{scene.height} Sentinel-2 L2A scene "
                        f"for {scene.window_start} → {scene.window_end}."
                    ),
                )
            )
            return scene

        if not self.settings.live_allow_synthetic:
            raise SentinelHubError(
                "Sentinel Hub is not configured. Set SH_CLIENT_ID and SH_CLIENT_SECRET, "
                "or set LIVE_ALLOW_SYNTHETIC=true."
            )

        return use_synthetic(
            "Sentinel Hub is not configured; using a synthetic Sentinel-2 scene. "
            "Set SH_CLIENT_ID and SH_CLIENT_SECRET for live NDWI.",
            "Generated a synthetic B03/B08/SCL scene for the NDWI demo.",
        )

    # -- result -------------------------------------------------------------

    async def _compute(self, water_body_id: str, *, demo: bool) -> _LiveResult:
        record = self._resolve(water_body_id, demo)
        name = str(record.feature["properties"].get("name") or water_body_id)

        metrics: HistoryMetrics = await self.history.metrics(water_body_id, demo=demo)
        warnings = list(metrics.warnings)
        steps = list(metrics.steps)

        today = date.today()
        month = today.month
        baseline_area = metrics.baseline.month_median(month)
        month_samples = metrics.baseline.month_samples(month)

        target_area = self._synthetic_target(baseline_area, water_body_id)
        scene = await self._load_scene(record, demo, target_area, warnings, steps)

        values = ndwi(scene.b03, scene.b08)
        valid = valid_pixel_mask(scene.scl, values.shape)
        inside = polygon_pixel_mask(record.mask_geometry, scene.bounds, scene.width, scene.height)
        cell_area = pixel_area_m2(scene.bounds, scene.width, scene.height)
        area_m2, water_pixels, valid_pixels = masked_area_m2(
            values,
            inside,
            valid,
            cell_area,
            threshold=self.settings.live_ndwi_threshold,
        )
        inside_pixels = int(inside.sum())
        valid_pct = (100.0 * valid_pixels / inside_pixels) if inside_pixels else 0.0
        cloud_pct = scene_cloud_pct(scene)

        drop_pct = (baseline_area - area_m2) / baseline_area * 100 if baseline_area > 0 else 0.0
        enough_history = (
            metrics.trend.months_observed >= self.settings.history_min_months and month_samples >= 3
        )
        flagged = enough_history and drop_pct > self.settings.flag_drop_pct

        if not enough_history or valid_pixels == 0:
            classification = "insufficient_data"
        elif flagged and metrics.trend.permanent_change_pct < -(self.settings.flag_drop_pct / 2):
            classification = "permanent_loss"
        elif flagged:
            classification = "seasonal_drought"
        else:
            classification = "normal"

        history_factor = min(
            1.0, metrics.trend.months_observed / (self.settings.history_min_months * 2)
        )
        anomaly_factor = min(1.0, abs(drop_pct) / (self.settings.flag_drop_pct * 2))
        quality_factor = max(
            0.0,
            1.0 - min(0.3, cloud_pct / 200) - (1.0 - valid_pct / 100) * 0.3,
        )
        confidence = round(
            max(
                0.0,
                min(
                    0.95,
                    0.3 + 0.4 * history_factor + 0.2 * anomaly_factor + 0.1 * quality_factor,
                ),
            ),
            2,
        )

        month_name = MONTH_NAMES[month - 1]
        evidence = [
            f"Sentinel-2 window {scene.window_start} → {scene.window_end}: "
            f"{cloud_pct:.0f}% of pixels masked by the SCL.",
            f"NDWI > {self.settings.live_ndwi_threshold} over the lakebed polygon: "
            f"{water_pixels:,} water pixels at {cell_area:.0f} m² each.",
            f"Live area {area_m2:,.0f} m² versus the {month_name} JRC baseline "
            f"{baseline_area:,.0f} m² ({-drop_pct:+.1f}%).",
        ]
        if valid_pixels == 0:
            evidence.append("No valid pixels remained after cloud masking.")
        elif not enough_history:
            evidence.append(
                f"Only {metrics.trend.months_observed} historical months and "
                f"{month_samples} {month_name} samples are available."
            )

        steps.append(
            Step(
                name="ndwi",
                status="ok" if valid_pixels else "warning",
                detail=(
                    f"NDWI > {self.settings.live_ndwi_threshold} gave "
                    f"{water_pixels:,} water pixels ({area_m2:,.0f} m²)."
                ),
            )
        )
        steps.append(
            Step(
                name="live_flag",
                status="warning" if flagged else "ok",
                detail=(
                    f"{-drop_pct:+.1f}% versus the {month_name} baseline; "
                    f"{classification.replace('_', ' ')}."
                ),
            )
        )

        flag = LiveFlag(
            water_body_id=water_body_id,
            area_m2=area_m2,
            baseline_area_m2=baseline_area,
            drop_pct=drop_pct,
            flag=flagged,
            classification=classification,  # type: ignore[arg-type]
            confidence=confidence,
            threshold_pct=self.settings.flag_drop_pct,
            ndwi_threshold=self.settings.live_ndwi_threshold,
            evaluated_month=month,
            water_pixels=water_pixels,
            valid_pixels=valid_pixels,
            pixel_area_m2=cell_area,
            cloud_pct=cloud_pct,
            source=scene.source,
            index_version=f"{self.settings.live_index_version}-{scene.source}",
            evidence=evidence,
        )

        scene_info = LiveSceneInfo(
            source=scene.source,
            bounds=list(scene.bounds),
            width=scene.width,
            height=scene.height,
            resolution_m=math.sqrt(cell_area),
            cloud_pct=cloud_pct,
            valid_pixel_pct=valid_pct,
            observed_at=scene.observed_at,
            window_start=scene.window_start,
            window_end=scene.window_end,
        )

        return _LiveResult(
            record=record,
            name=name,
            scene=scene,
            scene_info=scene_info,
            flag=flag,
            baseline=HistoryService._baseline_model(metrics.baseline),
            permanent_change_pct=metrics.trend.permanent_change_pct,
            steps=steps,
            warnings=warnings,
            index_version=flag.index_version,
        )

    # -- pipeline -----------------------------------------------------------

    def _alert_payload(self, result: _LiveResult) -> dict[str, Any]:
        flag = result.flag
        return {
            "lake": result.name,
            "area_m2": round(flag.area_m2, 1),
            "baseline_area_m2": round(flag.baseline_area_m2, 1),
            "drop_pct": round(flag.drop_pct, 2),
            "flag": flag.flag,
            "classification": flag.classification,
            "threshold_pct": flag.threshold_pct,
            "month_name": MONTH_NAMES[flag.evaluated_month - 1],
            "ndwi_threshold": flag.ndwi_threshold,
            "cloud_pct": round(flag.cloud_pct, 1),
            "water_pixels": flag.water_pixels,
            "valid_pixels": flag.valid_pixels,
            "pixel_area_m2": round(flag.pixel_area_m2, 2),
            "scene_source": flag.source,
            "observed_at": result.scene_info.observed_at,
            "confidence": flag.confidence,
            "permanent_change_pct": round(result.permanent_change_pct, 2),
        }

    async def _pipeline(
        self,
        water_body_id: str,
        language: str,
        *,
        demo: bool,
        speak: bool,
    ) -> AsyncIterator[tuple[Any, LiveProfile | None]]:
        result = await self._compute(water_body_id, demo=demo)
        flag = result.flag
        warnings = result.warnings
        steps = result.steps
        index_version = result.index_version

        yield (
            LiveMessage(
                water_body_id=water_body_id,
                observed_at=result.scene_info.observed_at,
                source=flag.source,
                index_version=index_version,
                scene=result.scene_info,
                flag=flag,
                baseline=result.baseline,
                steps=list(steps),
                warnings=list(warnings),
            ),
            None,
        )
        yield (LiveFlagMessage(water_body_id=water_body_id, flag=flag), None)

        alert_payload = self._alert_payload(result)
        alert, alert_source, warning = await analyze_live(alert_payload, self.settings)
        if warning:
            warnings.append(warning)
        steps.append(
            Step(
                name="alert_narration",
                status="ok" if alert_source == "nvidia" else "warning",
                detail=(
                    "NVIDIA NeMo wrote the anomaly report."
                    if alert_source == "nvidia"
                    else "Used the deterministic anomaly report."
                ),
            )
        )

        localized: str | None = None
        audio: str | None = None
        audio_mime: str | None = None

        if language != "en":
            if self.settings.bhashini_configured:
                try:
                    localized = await self.bhashini.translate(alert["summary_en"], "en", language)
                    steps.append(
                        Step(
                            name="localization",
                            status="ok",
                            detail="Bhashini translated the alert.",
                        )
                    )
                except BhashiniError as exc:
                    warnings.append(f"Alert translation failed: {exc}")
                    steps.append(Step(name="localization", status="warning", detail=str(exc)))
            else:
                warnings.append("Bhashini is not configured; the alert stays in English.")

        # Voice is reserved for actual alerts; a normal reading stays text-only.
        if speak and flag.flag:
            if self.settings.bhashini_configured:
                spoken = f"{alert['headline']}. {localized or alert['summary_en']}"
                try:
                    audio, audio_mime = await self.bhashini.text_to_speech(spoken, language)
                    steps.append(
                        Step(
                            name="voice_alert",
                            status="ok",
                            detail="Bhashini TTS alert audio is ready.",
                        )
                    )
                except BhashiniError as exc:
                    warnings.append(f"Alert audio failed: {exc}")
                    steps.append(Step(name="voice_alert", status="warning", detail=str(exc)))
            else:
                warnings.append("Bhashini is not configured; alert audio was skipped.")

        alert_model = LiveAlert(
            headline=alert["headline"],
            summary_en=alert["summary_en"],
            localized_summary=localized,
            language=language,
            severity=alert["severity"],  # type: ignore[arg-type]
            recommended_action=alert["recommended_action"],
            confidence=alert["confidence"],
            evidence=list(alert.get("evidence") or []),
            source="nvidia" if alert_source == "nvidia" else "deterministic",
            audio_base64=audio,
            audio_mime=audio_mime,
        )

        profile = LiveProfile(
            water_body_id=water_body_id,
            name=result.name,
            language=language,
            observed_at=result.scene_info.observed_at,
            source=flag.source,
            index_version=index_version,
            scene=result.scene_info,
            flag=flag,
            alert=alert_model,
            baseline=result.baseline,
            steps=steps,
            warnings=warnings,
        )
        yield (
            LiveAlertMessage(water_body_id=water_body_id, language=language, alert=alert_model),
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
    ) -> LiveProfile:
        key = self._cache_key(water_body_id, language, demo, speak)
        cached = self.cache.get(key)
        if cached is not None:
            return cached.model_copy(update={"cached": True})  # type: ignore[return-value]

        result: LiveProfile | None = None
        async for _, profile in self._pipeline(water_body_id, language, demo=demo, speak=speak):
            if profile is not None:
                result = profile
        if result is None:  # pragma: no cover - defensive
            raise SentinelHubError("The live pipeline produced no profile.")
        self.cache.put(key, result)
        return result

    async def stream(
        self,
        water_body_id: str,
        language: str = "en",
        *,
        demo: bool = False,
        speak: bool = True,
    ) -> AsyncIterator[LiveMessage | LiveFlagMessage | LiveAlertMessage]:
        key = self._cache_key(water_body_id, language, demo, speak)
        cached = self.cache.get(key)
        if cached is not None:
            profile: LiveProfile = cached  # type: ignore[assignment]
            yield LiveMessage(
                water_body_id=profile.water_body_id,
                observed_at=profile.observed_at,
                source=profile.source,
                index_version=profile.index_version,
                scene=profile.scene,
                flag=profile.flag,
                baseline=profile.baseline,
                steps=profile.steps,
                warnings=[*profile.warnings, "Served from the in-process live cache."],
            )
            yield LiveFlagMessage(water_body_id=profile.water_body_id, flag=profile.flag)
            yield LiveAlertMessage(
                water_body_id=profile.water_body_id,
                language=profile.language,
                alert=profile.alert,
            )
            return

        result: LiveProfile | None = None
        async for message, profile in self._pipeline(
            water_body_id, language, demo=demo, speak=speak
        ):
            if message is not None:
                yield message
            if profile is not None:
                result = profile
        if result is not None:
            self.cache.put(key, result)
