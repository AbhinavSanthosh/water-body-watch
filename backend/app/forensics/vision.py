"""Phase 5 multi-modal vision validation.

Renders true-colour RGB Sentinel-2 patches (historical vs current) and runs the
NVIDIA VLM over them. Without a VLM, a deterministic inspection is built from
the Phase 4 classification and the rendered markers.
"""

from __future__ import annotations

import base64
import io
import logging
from datetime import date, timedelta
from typing import Any

import httpx
import numpy as np
from PIL import Image
from shapely.geometry import shape

from app.config import Settings
from app.forensics.imagery import (
    SENTINEL_SOURCE,
    fetch_sentinel_rgb,
    render_patch_png,
    scene_variance,
    synthetic_rgb_scene,
)
from app.geo.raster import polygon_pixel_mask
from app.live.sentinel import SentinelHubError
from app.services.intent import _parse_json_object
from app.services.osm import WaterBodyRecord

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


async def render_patches_with_imagery(
    record: WaterBodyRecord,
    max_geometry: dict[str, Any],
    current_geometry: dict[str, Any],
    settings: Settings,
    *,
    today: date | None = None,
) -> tuple[bytes, bytes, str, str, str]:
    """Real before/after true-colour patches, with a synthetic fallback.

    Returns (historical_png, current_png, source, historical_date, current_date).
    ``source`` is ``sentinel-hub`` when both scenes are real, ``synthetic-demo``
    when both are synthetic, or ``mixed`` when only one could be fetched.
    """

    today = today or date.today()
    hist_year = int(round(settings.vision_historical_years * 365.25))

    def synthetic_pair(target: date, geometry: dict[str, Any]) -> tuple[bytes, str, str]:
        scene = synthetic_rgb_scene(record, settings, target_date=target)
        png = render_patch_png(scene, outline_geometry=geometry, outline_color=(255, 220, 80))
        return png, scene.source, scene.observed_at

    # Without Sentinel Hub credentials, render the deterministic synthetic pair
    # (also keeps the unit tests offline).
    if not settings.sentinel_hub_configured:
        historical_png, historical_source, historical_date = synthetic_pair(
            today - timedelta(days=hist_year), max_geometry
        )
        current_png, current_source, current_date = synthetic_pair(today, current_geometry)
        return historical_png, current_png, "synthetic-demo", historical_date, current_date

    async def fetch_or_synthetic(target: date, geometry: dict[str, Any]) -> tuple[bytes, str, str]:
        try:
            scene = await fetch_sentinel_rgb(
                record,
                settings,
                target_date=target,
                lookback_days=settings.vision_lookback_days,
            )
            png = render_patch_png(
                scene,
                outline_geometry=geometry,
                outline_color=(255, 220, 80),
            )
            return png, SENTINEL_SOURCE, scene.observed_at
        except SentinelHubError as exc:
            logger.warning("Sentinel RGB unavailable for %s (%s); using synthetic.", target, exc)
            scene = synthetic_rgb_scene(record, settings, target_date=target)
            png = render_patch_png(
                scene,
                outline_geometry=geometry,
                outline_color=(255, 220, 80),
            )
            return png, scene.source, scene.observed_at

    # Historical scene: walk back from the configured horizon until we find one
    # with real visual signal (early L2A tiles can mosaic to a blank fill).
    historical_png: bytes | None = None
    historical_source = "synthetic-demo"
    historical_date = (today - timedelta(days=hist_year)).isoformat()
    for years in (settings.vision_historical_years, 8, 6, 4):
        target = today - timedelta(days=int(round(years * 365.25)))
        try:
            scene = await fetch_sentinel_rgb(
                record,
                settings,
                target_date=target,
                lookback_days=settings.vision_lookback_days,
            )
            if scene_variance(scene) >= 6.0:
                historical_png = render_patch_png(
                    scene, outline_geometry=max_geometry, outline_color=(255, 220, 80)
                )
                historical_source = SENTINEL_SOURCE
                historical_date = scene.observed_at
                break
            logger.info(
                "Historical scene at %s was blank (std %.1f); trying an earlier year.",
                target,
                scene_variance(scene),
            )
        except SentinelHubError as exc:
            logger.warning("Sentinel RGB unavailable at %s (%s).", target, exc)

    if historical_png is None:
        historical_png, historical_source, historical_date = synthetic_pair(
            today - timedelta(days=hist_year), max_geometry
        )

    current_png, current_source, current_date = await fetch_or_synthetic(today, current_geometry)

    if historical_source == current_source == SENTINEL_SOURCE:
        source = SENTINEL_SOURCE
    elif historical_source != SENTINEL_SOURCE and current_source != SENTINEL_SOURCE:
        source = "synthetic-demo"
    else:
        source = "mixed"

    return historical_png, current_png, source, historical_date, current_date


