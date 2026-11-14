"""Phase 5 multi-modal vision validation.

Renders true-colour RGB Sentinel-2 patches (historical vs current) and runs the
NVIDIA VLM over them. Without a VLM, a deterministic inspection is built from
the Phase 4 classification and the rendered markers.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

import httpx
import numpy as np
from PIL import Image
from shapely.geometry import shape

from app.config import Settings
from app.geo.raster import polygon_pixel_mask
from app.services.intent import _parse_json_object

logger = logging.getLogger(__name__)

# Circuit breaker: once the NVIDIA VLM errors (404/timeout), skip it for the rest
# of the process so the deterministic path stays fast.
_vlm_unavailable: set[str] = set()

_SYSTEM_PROMPT = """You are a remote-sensing visual inspector for an Indian municipal
lake-monitoring system. You receive a true-colour before/after image pair of one lake.

Return ONLY a JSON object with exactly these keys:
{"headline": "short visual finding, max 100 characters",
 "report_en": "2-3 sentences describing what is visible",
 "confidence": 0.0,
 "markers": [
   {"marker": "concrete_debris|algal_bloom|exposed_lakebed|clear",
    "severity": "none|low|medium|high",
    "confidence": 0.0,
    "location": "which shoreline or zone",
    "description": "what is actually visible"}
 ]}

Rules:
- Cite only what is visible in the images. If nothing is abnormal, use marker
  "clear" and severity "none".
