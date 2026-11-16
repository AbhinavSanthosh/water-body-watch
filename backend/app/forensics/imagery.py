"""Real Sentinel-2 true-colour imagery for Phase 5 visual validation.

Fetches Sentinel-2 L2A blue/green/red (B02/B03/B04) plus the SCL cloud mask over
the exact lakebed bbox, pre-processes it into a true-colour PNG, and overlays the
lake outline so the before/after pair is grounded in real satellite pixels.

When Sentinel Hub is unavailable a deterministic synthetic scene is returned so
the offline demo still works; synthetic imagery is always labelled as such.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
import numpy as np
import tifffile
from PIL import Image, ImageDraw
from shapely.geometry import shape

from app.config import Settings
from app.geo.indices import Bounds, valid_pixel_mask
from app.live.sentinel import SentinelHubError, _access_token
from app.services.osm import WaterBodyRecord

logger = logging.getLogger(__name__)

SENTINEL_SOURCE = "sentinel-hub"
SYNTHETIC_SOURCE = "synthetic-demo"

RGB_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02", "B03", "B04", "SCL"] }],
    output: { bands: 4, sampleType: "FLOAT32" }
  };
}
function evaluatePixel(sample) {
  return [sample.B02, sample.B03, sample.B04, sample.SCL];
}
"""


@dataclass(frozen=True)
class RGBScene:
    """A Sentinel-2 L2A true-colour scene over one lake, ready to render."""

    blue: np.ndarray  # B02
    green: np.ndarray  # B03
    red: np.ndarray  # B04
    scl: np.ndarray
    bounds: Bounds
    width: int
    height: int
    observed_at: str
    window_start: str
    window_end: str
    source: str


def _padded_bounds(bbox: list[float], pad_frac: float = 0.3) -> Bounds:
    west, south, east, north = bbox
    pad_x = (east - west) * pad_frac
    pad_y = (north - south) * pad_frac
    return (west - pad_x, south - pad_y, east + pad_x, north + pad_y)


