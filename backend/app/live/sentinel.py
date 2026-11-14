"""Sentinel-2 L2A ingestion for Phase 3.

Real data comes from the Sentinel Hub Process API: the green (B03), NIR (B08),
and SCL bands are requested over the exact Phase 1 lakebed bbox, at float32
reflectance, then the SCL is used to drop clouds, shadows, snow, and no-data.

Without Sentinel Hub credentials a deterministic synthetic scene is generated so
the NDWI pixel-counting path, the LLM alert, and the UI pulse can be exercised
offline. Synthetic scenes are always labelled as such.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import math
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
import numpy as np
import tifffile

from app.config import Settings
from app.geo.indices import Bounds, cloud_fraction, pixel_area_m2
from app.geo.raster import polygon_pixel_mask
from app.services.osm import WaterBodyRecord

logger = logging.getLogger(__name__)

SENTINEL_SOURCE = "sentinel-hub"
SYNTHETIC_SOURCE = "synthetic-demo"

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B03", "B08", "SCL"] }],
    output: { bands: 3, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [sample.B03, sample.B08, sample.SCL];
}
"""

_TOKEN_MARGIN_S = 60.0
_token_cache: dict[str, tuple[str, float]] = {}
_token_lock = asyncio.Lock()


class SentinelHubError(RuntimeError):
    """Raised when Sentinel Hub cannot return a usable scene."""


@dataclass(frozen=True)
class LiveScene:
    b03: np.ndarray
    b08: np.ndarray
    scl: np.ndarray
    bounds: Bounds
    width: int
    height: int
    observed_at: str
    window_start: str
    window_end: str
    source: str


def scene_size(bounds: Bounds, target: int = 192) -> tuple[int, int]:
    """Keep pixels roughly square at the lake's latitude."""

    west, south, east, north = bounds
    mid_lat = (south + north) / 2
    lon_span = max(1e-9, (east - west) * 111_320 * math.cos(math.radians(mid_lat)))
    lat_span = max(1e-9, (north - south) * 110_540)
    if lon_span >= lat_span:
        width = target
        height = max(32, round(target * lat_span / lon_span))
    else:
        height = target
        width = max(32, round(target * lon_span / lat_span))
    return max(32, min(512, width)), max(32, min(512, height))


