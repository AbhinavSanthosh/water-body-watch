"""Turn a natural-language discovery request into a structured query.

The LLM is used only to parse place names and intent. Coordinates always come
from Nominatim/OSM later, never from the model.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app.config import Settings
from app.models import Intent

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a geographic intent parser for a municipal water-body monitoring app.
Return ONLY a JSON object with this exact shape:
{"target_entity": "water_bodies", "location": "place name",
 "action": "map_and_monitor", "radius_km": null}

Rules:
- target_entity must be one of: water_bodies, lakes, ponds, reservoirs.
- location must be a real, geocodable place string. Keep the user's place names and
  add the city/state/country when the user provided them. Never invent a location.
- action is normally "map_and_monitor".
- radius_km is a number only when the user asks for a distance, otherwise null.
- If no location is present, return {"location": ""}.
Do not include markdown, commentary, or extra keys."""


class IntentExtractionError(ValueError):
    """Raised when no usable location can be extracted from the prompt."""


def _clean_location(value: str) -> str:
    location = value.strip().strip(" .,;:!?\"'")
    location = re.sub(r"\s+", " ", location)
    location = re.sub(
        r"\s+(?:and|to|for|with)\s+(?:map|monitor|track|show|display)\b.*$",
        "",
        location,
        flags=re.IGNORECASE,
    )
    location = re.sub(
        r"\s+(?:map|monitor|track|show|display)\s+(?:them|it|lakes?|ponds?|water bodies?)\b.*$",
        "",
        location,
        flags=re.IGNORECASE,
    )
    return location.strip(" .,;:!?\"'")


def fallback_extract(text: str) -> Intent:
    """Small deterministic parser used when NVIDIA is unavailable or returns junk."""

    stripped = text.strip()
    location = ""
    for pattern in (
        r"\b(?:near|around|in|at|within|for)\s+(.+)$",
        r"\b(?:map|show|find|display|monitor|track)\s+(?:the\s+)?(?:lakes?|ponds?|water\s*bodies?|reservoirs?)\s+(.+)$",
    ):
        match = re.search(pattern, stripped, flags=re.IGNORECASE)
        if match:
            location = _clean_location(match.group(1))
            break

    if not location:
        cleaned = re.sub(
            r"^(?:please\s+)?(?:map|show|find|display|monitor|track)\s+",
            "",
            stripped,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\b(?:the\s+)?(?:lakes?|ponds?|water\s*bodies?|reservoirs?)\b",
            "",
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"^(?:near|around|in|at|within|for)\s+", "", cleaned, flags=re.IGNORECASE)
        location = _clean_location(cleaned)

    if not location:
        raise IntentExtractionError(
            "I could not find a place in that request. "
            "Try: 'Map the lakes near Mysore Road, Bangalore'."
        )

    lower = stripped.lower()
    target = "water_bodies"
    if "pond" in lower:
        target = "ponds"
    elif "reservoir" in lower:
        target = "reservoirs"
    elif "lake" in lower:
        target = "lakes"

    return Intent(target_entity=target, location=location, action="map_and_monitor")


def _parse_json_object(content: str) -> dict[str, Any]:
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        content = re.sub(r"\s*```$", "", content)
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("model response did not contain a JSON object")
    parsed = json.loads(content[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model response JSON was not an object")
    return parsed


async def _nvidia_extract(
    text: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Intent:
    url = f"{settings.nvidia_base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": settings.nvidia_llm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.nvidia_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=settings.nvidia_timeout_s, transport=transport) as client:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code == 400:
            # Some NIM-hosted models do not implement response_format.
            payload.pop("response_format", None)
            response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        body = response.json()

    content = body["choices"][0]["message"]["content"]
    parsed = _parse_json_object(content)
    location = _clean_location(str(parsed.get("location") or ""))
    if not location:
        raise IntentExtractionError("NVIDIA returned no location")

    target = str(parsed.get("target_entity") or "water_bodies").lower()
    if target not in {"water_bodies", "lakes", "ponds", "reservoirs"}:
        target = "water_bodies"

    radius = parsed.get("radius_km")
    try:
        radius_value = float(radius) if radius is not None else None
    except (TypeError, ValueError):
        radius_value = None

    return Intent(
        target_entity=target,  # type: ignore[arg-type]
        location=location,
        action=str(parsed.get("action") or "map_and_monitor"),
        radius_km=radius_value,
    )


async def extract_intent(
    text: str,
    settings: Settings,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[Intent, str, str | None]:
    """Return (intent, source, warning). Never fails just because NVIDIA is down."""

    if settings.nvidia_configured:
        try:
            return await _nvidia_extract(text, settings, transport), "nvidia", None
        except Exception as exc:  # noqa: BLE001 - fallback is intentional
            logger.warning("NVIDIA intent extraction failed, using fallback: %s", exc)
            warning = f"NVIDIA intent parsing failed; used the local parser ({exc})."
            return fallback_extract(text), "fallback", warning

    return fallback_extract(text), "fallback", "NVIDIA_API_KEY is not set; used the local parser."