async def fetch_sentinel_rgb(
    record: WaterBodyRecord,
    settings: Settings,
    *,
    target_date: date,
    lookback_days: int,
) -> RGBScene:
    """Fetch the least-cloudy true-colour scene near `target_date`."""

    bbox: list[float] = record.feature["properties"]["bbox"]
    bounds = _padded_bounds(bbox)
    size = settings.vision_patch_size_px
    end = target_date
    start = end - timedelta(days=lookback_days)

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
            "width": size,
            "height": size,
            "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}],
        },
        "evalscript": RGB_EVALSCRIPT,
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
                    f"Sentinel Hub RGB request failed: HTTP {response.status_code} "
                    f"{response.text[:300]}"
                )
            content = response.content
            content_type = response.headers.get("content-type", "")
    except SentinelHubError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as a source error
        raise SentinelHubError(f"Sentinel Hub RGB request failed: {exc}") from exc

    if not content or "tiff" not in content_type.lower():
        raise SentinelHubError("Sentinel Hub returned no GeoTIFF for the RGB scene.")

    try:
        array = tifffile.imread(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        raise SentinelHubError(f"Could not read the RGB GeoTIFF: {exc}") from exc

    if array.ndim != 3:
        raise SentinelHubError(f"Unexpected RGB raster shape {array.shape}.")
    if array.shape[0] == 4:
        bands = array
    elif array.shape[-1] == 4:
        bands = np.moveaxis(array, -1, 0)
    else:
        raise SentinelHubError(f"Unexpected RGB raster shape {array.shape}.")

    blue = np.asarray(bands[0], dtype=np.float64)
    green = np.asarray(bands[1], dtype=np.float64)
    red = np.asarray(bands[2], dtype=np.float64)
    scl = np.asarray(bands[3], dtype=np.float64)

    return RGBScene(
        blue=blue,
        green=green,
        red=red,
        scl=scl,
        bounds=bounds,
        width=size,
        height=size,
        observed_at=end.isoformat(),
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        source=SENTINEL_SOURCE,
    )


def render_true_color(
    scene: RGBScene, *, percentile: float = 98.0, gamma: float = 0.9
) -> np.ndarray:
    """Cloud-masked true-colour rendering as a uint8 HxWx3 array."""

    valid = valid_pixel_mask(scene.scl, scene.red.shape)

    def stretch(band: np.ndarray) -> np.ndarray:
        values = np.asarray(band, dtype=np.float64)
        usable = values[valid]
        if usable.size == 0:
            return np.zeros(values.shape, dtype=np.float64)
        hi = float(np.percentile(usable, percentile))
        lo = 0.0
        norm = np.clip((values - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
        return np.power(norm, gamma)

    rgb = np.stack([stretch(scene.red), stretch(scene.green), stretch(scene.blue)], axis=-1)
    rgb = np.nan_to_num(rgb, nan=0.0, posinf=0.0, neginf=0.0)
    rgb = (np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)
    rgb[~valid] = (18, 18, 18)
    return rgb


def _draw_outline(
    rgb: np.ndarray,
    geometry: dict[str, Any],
    bounds: Bounds,
    size: int,
    color: tuple[int, int, int],
) -> np.ndarray:
    """Draw a polygon outline onto an RGB array (row 0 = north)."""

    west, south, east, north = bounds
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    poly = shape(geometry)
    if poly.is_empty:
        return rgb
    polygons = [poly] if poly.geom_type == "Polygon" else list(poly.geoms)

    def to_px(ring: Any) -> list[tuple[float, float]]:
        return [
            (
                (x - west) / (east - west) * size,
                (north - y) / (north - south) * size,
            )
            for x, y in ring
        ]

    for item in polygons:
        outer = to_px(item.exterior.coords)
        draw.line(outer + [outer[0]], fill=color, width=2)
        for interior in item.interiors:
            inner = to_px(interior.coords)
            draw.line(inner + [inner[0]], fill=color, width=2)
    return np.asarray(image)


def render_patch_png(
    scene: RGBScene,
    *,
    outline_geometry: dict[str, Any] | None,
    outline_color: tuple[int, int, int],
) -> bytes:
    """Render a scene to PNG, optionally drawing the lake outline."""

    rgb = render_true_color(scene)
    if outline_geometry is not None:
        rgb = _draw_outline(rgb, outline_geometry, scene.bounds, scene.width, outline_color)
    buffer = io.BytesIO()
    Image.fromarray(rgb).save(buffer, format="PNG")
    return buffer.getvalue()


def scene_variance(scene: RGBScene) -> float:
    """Standard deviation of the rendered scene, used to reject blank scenes.

    Early Sentinel-2 L2A tiles (2016) sometimes mosaic to a near-uniform fill;
    a low variance means the scene carries no visual signal and should be retried
    at a more recent year.
    """

    rgb = render_true_color(scene)
    return float(rgb.std())


def synthetic_rgb_scene(
    record: WaterBodyRecord,
    settings: Settings,
    *,
    target_date: date,
) -> RGBScene:
    """Deterministic synthetic scene used when Sentinel Hub is unavailable."""

    import hashlib

    from app.geo.raster import polygon_pixel_mask

    bbox: list[float] = record.feature["properties"]["bbox"]
    bounds = _padded_bounds(bbox)
    size = settings.vision_patch_size_px
    seed = int.from_bytes(
        hashlib.sha256(f"{record.water_body_id}:{target_date.isoformat()}".encode()).digest()[:8],
        "big",
    )
    rng = np.random.default_rng(seed)

    inside = polygon_pixel_mask(record.mask_geometry, bounds, size, size)

    # Synthetic earth tones: vegetation, bare soil, and urban patches.
    red = rng.uniform(0.10, 0.22, (size, size))
    green = rng.uniform(0.12, 0.24, (size, size))
    blue = rng.uniform(0.06, 0.14, (size, size))

    urban = rng.random((size, size)) < 0.15
    red[urban] = rng.uniform(0.24, 0.34, urban.sum())
    green[urban] = rng.uniform(0.22, 0.30, urban.sum())
    blue[urban] = rng.uniform(0.20, 0.28, urban.sum())

    water = inside
    red[water] = 0.03
    green[water] = 0.10
    blue[water] = 0.16

    # Thin cloud strips.
    scl = np.full((size, size), 4.0)
    cloud_rows = rng.choice(size, size=max(1, size // 12), replace=False)
    scl[cloud_rows, :] = 8.0
    red[cloud_rows, :] = np.nan
    green[cloud_rows, :] = np.nan
    blue[cloud_rows, :] = np.nan

    return RGBScene(
        blue=blue,
        green=green,
        red=red,
        scl=scl,
        bounds=bounds,
        width=size,
        height=size,
        observed_at=target_date.isoformat(),
        window_start=(target_date - timedelta(days=settings.vision_lookback_days)).isoformat(),
        window_end=target_date.isoformat(),
        source=SYNTHETIC_SOURCE,
    )
