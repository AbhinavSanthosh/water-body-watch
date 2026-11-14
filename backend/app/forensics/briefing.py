"""Phase 5 orchestration: patch extraction -> VLM inspection -> localization.

Runs as a small LangGraph graph. It reuses the Phase 4 diagnosis (carried into
the final payload) and adds the visual inspection and multilingual briefing.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.forensics.service import ForensicService
from app.forensics.vision import analyze_vision, deterministic_inspection, render_patches
from app.history.cache import HistoryCache
from app.models import (
    ImagePatch,
    Step,
    VisionBriefingMessage,
    VisionInspection,
    VisionMessage,
    VisionProfile,
)
from app.services.bhashini import BhashiniClient, BhashiniError
from app.services.osm import WaterBodyRecord, load_demo_records
from app.services.store import WaterBodyStore

logger = logging.getLogger(__name__)


class VisionNotFound(LookupError):
    """The water body is not in the in-process store."""


class VisionState(TypedDict, total=False):
    water_body_id: str
    name: str
    language: str
    speak: bool
    max_geometry: dict[str, Any]
    current_geometry: dict[str, Any]
    classification: str
    location: str
    patches: list[dict[str, Any]]
    image_bytes: list[bytes]
    inspection: dict[str, Any]
    localized_report: str | None
    audio_base64: str | None
    audio_mime: str | None
    steps: list[dict[str, Any]]
    warnings: list[str]


def _step(steps: list[dict[str, Any]], name: str, status: str, detail: str) -> list[dict[str, Any]]:
    return [*steps, Step(name=name, status=status, detail=detail).model_dump()]  # type: ignore[arg-type]


def _location_for(classification: str) -> str:
    if classification in {"encroachment", "encroachment_suspected"}:
        return "the eastern bank"
    if classification == "siltation_debris":
        return "the water surface"
    return "the shoreline margin"


def build_vision_graph(
    settings: Settings,
    *,
    bhashini: BhashiniClient | None = None,
    vision_transport: httpx.AsyncBaseTransport | None = None,
):
    bhashini = bhashini or BhashiniClient(settings)

    async def extract_node(state: VisionState) -> dict[str, Any]:
        today = datetime.now(UTC).date()
        historical_date = (today - timedelta(days=3650)).isoformat()
        historical, current = render_patches(
            state["max_geometry"],
            state["current_geometry"],
            state["classification"],
            settings,
        )
        patches = [
            {
                "role": "historical",
                "date": historical_date,
                "image_base64": base64.b64encode(historical).decode(),
                "mime": "image/png",
            },
            {
                "role": "current",
                "date": today.isoformat(),
                "image_base64": base64.b64encode(current).decode(),
                "mime": "image/png",
            },
        ]
        steps = _step(
            state.get("steps", []),
            "extract_patches",
            "ok",
            f"Rendered {settings.vision_patch_size_px}px before/after RGB patches.",
        )
        return {"patches": patches, "image_bytes": [historical, current], "steps": steps}

    async def inspect_node(state: VisionState) -> dict[str, Any]:
        classification = state["classification"]
        location = state["location"]
        prompt = (
            f"Inspect this before/after pair for the lake classified as "
            f"classification={classification}, location={location}. "
            "Describe any visible markers of encroachment, siltation, or drought."
        )
        fallback = deterministic_inspection(classification, location)
        inspection, source, warning = await analyze_vision(
            state["image_bytes"], prompt, fallback, settings, transport=vision_transport
        )
        warnings = list(state.get("warnings", []))
        if warning:
            warnings.append(warning)
        steps = _step(
            state.get("steps", []),
            "vision_inspection",
            "ok" if source == "nvidia" else "warning",
            "NVIDIA VLM wrote the visual report."
            if source == "nvidia"
            else "Used the deterministic visual report.",
        )
        inspection["source"] = source
        return {"inspection": inspection, "warnings": warnings, "steps": steps}

    async def localize_node(state: VisionState) -> dict[str, Any]:
        inspection = state["inspection"]
        language = state["language"]
        speak = state.get("speak", False)
        warnings = list(state.get("warnings", []))
        steps = state.get("steps", [])

        localized: str | None = None
        if language != "en":
            if settings.bhashini_configured:
                try:
                    localized = await bhashini.translate(inspection["report_en"], "en", language)
                    steps = _step(steps, "localization", "ok", "Bhashini translated the briefing.")
                except BhashiniError as exc:
                    warnings.append(f"Briefing translation failed: {exc}")
                    steps = _step(steps, "localization", "warning", str(exc))
            else:
                warnings.append("Bhashini is not configured; the briefing stays in English.")

        audio = None
        audio_mime = None
        actionable = state["classification"] in {
            "encroachment",
            "encroachment_suspected",
            "siltation_debris",
        }
        if speak and actionable:
            if settings.bhashini_configured:
                spoken = f"{inspection['headline']}. {localized or inspection['report_en']}"
                try:
                    audio, audio_mime = await bhashini.text_to_speech(spoken, language)
                    steps = _step(steps, "voice_briefing", "ok", "Bhashini TTS briefing ready.")
                except BhashiniError as exc:
                    warnings.append(f"Briefing audio failed: {exc}")
                    steps = _step(steps, "voice_briefing", "warning", str(exc))
            else:
                warnings.append("Bhashini is not configured; briefing audio was skipped.")

        return {
            "localized_report": localized,
            "audio_base64": audio,
            "audio_mime": audio_mime,
            "warnings": warnings,
            "steps": steps,
        }

    builder = StateGraph(VisionState)
    builder.add_node("extract", extract_node)
    builder.add_node("inspect", inspect_node)
    builder.add_node("localize", localize_node)
    builder.add_edge(START, "extract")
    builder.add_edge("extract", "inspect")
    builder.add_edge("inspect", "localize")
    builder.add_edge("localize", END)
    return builder.compile()


class BriefingService:
    def __init__(
        self,
        settings: Settings,
        store: WaterBodyStore,
        forensics: ForensicService | None = None,
        bhashini: BhashiniClient | None = None,
        cache: HistoryCache | None = None,
        vision_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.bhashini = bhashini or BhashiniClient(settings)
        self.forensics = forensics or ForensicService(settings, store, bhashini=self.bhashini)
        self.cache = cache or HistoryCache(ttl_s=settings.history_cache_ttl_s)
        self.graph = build_vision_graph(
            settings, bhashini=self.bhashini, vision_transport=vision_transport
        )

    def _resolve(self, water_body_id: str, demo: bool) -> WaterBodyRecord:
        record = self.store.get(water_body_id)
        if record is None and demo:
            self.store.put_many(load_demo_records(self.settings))
            record = self.store.get(water_body_id)
        if record is None:
            raise VisionNotFound(
                "Water body not found in this process. Run a discovery query first."
            )
        return record

    def _cache_key(
        self, water_body_id: str, language: str, demo: bool, speak: bool
    ) -> tuple[object, ...]:
        return (
            water_body_id,
            self.settings.vision_index_version,
            language,
            speak,
            demo,
            self.settings.forensic_index_version,
        )

    async def _run(
        self, water_body_id: str, language: str, *, demo: bool, speak: bool
    ) -> VisionProfile:
        record = self._resolve(water_body_id, demo)
        name = str(record.feature["properties"].get("name") or water_body_id)

        # Phase 4 in English (its own localization is unnecessary here).
        forensic = await self.forensics.profile(water_body_id, "en", demo=demo, speak=False)
        classification = forensic.diagnosis.classification

        initial: VisionState = {
            "water_body_id": water_body_id,
            "name": name,
            "language": language,
            "speak": speak,
            "max_geometry": record.mask_geometry,
            "current_geometry": forensic.geometry.current_geometry,
            "classification": classification,
            "location": _location_for(classification),
            "steps": [],
            "warnings": [],
        }
        final = await self.graph.ainvoke(initial)

        inspection_state = final["inspection"]
        markers = [
            {
                "marker": item["marker"],
                "severity": item["severity"],
                "confidence": item["confidence"],
                "location": item["location"],
                "description": item["description"],
            }
            for item in inspection_state["markers"]
        ]
        inspection = VisionInspection(
            headline=inspection_state["headline"],
            report_en=inspection_state["report_en"],
            localized_report=final.get("localized_report"),
            language=language,
            confidence=inspection_state["confidence"],
            markers=markers,
            source=inspection_state.get("source", "deterministic"),
            audio_base64=final.get("audio_base64"),
            audio_mime=final.get("audio_mime"),
        )

        patches = [
            ImagePatch(
                role=item["role"],
                date=item["date"],
                image_base64=item["image_base64"],
                mime=item["mime"],
            )
            for item in final["patches"]
        ]

        return VisionProfile(
            water_body_id=water_body_id,
            name=name,
            language=language,
            source=forensic.source,
            index_version=f"{self.settings.vision_index_version}-{forensic.source}",
            generated_at=datetime.now(UTC).isoformat(),
            patches=patches,
            inspection=inspection,
            diagnosis=forensic.diagnosis,
            steps=[Step(**item) for item in final.get("steps", [])],
            warnings=list(final.get("warnings", [])),
        )

    async def profile(
        self,
        water_body_id: str,
        language: str = "en",
        *,
        demo: bool = False,
        speak: bool = True,
    ) -> VisionProfile:
        key = self._cache_key(water_body_id, language, demo, speak)
        cached = self.cache.get(key)
        if cached is not None:
            return cached.model_copy(update={"cached": True})  # type: ignore[return-value]

        profile = await self._run(water_body_id, language, demo=demo, speak=speak)
        self.cache.put(key, profile)
        return profile

    async def stream(
        self,
        water_body_id: str,
        language: str = "en",
        *,
        demo: bool = False,
        speak: bool = True,
    ) -> AsyncIterator[VisionMessage | VisionBriefingMessage]:
        key = self._cache_key(water_body_id, language, demo, speak)
        cached = self.cache.get(key)
        if cached is not None:
            profile: VisionProfile = cached  # type: ignore[assignment]
            yield VisionMessage(
                water_body_id=profile.water_body_id,
                source=profile.source,
                index_version=profile.index_version,
                patches=profile.patches,
                inspection=profile.inspection,
                diagnosis=profile.diagnosis,
                steps=profile.steps,
                warnings=[*profile.warnings, "Served from the in-process vision cache."],
            )
            yield VisionBriefingMessage(
                water_body_id=profile.water_body_id,
                language=profile.language,
                inspection=profile.inspection,
            )
            return

        profile = await self._run(water_body_id, language, demo=demo, speak=speak)
        self.cache.put(key, profile)
        yield VisionMessage(
            water_body_id=profile.water_body_id,
            source=profile.source,
            index_version=profile.index_version,
            patches=profile.patches,
            inspection=profile.inspection,
            diagnosis=profile.diagnosis,
            steps=profile.steps,
            warnings=profile.warnings,
        )
        yield VisionBriefingMessage(
            water_body_id=profile.water_body_id,
            language=profile.language,
            inspection=profile.inspection,
        )
