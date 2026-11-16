"""Phase 1 orchestration: language -> intent -> geocode -> OSM -> mask -> TTS."""

from __future__ import annotations

import logging

from app.config import Settings
from app.models import DiscoveryResponse, GeocodeResult, Intent, Step
from app.services.bhashini import BhashiniClient, BhashiniError
from app.services.intent import IntentExtractionError, extract_intent, fallback_extract
from app.services.osm import (
    GeocodeError,
    OsmFetchError,
    WaterBodyRecord,
    feature_collection,
    fetch_water_bodies,
    geocode,
    load_demo_records,
)
from app.services.store import WaterBodyStore

logger = logging.getLogger(__name__)

_DEMO_LOCATION = GeocodeResult(
    display_name="Bengaluru, Karnataka, India (bundled OSM demo)",
    lat=12.9716,
    lon=77.5946,
    bbox=(77.45, 12.85, 77.75, 13.10),
    category="place",
    result_type="city",
    osm_type="relation",
    osm_id=7445844,
)


def _short_place(display_name: str) -> str:
    parts = [part.strip() for part in display_name.split(",") if part.strip()]
    return ", ".join(parts[:2]) if parts else display_name


def _is_bengaluru_query(text: str | None) -> bool:
    """A bundled offline dataset covers Bengaluru, so queries naming the city are
    served instantly without geocoding, Overpass, NVIDIA, or Bhashini."""
    lowered = (text or "").lower()
    return "bengaluru" in lowered or "bangalore" in lowered


