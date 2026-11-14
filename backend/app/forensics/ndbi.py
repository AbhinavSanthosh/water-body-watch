"""100 m buffer-ring urbanization tracking for Phase 4.

GeoPandas/shapely builds a 100 m ring around the lakebed polygon, then NDBI
`(B11 - B08) / (B11 + B08)` is evaluated over that ring. A spike in built-up
pixels (positive NDBI) on the same side as a straight water retreat is the
mathematical corroboration for encroachment.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import numpy as np
from pyproj import Transformer
from shapely import transform as _shapely_transform
from shapely.geometry import MultiPolygon, mapping, shape

from app.config import Settings
from app.geo.indices import ndbi
from app.geo.raster import polygon_pixel_mask
from app.services.osm import WaterBodyRecord


@dataclass(frozen=True)
class BufferMetrics:
    buffer_m: float
    ring_area_m2: float
    ndbi_mean: float
    ndbi_built_pct: float
    ndbi_anomaly: float


def _reproject(geometry: Any, transformer: Transformer) -> Any:
    def project(coords: Any) -> Any:
        xs, ys = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack((xs, ys))

    return _shapely_transform(geometry, project)


def _utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def buffer_ring(max_geometry: dict[str, Any], buffer_m: float) -> dict[str, Any]:
    """Return the ring polygon (buffer minus the lake) as a GeoJSON geometry."""

    geom = shape(max_geometry)
    if not geom.is_valid:
        geom = geom.buffer(0)
    if isinstance(geom, MultiPolygon):
        geom = max(geom.geoms, key=lambda part: part.area)

    lon, lat = geom.centroid.x, geom.centroid.y
    epsg = _utm_epsg(lon, lat)
    to_utm = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    to_4326 = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)

    projected = _reproject(geom, to_utm)
    ring = projected.buffer(buffer_m).difference(projected)
    return mapping(_reproject(ring, to_4326))


def _ring_area_m2(ring_geometry: dict[str, Any]) -> float:
    ring = shape(ring_geometry)
    lon, lat = ring.centroid.x, ring.centroid.y
    epsg = _utm_epsg(lon, lat)
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    projected = _reproject(ring, transformer)
    return float(projected.area)


def synthetic_ndbi(
    record: WaterBodyRecord,
    settings: Settings,
    *,
    style: str,
    rng: random.Random,
) -> BufferMetrics:
    """Deterministic stand-in NDBI field over the 100 m ring.

    Built-up share is chosen from the retreat style: encroachment (one_sided)
    gets a large concrete spike, seasonal drought (concentric) stays rural.
    """

    ring = buffer_ring(record.mask_geometry, settings.forensic_buffer_m)
    ring_area = _ring_area_m2(ring)

    west, south, east, north = record.feature["properties"]["bbox"]
    pad_x = (east - west) * 0.25
    pad_y = (north - south) * 0.25
    bounds = (west - pad_x, south - pad_y, east + pad_x, north + pad_y)
    width = height = 96

    ring_mask = polygon_pixel_mask(ring, bounds, width, height)
    n_ring = int(ring_mask.sum()) or 1

    if style == "one_sided":
        built_target = rng.uniform(0.25, 0.55)
    elif style == "mixed":
        built_target = rng.uniform(0.10, 0.25)
    else:
        built_target = rng.uniform(0.02, 0.08)

    np_rng = np.random.default_rng(rng.getrandbits(64))
    swir = 0.14 + 0.10 * np_rng.random((height, width))  # B11 vegetation-ish
    nir = 0.32 + 0.08 * np_rng.random((height, width))  # B08 vegetation-ish

    built_count = int(round(built_target * n_ring))
    candidates = np.flatnonzero(ring_mask.ravel())
    if built_count > 0 and candidates.size:
        scores = np_rng.random((height, width)).ravel()
        order = candidates[np.argsort(-scores[candidates])][:built_count]
        flat_swir = swir.ravel()
        flat_nir = nir.ravel()
        flat_swir[order] = 0.38 + 0.08 * np_rng.random(built_count)  # concrete
        flat_nir[order] = 0.16 + 0.04 * np_rng.random(built_count)

    values = ndbi(swir, nir)
    ring_values = values[ring_mask]
    ring_values = ring_values[np.isfinite(ring_values)]

    built_pct = float((ring_values > 0.0).mean() * 100) if ring_values.size else 0.0
    mean = float(ring_values.mean()) if ring_values.size else 0.0
    anomaly = min(1.0, built_pct / 50.0)

    return BufferMetrics(
        buffer_m=settings.forensic_buffer_m,
        ring_area_m2=ring_area,
        ndbi_mean=mean,
        ndbi_built_pct=built_pct,
        ndbi_anomaly=anomaly,
    )