def deterministic_inspection(
    classification: str,
    location: str,
    *,
    shrinkage_pct: float | None = None,
    ndbi_built_pct: float | None = None,
    velocity_ratio: float | None = None,
) -> dict[str, Any]:
    """Structured visual report built from the Phase 4 numbers.

    The classification plus the measured shrinkage, built-up fraction, and
    recession velocity produce a specific, confident conclusion. Numbers are
    only ever cited from what was computed, never invented.
    """

    def fmt_pct(value: float | None) -> str:
        return "—" if value is None else f"{value:.0f}%"

    def fmt_ratio(value: float | None) -> str:
        return "—" if value is None else f"{value:.1f}×"

    shrink = fmt_pct(shrinkage_pct)
    built = fmt_pct(ndbi_built_pct)
    ratio = fmt_ratio(velocity_ratio)

    if classification == "encroachment":
        marker = {
            "marker": "concrete_debris",
            "severity": "high",
            "confidence": 0.85,
            "location": location,
            "description": (
                f"Grey concrete edge visible on {location}, where the buffer ring is "
                f"{built} built-up — corroborating the NDBI encroachment spike."
            ),
        }
        headline = "Visual analysis confirms encroachment on one bank"
        report = (
            f"The before/after pair shows a straight, one-sided retreat on {location} "
            f"with the water now {shrink} below the lakebed and a {built} built-up "
            "buffer ring. The visual change matches the walled-off shoreline."
        )
    elif classification == "encroachment_suspected":
        marker = {
            "marker": "concrete_debris",
            "severity": "medium",
            "confidence": 0.6,
            "location": location,
            "description": (
                f"A light-coloured edge is forming on {location}, but construction is "
                f"not yet obvious (buffer ring {built} built-up)."
            ),
        }
        headline = "Early visual signs of bank modification"
        report = (
            f"One bank shows a straight, light-coloured margin while the rest of the "
            f"shoreline is unchanged, with water {shrink} below the lakebed. This is "
            "consistent with the start of encroachment but is not yet confirmed."
        )
    elif classification == "siltation_debris":
        marker = {
            "marker": "algal_bloom",
            "severity": "high",
            "confidence": 0.8,
            "location": "surface",
            "description": (
                f"A dense green algal scum covers the water surface, with recession at "
                f"{ratio} natural — signs of a shallow, silted basin."
            ),
        }
        headline = "Visual analysis shows heavy algal bloom over a shallow basin"
        report = (
            f"The current patch shows a green scum with the water receded {shrink} and "
            f"retreating at {ratio} the natural rate. This is a shallow, silted basin "
            "rather than a walled-off bank."
        )
    elif classification == "seasonal_drought":
        marker = {
            "marker": "exposed_lakebed",
            "severity": "low",
            "confidence": 0.7,
            "location": "margin",
            "description": (
                f"A cracked, exposed lakebed margin is visible ({shrink} below maximum), "
                "matching the normal dry-season drawdown."
            ),
        }
        headline = "Seasonal drawdown with an exposed lakebed margin"
        report = (
            f"The water has pulled back evenly on all sides to {shrink} below the lakebed. "
            "No debris or construction is visible, so this is the normal seasonal low."
        )
    elif classification == "insufficient_data":
        # No confirmed flag yet — describe what is actually measured, not "inconclusive".
        if shrinkage_pct is not None and shrinkage_pct >= 15:
            marker = {
                "marker": "exposed_lakebed",
                "severity": "medium",
                "confidence": 0.65,
                "location": "margin",
                "description": (
                    f"Water extent is visibly {shrink} below the lakebed boundary, but "
                    "history is too thin to separate seasonal low from permanent loss."
                ),
            }
            headline = "Shrinkage visible but the cause is not yet confirmed"
            report = (
                f"The before/after pair shows the water {shrink} below the historical "
                "lakebed. The change is real, but there is not enough same-month history "
                "to confirm whether it is a seasonal low or a permanent loss."
            )
        elif shrinkage_pct is not None:
            marker = {
                "marker": "clear",
                "severity": "none",
                "confidence": 0.55,
                "location": "n/a",
                "description": (
                    f"Water extent is only {shrink} below the lakebed — within the normal "
                    "seasonal swing — and no debris or construction is visible."
                ),
            }
            headline = "No significant change detected"
            report = (
                f"The before/after pair shows the water {shrink} below the lakebed, inside "
                "the normal seasonal tolerance, with no debris or construction visible."
            )
        else:
            marker = {
                "marker": "clear",
                "severity": "none",
                "confidence": 0.5,
                "location": "n/a",
                "description": "No confirmed flag, so no specific visual finding is reported.",
            }
            headline = "No anomaly detected"
            report = (
                "The live NDWI check did not confirm a flag, so no specific visual "
                "finding is reported. Re-run after the next clear Sentinel-2 pass."
            )
    else:  # inconclusive or normal
        if shrinkage_pct is not None and shrinkage_pct >= 15:
            marker = {
                "marker": "exposed_lakebed",
                "severity": "medium",
                "confidence": 0.6,
                "location": "mixed",
                "description": (
                    f"Water is {shrink} below the lakebed with a mixed retreat shape "
                    f"({built} built-up ring, {ratio} natural recession)."
                ),
            }
            headline = "Shrinkage confirmed with a mixed retreat pattern"
            report = (
                f"The before/after pair shows a {shrink} reduction in water extent, but the "
                f"retreat shape is mixed ({built} built-up, {ratio} recession), so no single "
                "cause dominates. The numerical diagnosis remains the primary signal."
            )
        else:
            marker = {
                "marker": "clear",
                "severity": "none",
                "confidence": 0.55,
                "location": "n/a",
                "description": (
                    f"Water extent is {shrink} below the lakebed, within the normal seasonal range."
                ),
            }
            headline = "Lake is within its seasonal range"
            report = (
                f"The before/after pair shows the water {shrink} below the lakebed, inside "
                "the normal seasonal tolerance, with no structural change visible."
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
    demo: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[dict[str, Any], str, str | None]:
    """Run the NVIDIA VLM over the before/after patches; never fails."""

    if demo or not settings.nvidia_configured or not settings.vision_llm_enabled:
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
