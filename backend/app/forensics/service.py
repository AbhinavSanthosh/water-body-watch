"""Phase 4 orchestration: run the LangGraph forensic graph for one lake.

Reuses the Phase 2 history metrics (baseline, series) and the Phase 3 live flag,
then runs velocity -> geometry -> NDBI -> classify -> narrate -> localize. The
result is exposed over REST and a typed WebSocket stream.
"""

from __future__ import annotations

import hashlib
import logging
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.forensics.graph import build_graph
from app.history.cache import HistoryCache
from app.history.service import HistoryService
from app.live.service import LiveService
from app.models import (
    BufferUrbanization,
    DiagnosisMessage,
    ForensicBriefingMessage,
    ForensicDiagnosis,
    ForensicMessage,
    ForensicProfile,
    ForensicReport,
    RecessionMetrics,
    RetreatGeometry,
    Step,
)
from app.services.bhashini import BhashiniClient
from app.services.osm import WaterBodyRecord, load_demo_records
from app.services.store import WaterBodyStore

logger = logging.getLogger(__name__)


class ForensicNotFound(LookupError):
    """The water body is not in the in-process store."""


class ForensicService:
    def __init__(
        self,
        settings: Settings,
        store: WaterBodyStore,
        history: HistoryService | None = None,
        live: LiveService | None = None,
        bhashini: BhashiniClient | None = None,
        cache: HistoryCache | None = None,
        report_transport: Any = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.bhashini = bhashini or BhashiniClient(settings)
        self.history = history or HistoryService(settings, store, self.bhashini)
        self.live = live or LiveService(settings, store, self.history, self.bhashini)
        self.cache = cache or HistoryCache(ttl_s=settings.history_cache_ttl_s)
        self.graph = build_graph(
            settings, report_transport=report_transport, bhashini=self.bhashini
        )

    def _resolve(self, water_body_id: str, demo: bool) -> WaterBodyRecord:
        record = self.store.get(water_body_id)
        if record is None and demo:
            self.store.put_many(load_demo_records(self.settings))
            record = self.store.get(water_body_id)
        if record is None:
            raise ForensicNotFound(
                "Water body not found in this process. Run a discovery query first."
            )
        return record

    def _cache_key(
        self, water_body_id: str, language: str, demo: bool, speak: bool
    ) -> tuple[object, ...]:
        return (
            water_body_id,
            self.settings.forensic_index_version,
            language,
            speak,
            demo,
            self.settings.live_index_version,
        )

    def _style(self, water_body_id: str, classification: str) -> str:
        """Choose a synthetic retreat shape, biased by the live classification."""

        seed = int.from_bytes(hashlib.sha256(f"{water_body_id}:style".encode()).digest()[:8], "big")
        rng = random.Random(seed)
        if classification in {"seasonal_drought", "normal"}:
            return "concentric"
        if classification == "permanent_loss":
            return rng.choice(["one_sided", "one_sided", "mixed"])
        return rng.choice(["concentric", "mixed"])

    async def _initial_state(
        self, water_body_id: str, language: str, *, demo: bool, speak: bool
    ) -> dict[str, Any]:
        record = self._resolve(water_body_id, demo)
        name = str(record.feature["properties"].get("name") or water_body_id)

        metrics = await self.history.metrics(water_body_id, demo=demo)
        live_profile = await self.live.profile(water_body_id, "en", demo=demo, speak=False)
        live_flag = live_profile.flag

        return {
            "water_body_id": water_body_id,
            "name": name,
            "language": language,
            "speak": speak,
            "record": record,
            "series": metrics.series,
            "baseline": metrics.baseline,
            "max_geometry": record.mask_geometry,
            "target_area_m2": live_flag.area_m2,
            "style": self._style(water_body_id, live_flag.classification),
            "live_flag": live_flag.model_dump(),
            "index_version": f"{self.settings.forensic_index_version}-{live_flag.source}",
            "steps": [],
            "warnings": [],
        }

    def _profile_from_state(self, state: dict[str, Any]) -> ForensicProfile:
        geometry = dict(state["geometry"])
        current_geometry = geometry.pop("current_geometry")

        recession = RecessionMetrics(**state["recession"])
        retreat = RetreatGeometry(current_geometry=current_geometry, **geometry)
        ndbi = BufferUrbanization(**state["ndbi"])
        diagnosis = ForensicDiagnosis(
            classification=state["diagnosis"]["classification"],
            confidence=state["diagnosis"]["confidence"],
            evidence=list(state["diagnosis"]["evidence"]),
            index_version=state["index_version"],
        )

        report_state = state["report"]
        report = ForensicReport(
            headline=report_state["headline"],
            report_en=report_state["report_en"],
            localized_report=state.get("localized_report"),
            language=state["language"],
            recommended_action=report_state["recommended_action"],
            confidence=report_state["confidence"],
            evidence=list(report_state.get("evidence") or []),
            source=report_state.get("source", "deterministic"),
            audio_base64=state.get("audio_base64"),
            audio_mime=state.get("audio_mime"),
        )

        steps = [Step(**item) for item in state.get("steps", [])]
        return ForensicProfile(
            water_body_id=state["water_body_id"],
            name=state["name"],
            language=state["language"],
            source=state["live_flag"].get("source", "synthetic-demo"),
            index_version=state["index_version"],
            generated_at=datetime.now(UTC).isoformat(),
            recession=recession,
            geometry=retreat,
            ndbi=ndbi,
            diagnosis=diagnosis,
            report=report,
            steps=steps,
            warnings=list(state.get("warnings", [])),
        )

    async def _run(
        self, water_body_id: str, language: str, *, demo: bool, speak: bool
    ) -> ForensicProfile:
        state = await self._initial_state(water_body_id, language, demo=demo, speak=speak)
        final = await self.graph.ainvoke(state)
        return self._profile_from_state(final)

    async def profile(
        self,
        water_body_id: str,
        language: str = "en",
        *,
        demo: bool = False,
        speak: bool = True,
    ) -> ForensicProfile:
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
    ) -> AsyncIterator[ForensicMessage | DiagnosisMessage | ForensicBriefingMessage]:
        key = self._cache_key(water_body_id, language, demo, speak)
        cached = self.cache.get(key)
        if cached is not None:
            profile: ForensicProfile = cached  # type: ignore[assignment]
            yield ForensicMessage(
                water_body_id=profile.water_body_id,
                source=profile.source,
                index_version=profile.index_version,
                recession=profile.recession,
                geometry=profile.geometry,
                ndbi=profile.ndbi,
                diagnosis=profile.diagnosis,
                steps=profile.steps,
                warnings=[*profile.warnings, "Served from the in-process forensic cache."],
            )
            yield DiagnosisMessage(water_body_id=profile.water_body_id, diagnosis=profile.diagnosis)
            yield ForensicBriefingMessage(
                water_body_id=profile.water_body_id,
                language=profile.language,
                report=profile.report,
            )
            return

        profile = await self._run(water_body_id, language, demo=demo, speak=speak)
        self.cache.put(key, profile)
        yield ForensicMessage(
            water_body_id=profile.water_body_id,
            source=profile.source,
            index_version=profile.index_version,
            recession=profile.recession,
            geometry=profile.geometry,
            ndbi=profile.ndbi,
            diagnosis=profile.diagnosis,
            steps=profile.steps,
            warnings=profile.warnings,
        )
        yield DiagnosisMessage(water_body_id=profile.water_body_id, diagnosis=profile.diagnosis)
        yield ForensicBriefingMessage(
            water_body_id=profile.water_body_id,
            language=profile.language,
            report=profile.report,
        )