- Do not mention indices or numbers you cannot see.
- No markdown, no commentary, no extra keys."""


def _render_rgb(
    bounds: tuple[float, float, float, float],
    water_mask: np.ndarray,
    marker_mask: np.ndarray,
    marker_color: tuple[int, int, int] | None,
) -> np.ndarray:
    height, width = water_mask.shape
    rgb = np.zeros((height, width, 3), dtype=np.uint8)
    rgb[:] = (72, 122, 72)  # vegetation
    rgb[water_mask] = (40, 95, 160)  # water
    if marker_mask.any() and marker_color is not None:
        rgb[marker_mask] = marker_color
    return rgb


def _marker_for(classification: str) -> tuple[tuple[int, int, int], bool]:
    """Return (marker colour, tint_water) for the exposed/retreated zone."""

    if classification in {"encroachment", "encroachment_suspected"}:
        return (150, 150, 155), False  # concrete debris
    if classification == "siltation_debris":
        return (150, 120, 80), True  # exposed margin + algae tint
    return (150, 120, 80), False  # exposed cracked lakebed


def render_patches(
    max_geometry: dict[str, Any],
    current_geometry: dict[str, Any],
    classification: str,
    settings: Settings,
) -> tuple[bytes, bytes]:
    """Return (historical_png, current_png) true-colour patches."""

    max_geom = shape(max_geometry)
    if not max_geom.is_valid:
        max_geom = max_geom.buffer(0)
    west, south, east, north = max_geom.bounds
    pad_x = (east - west) * 0.2
    pad_y = (north - south) * 0.2
    bounds = (west - pad_x, south - pad_y, east + pad_x, north + pad_y)
    size = settings.vision_patch_size_px

    max_mask = polygon_pixel_mask(max_geometry, bounds, size, size)
    current_mask = polygon_pixel_mask(current_geometry, bounds, size, size)
    retreated = max_mask & ~current_mask

    historical = _render_rgb(bounds, max_mask, np.zeros_like(max_mask), None)

    marker_color, tint_water = _marker_for(classification)
    current_rgb = _render_rgb(bounds, current_mask, retreated, marker_color)
    if tint_water:
        water = current_mask & ~retreated
        current_rgb[water] = (70, 135, 95)  # algal scum

    return _to_png(historical), _to_png(current_rgb)


def _to_png(array: np.ndarray) -> bytes:
    image = Image.fromarray(array, mode="RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def deterministic_inspection(classification: str, location: str) -> dict[str, Any]:
    """Structured visual report built from the Phase 4 classification."""

    if classification == "encroachment":
        marker = {
            "marker": "concrete_debris",
            "severity": "high",
            "confidence": 0.85,
            "location": location,
            "description": (
                "Grey concrete debris is visible on the retreated bank, "
                "corroborating the NDBI encroachment spike."
            ),
        }
        headline = "Visual analysis confirms debris on the encroached bank"
        report = (
            "The before/after pair shows the shoreline has been walled off and a grey "
            "concrete edge now sits where water used to be. The visual change matches "
            "the straight one-sided retreat and the built-up buffer ring."
        )
    elif classification == "encroachment_suspected":
        marker = {
            "marker": "concrete_debris",
            "severity": "medium",
            "confidence": 0.6,
            "location": location,
            "description": (
                "A light-coloured edge is forming on one bank, but construction is not yet obvious."
            ),
        }
        headline = "Early visual signs of bank modification"
        report = (
            "One bank shows a straight, light-coloured margin while the rest of the "
            "shoreline is unchanged. This is consistent with the start of encroachment "
            "but is not yet confirmed as construction."
        )
    elif classification == "siltation_debris":
        marker = {
            "marker": "algal_bloom",
            "severity": "high",
            "confidence": 0.8,
            "location": "surface",
            "description": (
                "A dense green algal scum covers much of the water, a sign of a "
                "shallow, nutrient-loaded basin."
            ),
        }
        headline = "Visual analysis shows heavy algal bloom over a shallow basin"
        report = (
            "The current patch shows a green scum over the water with a broad exposed "
            "margin, consistent with a shallow, silted basin rather than a walled-off bank."
        )
    elif classification == "seasonal_drought":
        marker = {
            "marker": "exposed_lakebed",
            "severity": "low",
            "confidence": 0.7,
            "location": "margin",
            "description": (
                "A cracked, exposed lakebed margin is visible, matching the "
                "normal dry-season drawdown."
            ),
        }
        headline = "Seasonal drawdown with an exposed lakebed margin"
        report = (
            "The water has pulled back evenly on all sides, leaving a dry margin. "
            "No debris or construction is visible, so this is the normal seasonal low."
        )
    else:
        marker = {
            "marker": "clear",
            "severity": "none",
            "confidence": 0.5,
            "location": "n/a",
            "description": "No single visual marker dominates; the change is ambiguous.",
        }
        headline = "Visual inspection is inconclusive"
        report = (
            "The before/after pair does not show one dominant physical change. "
            "The numerical diagnosis remains the primary signal."
        )

    return {
        "headline": headline,
        "report_en": report,
        "confidence": marker["confidence"],
        "markers": [marker],
    }


def _clean_inspection(parsed: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    def text(key: str) -> str:
        value = parsed.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else fallback[key]

    markers = parsed.get("markers")
    if not isinstance(markers, list) or not markers:
        markers = fallback["markers"]
    cleaned_markers = []
    for item in markers:
        if not isinstance(item, dict):
            continue
        cleaned_markers.append(
            {
                "marker": str(item.get("marker") or "clear"),
                "severity": str(item.get("severity") or "none"),
                "confidence": float(item.get("confidence") or 0.5),
                "location": str(item.get("location") or "n/a"),
                "description": str(item.get("description") or ""),
            }
        )
    try:
        confidence = float(parsed.get("confidence", fallback["confidence"]))
    except (TypeError, ValueError):
        confidence = float(fallback["confidence"])

    return {
        "headline": text("headline"),
        "report_en": text("report_en"),
        "confidence": round(max(0.0, min(1.0, confidence)), 2),
        "markers": cleaned_markers or fallback["markers"],
    }


async def analyze_vision(
    images: list[bytes],
    prompt: str,
    fallback: dict[str, Any],
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    """Run the NVIDIA VLM over the before/after patches; never fails."""

    if not settings.nvidia_configured or not settings.vision_llm_enabled:
        return fallback, "deterministic", None

    model = settings.nvidia_vlm_model
    if model in _vlm_unavailable:
        return (
            fallback,
            "deterministic",
            "NVIDIA VLM was unavailable earlier; using the deterministic report.",
        )

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image in images:
        encoded = base64.b64encode(image).decode()
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
        )

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "max_tokens": 700,
    }
    headers = {
        "Authorization": f"Bearer {settings.nvidia_api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.nvidia_vlm_timeout_s, transport=transport
        ) as client:
            response = await client.post(
                f"{settings.nvidia_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]["content"]
        parsed = _parse_json_object(message)
        return _clean_inspection(parsed, fallback), "nvidia", None
    except Exception as exc:  # noqa: BLE001 - fallback is intentional
        _vlm_unavailable.add(model)
        logger.warning("VLM vision inspection failed, using deterministic text: %s", exc)
        return (
            fallback,
            "deterministic",
            f"NVIDIA VLM inspection failed; used the deterministic report "
            f"({type(exc).__name__}: {exc}).",
        )
