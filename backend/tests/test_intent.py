import pytest

from app.config import Settings
from app.services.intent import (
    IntentExtractionError,
    _parse_json_object,
    extract_intent,
    fallback_extract,
)


def test_fallback_extracts_place_and_target() -> None:
    intent = fallback_extract("Map the lakes near Mysore Road, Bangalore")
    assert intent.location == "Mysore Road, Bangalore"
    assert intent.target_entity == "lakes"


def test_fallback_extracts_ponds_around_landmark() -> None:
    intent = fallback_extract("show ponds around Ulsoor Lake")
    assert intent.location == "Ulsoor Lake"
    assert intent.target_entity == "ponds"


def test_fallback_accepts_bare_place() -> None:
    intent = fallback_extract("Mysore Road")
    assert intent.location == "Mysore Road"
    assert intent.target_entity == "water_bodies"


def test_fallback_rejects_missing_place() -> None:
    with pytest.raises(IntentExtractionError):
        fallback_extract("please map water bodies")


def test_parse_json_object_strips_markdown_fence() -> None:
    parsed = _parse_json_object(
        '```json\n{"target_entity": "lakes", "location": "Ulsoor Lake"}\n```'
    )
    assert parsed["location"] == "Ulsoor Lake"


async def test_extract_intent_uses_fallback_without_key(settings: Settings) -> None:
    intent, source, warning = await extract_intent(
        "Map the lakes near Mysore Road, Bangalore", settings
    )
    assert intent.location == "Mysore Road, Bangalore"
    assert source == "fallback"
    assert warning is not None