class DiscoveryService:
    def __init__(
        self,
        settings: Settings,
        store: WaterBodyStore,
        bhashini: BhashiniClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.bhashini = bhashini or BhashiniClient(settings)

    async def _confirmation(
        self,
        english_text: str,
        language: str,
        speak: bool,
        warnings: list[str],
        steps: list[Step],
    ) -> tuple[str, str | None, str | None]:
        if not speak:
            return english_text, None, None
        if not self.settings.bhashini_configured:
            warnings.append("Bhashini is not configured; confirmation audio was skipped.")
            return english_text, None, None

        text = english_text
        if language != "en":
            try:
                text = await self.bhashini.translate(english_text, "en", language)
            except BhashiniError as exc:
                warnings.append(f"Confirmation translation failed: {exc}")
                return english_text, None, None

        try:
            audio, mime = await self.bhashini.text_to_speech(text, language)
            steps.append(
                Step(name="voice_confirmation", status="ok", detail="Bhashini TTS audio ready.")
            )
            return text, audio, mime
        except BhashiniError as exc:
            warnings.append(f"Confirmation audio failed: {exc}")
            steps.append(Step(name="voice_confirmation", status="warning", detail=str(exc)))
            return text, None, None

    def _bundled_discover(
        self,
        text: str,
        language: str,
        *,
        transcript: str | None,
    ) -> DiscoveryResponse:
        """Serve the pre-bundled Bengaluru dataset with no external calls at all.

        This is the "proper demo" path: data is pre-downloaded and pre-processed
        in the repository, so a Bengaluru query returns in milliseconds with no
        NVIDIA intent parse, no geocode, no Overpass, and no Bhashini TTS.
        """

        records = load_demo_records(self.settings)
        self.store.put_many(records)

        try:
            location = fallback_extract(text).location
        except IntentExtractionError:
            location = "Bengaluru"
        intent = Intent(
            target_entity="water_bodies",
            location=location,
            action="map_and_monitor",
        )
        steps: list[Step] = []
        if transcript is not None:
            steps.append(Step(name="speech_to_text", status="ok", detail="Bhashini ASR completed."))
        steps.extend(
            [
                Step(
                    name="intent",
                    status="ok",
                    detail="Resolved the bundled Bengaluru demo offline (no LLM call).",
                ),
                Step(
                    name="osm_ingest",
                    status="ok",
                    detail=f"Loaded {len(records)} pre-bundled Bengaluru lake polygons.",
                ),
                Step(
                    name="spatial_mask",
                    status="ok",
                    detail="Exact lakebed polygons and strict bboxes are ready.",
                ),
            ]
        )

        return DiscoveryResponse(
            query=text,
            language=language,
            transcript=transcript,
            english_text=text,
            intent=intent,
            location=_DEMO_LOCATION,
            water_bodies=feature_collection(records),
            count=len(records),
            confirmation_text=(
                f"Mapped {len(records)} water bodies near Bengaluru from the bundled offline demo."
            ),
            confirmation_audio_base64=None,
            confirmation_audio_mime=None,
            steps=steps,
            warnings=[],
        )

    async def discover(
        self,
        text: str,
        language: str = "en",
        *,
        english_text: str | None = None,
        transcript: str | None = None,
        demo: bool = False,
        speak: bool = True,
    ) -> DiscoveryResponse:
        warnings: list[str] = []
        steps: list[Step] = []

        if transcript is not None:
            steps.append(Step(name="speech_to_text", status="ok", detail="Bhashini ASR completed."))

        # Fast path: bundled offline demo. Triggered by the demo flag or by any
        # query that names Bengaluru/Bangalore. No providers, no LLM, no TTS.
        if demo or _is_bengaluru_query(text if english_text is None else english_text):
            return self._bundled_discover(text, language, transcript=transcript)

        if english_text is None:
            if language != "en":
                if not self.settings.bhashini_configured:
                    raise BhashiniError(
                        "Regional-language text needs Bhashini. Set BHASHINI_USER_ID and "
                        "BHASHINI_API_KEY, or use language 'en'."
                    )
                try:
                    english_text = await self.bhashini.translate(text, language, "en")
                    steps.append(
                        Step(
                            name="translation",
                            status="ok",
                            detail="Bhashini translated the request to English.",
                        )
                    )
                except BhashiniError as exc:
                    raise BhashiniError(f"Could not translate the request: {exc}") from exc
            else:
                english_text = text

        try:
            intent, source, warning = await extract_intent(english_text, self.settings)
        except IntentExtractionError as exc:
            raise GeocodeError(str(exc)) from exc
        if warning:
            warnings.append(warning)
        steps.append(
            Step(
                name="intent",
                status="warning" if source == "fallback" else "ok",
                detail=f"Extracted location '{intent.location}' using {source}.",
            )
        )

        if demo:
            records = load_demo_records(self.settings)
            location = _DEMO_LOCATION
            steps.append(
                Step(
                    name="osm_ingest",
                    status="ok",
                    detail=f"Loaded {len(records)} bundled OSM demo water bodies.",
                )
            )
        else:
            location = await geocode(intent.location, self.settings, radius_km=intent.radius_km)
            steps.append(
                Step(
                    name="geocode",
                    status="ok",
                    detail=f"Resolved '{intent.location}' to {location.display_name}.",
                )
            )
            try:
                records = await fetch_water_bodies(location.bbox, self.settings)
            except OsmFetchError as exc:
                raise OsmFetchError(str(exc)) from exc
            source = (
                str(records[0].feature["properties"].get("source") or "osm") if records else "osm"
            )
            if source == "osm-nominatim":
                warnings.append(
                    "Overpass was unavailable; used the Nominatim named-lake fallback, "
                    "so unnamed ponds may be missing."
                )
            steps.append(
                Step(
                    name="osm_ingest",
                    status="warning" if source == "osm-nominatim" else "ok",
                    detail=(
                        f"Fetched {len(records)} lake/pond polygons via {source}."
                        if source == "osm-nominatim"
                        else f"Fetched {len(records)} lake/pond polygons from OSM."
                    ),
                )
            )

        self.store.put_many(records)
        collection = feature_collection(records)
        steps.append(
            Step(
                name="spatial_mask",
                status="ok",
                detail="Exact lakebed polygons and strict bboxes are ready for satellite clipping.",
            )
        )

        place = _short_place(location.display_name)
        if records:
            confirmation_en = (
                f"Mapped {len(records)} water bodies near {place}. "
                "Their lakebed masks are ready and they are now in the monitoring layer."
            )
        else:
            confirmation_en = (
                f"No lakes or ponds were found near {place}. "
                "Try a nearby landmark or a larger area."
            )

        confirmation_text, audio, mime = await self._confirmation(
            confirmation_en, language, speak, warnings, steps
        )

        return DiscoveryResponse(
            query=text,
            language=language,
            transcript=transcript,
            english_text=english_text,
            intent=intent,
            location=location,
            water_bodies=collection,
            count=len(records),
            confirmation_text=confirmation_text,
            confirmation_audio_base64=audio,
            confirmation_audio_mime=mime,
            steps=steps,
            warnings=warnings,
        )


def demo_records(settings: Settings) -> list[WaterBodyRecord]:
    """Small helper used by tests and fixture tooling."""

    return load_demo_records(settings)
