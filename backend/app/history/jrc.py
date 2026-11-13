"""Monthly surface-area series from JRC Global Surface Water.

The primary source is Earth Engine's ``JRC/GSW1_4/MonthlyHistory`` collection,
reduced over the exact OSM lakebed mask produced in Phase 1. That collection is
30 m Landsat-derived, so areas are computed on the same grid and never mixed
with 10 m Sentinel-2 pixel counts.

When Earth Engine is not configured, or the query fails, a deterministic
synthetic series keeps the chart, LLM narration, and Bhashini briefing testable
offline. Synthetic data is always labelled as such; it is never presented as
JRC.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
from datetime import date
from pathlib import Path
from typing import Any

from app.config import Settings
from app.history.baseline import Observation
from app.services.osm import WaterBodyRecord

logger = logging.getLogger(__name__)

JRC_SOURCE = "jrc-gsw"
SYNTHETIC_SOURCE = "synthetic-demo"


class HistorySourceError(RuntimeError):
    """Raised when a real historical source cannot be queried."""


def last_complete_month(today: date | None = None) -> date:
    """First day of the most recent month that has finished."""

    today = today or date.today()
    if today.month == 1:
        return date(today.year - 1, 12, 1)
    return date(today.year, today.month - 1, 1)


def shift_month(value: date, months: int) -> date:
    index = value.year * 12 + (value.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def _date_range(end: date, years: int) -> tuple[date, date]:
    start = shift_month(end, -(years * 12 - 1))
    return start, end


def _initialize_ee(settings: Settings) -> Any:
    try:
        import ee  # noqa: PLC0415 - optional dependency, imported lazily
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise HistorySourceError(
            "Earth Engine is not installed. Run `uv sync --extra gee` or set "
            "HISTORY_ALLOW_SYNTHETIC=true to use the offline demo series."
        ) from exc

    if not settings.gee_configured:
        raise HistorySourceError(
            "Google Earth Engine is not configured. Set GEE_SERVICE_ACCOUNT_JSON and GEE_PROJECT."
        )

    raw = (settings.gee_service_account_json or "").strip()
    try:
        if raw.startswith("{"):
            info = json.loads(raw)
            key_data = raw
        else:
            path = Path(raw).expanduser()
            key_data = path.read_text(encoding="utf-8")
            info = json.loads(key_data)
        credentials = ee.ServiceAccountCredentials(info["client_email"], key_data=key_data)
        ee.Initialize(credentials, project=settings.gee_project)
    except Exception as exc:  # noqa: BLE001 - surfaced as a source error
        raise HistorySourceError(f"Earth Engine authentication failed: {exc}") from exc

    return ee


def _fetch_jrc_sync(
    mask_geometry: dict[str, Any],
    settings: Settings,
    start: date,
    end: date,
) -> list[Observation]:
    ee = _initialize_ee(settings)
    geometry = ee.Geometry(mask_geometry)
    pixel_area = ee.Image.pixelArea()
    collection = (
        ee.ImageCollection(settings.jrc_collection)
        .filterDate(start.isoformat(), shift_month(end, 1).isoformat())
        .filterBounds(geometry)
    )

    def reduce_month(image: Any) -> Any:
        water = image.select("water").eq(settings.jrc_water_value)
        area = pixel_area.updateMask(water).reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=geometry,
            scale=settings.jrc_scale_m,
            maxPixels=settings.jrc_max_pixels,
            bestEffort=True,
        )
        return ee.Feature(
            None,
            {
                "date": image.date().format("YYYY-MM-dd"),
                "area_m2": ee.Number(area.get("area")).max(0),
            },
        )

    try:
        payload = collection.map(reduce_month).getInfo()
    except Exception as exc:  # noqa: BLE001 - Earth Engine raises many types
        raise HistorySourceError(f"JRC query failed: {exc}") from exc

    observations: list[Observation] = []
    for feature in (payload or {}).get("features", []):
        properties = feature.get("properties") or {}
        raw_date = properties.get("date")
        area = properties.get("area_m2")
        if not raw_date:
            continue
        try:
            day = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            continue
        observations.append(
            Observation(date=day.replace(day=1), area_m2=float(area) if area is not None else None)
        )
    return sorted(observations, key=lambda item: item.date)


async def fetch_jrc_series(
    record: WaterBodyRecord,
    settings: Settings,
    *,
    end: date | None = None,
) -> list[Observation]:
    """Query JRC monthly water area over the Phase 1 lakebed mask."""

    end = end or last_complete_month()
    start, finish = _date_range(end, settings.history_years)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_fetch_jrc_sync, record.mask_geometry, settings, start, finish),
            timeout=settings.jrc_timeout_s,
        )
    except TimeoutError as exc:
        raise HistorySourceError(
            f"JRC query timed out after {settings.jrc_timeout_s:.0f}s."
        ) from exc


def _seed_for(water_body_id: str) -> int:
    return int.from_bytes(hashlib.sha256(water_body_id.encode()).digest()[:8], "big")


def synthetic_series(
    record: WaterBodyRecord,
    settings: Settings,
    *,
    end: date | None = None,
) -> list[Observation]:
    """Deterministic stand-in series for offline demos.

    Shape: a post-monsoon peak, a late-dry-season trough, a slow multi-year
    trend, and a little observation noise. Seeded by the stable water-body ID so
    the same lake always draws the same chart.
    """

    end = end or last_complete_month()
    start, _ = _date_range(end, settings.history_years)
    base_area = float(record.feature["properties"].get("area_m2") or 10_000.0)
    rng = random.Random(_seed_for(record.water_body_id))

    amplitude = rng.uniform(0.22, 0.42)
    trend_pct_per_year = rng.uniform(-3.5, 0.4)
    noise = rng.uniform(0.02, 0.05)
    peak_month = rng.choice((9, 10, 11))

    total_months = settings.history_years * 12
    observations: list[Observation] = []
    for index in range(total_months):
        day = shift_month(start, index)
        years_elapsed = index / 12
        seasonal = 1 - amplitude * 0.5 * (1 - math.cos(2 * math.pi * (day.month - peak_month) / 12))
        trend = 1 + (trend_pct_per_year / 100) * years_elapsed
        jitter = 1 + noise * rng.gauss(0, 1)
        area = max(500.0, base_area * seasonal * trend * jitter)

        # Simulate a few missing passes, but keep the latest months observed so
        # the flag always has a current sample.
        missing = index < total_months - 2 and rng.random() < 0.05
        observations.append(Observation(date=day, area_m2=None if missing else area))

    return observations