async def _access_token(client: httpx.AsyncClient, settings: Settings) -> str:
    client_id = settings.sh_client_id or ""
    cached = _token_cache.get(client_id)
    if cached and cached[1] > time.monotonic():
        return cached[0]

    async with _token_lock:
        cached = _token_cache.get(client_id)
        if cached and cached[1] > time.monotonic():
            return cached[0]

        try:
            response = await client.post(
                settings.sh_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": settings.sh_client_secret or "",
                },
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPStatusError as exc:
            raise SentinelHubError(
                f"Sentinel Hub authentication failed: HTTP {exc.response.status_code} "
                f"{exc.response.text[:200]}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - surfaced as a source error
            raise SentinelHubError(f"Sentinel Hub authentication failed: {exc}") from exc

        token = str(body.get("access_token") or "")
        if not token:
            raise SentinelHubError("Sentinel Hub returned no access token.")
        expires_in = float(body.get("expires_in") or 3600)
        _token_cache[client_id] = (token, time.monotonic() + expires_in - _TOKEN_MARGIN_S)
        return token


async def fetch_sentinel_scene(
    record: WaterBodyRecord,
    settings: Settings,
    *,
    today: date | None = None,
) -> LiveScene:
    """Fetch the least-cloudy recent Sentinel-2 L2A scene over the lakebed."""

    bounds: Bounds = tuple(record.feature["properties"]["bbox"])  # type: ignore[assignment]
    width, height = scene_size(bounds, settings.live_pixel_target)
    end = today or date.today()
    start = end - timedelta(days=settings.live_lookback_days)

    payload: dict[str, Any] = {
        "input": {
            "bounds": {
                "bbox": list(bounds),
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [
                {
                    "type": settings.sh_collection,
                    "dataFilter": {
                        "timeRange": {
                            "from": f"{start.isoformat()}T00:00:00Z",
                            "to": f"{end.isoformat()}T23:59:59Z",
                        },
                        "maxCloudCoverage": settings.live_max_cloud_pct,
                        "mosaickingOrder": "leastCC",
                    },
                }
            ],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
        },
        "evalscript": EVALSCRIPT,
    }

    try:
        async with httpx.AsyncClient(timeout=settings.sh_timeout_s) as client:
            token = await _access_token(client, settings)
            response = await client.post(
                f"{settings.sh_base_url.rstrip('/')}/api/v1/process",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "image/tiff",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if response.status_code >= 400:
                raise SentinelHubError(
                    f"Sentinel Hub process request failed: HTTP {response.status_code} "
                    f"{response.text[:300]}"
                )
            content = response.content
            content_type = response.headers.get("content-type", "")
    except SentinelHubError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as a source error
        raise SentinelHubError(f"Sentinel Hub request failed: {exc}") from exc

    if not content or "tiff" not in content_type.lower():
        raise SentinelHubError(
            "Sentinel Hub returned no GeoTIFF (no cloud-free scene in the window?)."
        )

    try:
        array = tifffile.imread(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        raise SentinelHubError(f"Could not read the Sentinel Hub GeoTIFF: {exc}") from exc

    if array.ndim != 3:
        raise SentinelHubError("Expected a 3-band float32 raster from Sentinel Hub.")
    if array.shape[0] == 3:
        bands = array
    elif array.shape[-1] == 3:
        bands = np.moveaxis(array, -1, 0)
    else:
        raise SentinelHubError(f"Unexpected Sentinel Hub raster shape {array.shape}.")

    green = np.asarray(bands[0], dtype=np.float64)
    nir = np.asarray(bands[1], dtype=np.float64)
    scl = np.asarray(bands[2], dtype=np.float64)

    return LiveScene(
        b03=green,
        b08=nir,
        scl=scl,
        bounds=bounds,
        width=width,
        height=height,
        observed_at=end.isoformat(),
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        source=SENTINEL_SOURCE,
    )


def _seed_for(water_body_id: str, today: date) -> int:
    key = f"{water_body_id}:{today.year}-{today.month:02d}"
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")


def synthetic_scene(
    record: WaterBodyRecord,
    settings: Settings,
    *,
    target_area_m2: float,
    today: date | None = None,
) -> LiveScene:
    """Build a deterministic synthetic reflectance scene for offline demos.

    The scene lands around `target_area_m2` so the live flag reacts to a
    realistic value rather than to the OSM lakebed area.
    """

    today = today or date.today()
    bounds: Bounds = tuple(record.feature["properties"]["bbox"])  # type: ignore[assignment]
    width, height = scene_size(bounds, settings.live_pixel_target)
    rng = np.random.default_rng(_seed_for(record.water_body_id, today))

    inside = polygon_pixel_mask(record.mask_geometry, bounds, width, height)
    cell_area = pixel_area_m2(bounds, width, height)

    cloud_fraction_target = rng.uniform(0.04, 0.15)
    clouds = np.zeros((height, width), dtype=bool)
    rows = rng.choice(
        height, size=max(1, int(round(cloud_fraction_target * height))), replace=False
    )
    clouds[rows, :] = True
    clouds |= rng.random((height, width)) < cloud_fraction_target / 4

    scl = np.full((height, width), 4.0)
    scl[clouds] = 8.0

    usable = inside & ~clouds
    candidates = np.flatnonzero(usable.ravel())
    target_pixels = int(round(max(0.0, target_area_m2) / cell_area))
    water_count = min(target_pixels, candidates.size)
    water = np.zeros((height, width), dtype=bool)
    if water_count > 0:
        scores = rng.random((height, width)).ravel()
        order = candidates[np.argsort(-scores[candidates])][:water_count]
        water.flat[order] = True

    green = 0.05 + 0.03 * rng.random((height, width))
    nir = 0.30 + 0.05 * rng.random((height, width))
    water_pixels = int(water.sum())
    if water_pixels:
        green[water] = 0.16 + 0.04 * rng.random(water_pixels)
        nir[water] = 0.02 + 0.01 * rng.random(water_pixels)

    green[clouds] = np.nan
    nir[clouds] = np.nan

    return LiveScene(
        b03=green,
        b08=nir,
        scl=scl,
        bounds=bounds,
        width=width,
        height=height,
        observed_at=today.isoformat(),
        window_start=(today - timedelta(days=settings.live_lookback_days)).isoformat(),
        window_end=today.isoformat(),
        source=SYNTHETIC_SOURCE,
    )


def scene_cloud_pct(scene: LiveScene) -> float:
    return cloud_fraction(scene.scl)
