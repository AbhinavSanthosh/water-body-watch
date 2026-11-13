from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.config import get_settings
from app.deps import get_discovery_service
from app.languages import LANGUAGE_CODES, LANGUAGES
from app.models import (
    DiscoveryResponse,
    HealthResponse,
    LanguageOption,
    QueryRequest,
    WaterBodyMaskResponse,
)
from app.services.bhashini import BhashiniError
from app.services.discovery import DiscoveryService
from app.services.osm import GeocodeError, OsmFetchError
from app.services.store import store

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_AUDIO_BYTES = 10 * 1024 * 1024


def _validate_language(language: str) -> str:
    code = language.strip().lower()
    if code not in LANGUAGE_CODES:
        supported = ", ".join(sorted(LANGUAGE_CODES))
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported language '{language}'. Use one of: {supported}.",
        )
    return code


def _map_service_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, GeocodeError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, OsmFetchError):
        return HTTPException(status_code=502, detail=str(exc))
    if isinstance(exc, BhashiniError):
        return HTTPException(status_code=502, detail=str(exc))
    logger.exception("Unhandled discovery error", exc_info=exc)
    return HTTPException(status_code=500, detail="Unexpected discovery failure.")


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        environment=settings.environment,
        bhashini_configured=settings.bhashini_configured,
        nvidia_configured=settings.nvidia_configured,
        demo_data_available=settings.demo_file.exists(),
    )


@router.get("/languages", response_model=list[LanguageOption])
async def languages() -> list[LanguageOption]:
    return LANGUAGES


@router.post("/query", response_model=DiscoveryResponse)
async def query(
    request: QueryRequest,
    service: Annotated[DiscoveryService, Depends(get_discovery_service)],
) -> DiscoveryResponse:
    language = _validate_language(request.language)
    try:
        return await service.discover(
            request.text,
            language,
            demo=request.demo,
            speak=request.speak,
        )
    except Exception as exc:  # noqa: BLE001 - converted to HTTP errors
        raise _map_service_errors(exc) from exc


@router.post("/voice/query", response_model=DiscoveryResponse)
async def voice_query(
    service: Annotated[DiscoveryService, Depends(get_discovery_service)],
    audio: Annotated[UploadFile, File()],
    language: Annotated[str, Form()] = "kn",
    demo: Annotated[bool, Form()] = False,
) -> DiscoveryResponse:
    language = _validate_language(language)
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=422, detail="The audio upload was empty.")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio must be smaller than 10 MB.")
    if not service.settings.bhashini_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Voice input needs Bhashini. Set BHASHINI_USER_ID and BHASHINI_API_KEY, "
                "or use the text box."
            ),
        )

    try:
        transcript, english_text = await service.bhashini.speech_to_english(data, language)
    except BhashiniError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        return await service.discover(
            english_text,
            language,
            english_text=english_text,
            transcript=transcript,
            demo=demo,
            speak=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise _map_service_errors(exc) from exc


@router.get("/water-bodies/mask", response_model=WaterBodyMaskResponse)
async def water_body_mask(water_body_id: str) -> WaterBodyMaskResponse:
    payload = store.mask_payload(water_body_id)
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail="Water body not found in this process. Run a discovery query first.",
        )
    return WaterBodyMaskResponse(**payload)


@router.get("/water-bodies/detail")
async def water_body_detail(water_body_id: str) -> dict:
    record = store.get(water_body_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Water body not found in this process. Run a discovery query first.",
        )
    return record.feature
