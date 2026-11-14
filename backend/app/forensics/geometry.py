"""Geometric retreat classification for Phase 4.

Compares the current NDWI water boundary against the Phase 2 historical maximum
boundary (the OSM lakebed). Concentric retreat means the whole shoreline pulls
in evenly (seasonal drought); a straight, one-sided cut means a single bank has
been walled off (encroachment). All distances are computed in a local UTM CRS,
never in degrees.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import numpy as np
from pyproj import Transformer
from shapely import affinity
from shapely import transform as _shapely_transform
from shapely.geometry import LineString, MultiPolygon, Polygon, box, mapping, shape

Style = str  # "concentric" | "one_sided" | "mixed"

_SAMPLE_POINTS = 160
_RESOLUTION_M = 2.0


def _reproject(geometry: Any, transformer: Transformer) -> Any:
    def project(coords: Any) -> Any:
        xs, ys = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack((xs, ys))

    return _shapely_transform(geometry, project)


def _utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _projector(geometry: Any) -> tuple[Any, Transformer]:
    """Return (geometry_in_utm, transformer) for the geometry's local zone."""

    lon, lat = geometry.centroid.x, geometry.centroid.y
    epsg = _utm_epsg(lon, lat)
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    return _reproject(geometry, transformer), transformer


def current_water_polygon(
    max_geometry: dict[str, Any],
    target_area_m2: float,
    style: Style,
    rng: random.Random,
) -> dict[str, Any]:
    """Synthesize a current water boundary from the lakebed polygon.

    ``style`` picks the retreat shape: concentric (even erosion), one_sided
    (a straight cut along one bank), or mixed (elliptical, in between).
    """

    max_geom = shape(max_geometry)
    if not max_geom.is_valid:
        max_geom = max_geom.buffer(0)
    if isinstance(max_geom, MultiPolygon):
        max_geom = max(max_geom.geoms, key=lambda part: part.area)

    projected, _ = _projector(max_geom)
    max_area = float(projected.area)
    retained = max(0.15, min(0.95, target_area_m2 / max_area)) if max_area else 1.0

    if style == "one_sided":
        current = _cut_side(projected, rng.choice(["west", "east", "south", "north"]), retained)
    elif style == "mixed":
        factor = retained
        anisotropy = rng.uniform(0.6, 0.9)
        current = affinity.scale(
            projected, xfact=factor, yfact=factor * anisotropy, origin="centroid"
        )
    else:
        factor = retained**0.5
        current = affinity.scale(projected, xfact=factor, yfact=factor, origin="centroid")

    if current.is_empty:
        current = affinity.scale(projected, xfact=0.8, yfact=0.8, origin="centroid")

    epsg = _utm_epsg(max_geom.centroid.x, max_geom.centroid.y)
    back_transformer = Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    back = _reproject(current, back_transformer)
    return mapping(back)


def _cut_side(geometry: Polygon, side: str, retained: float) -> Polygon:
    """Cut one side of the polygon with a straight line to a target area share."""

    minx, miny, maxx, maxy = geometry.bounds
    pad = (maxx - minx) * 0.01
    target = geometry.area * retained

    def cut(xmin: float, ymin: float, xmax: float, ymax: float) -> Polygon:
        half = box(xmin, ymin, xmax, ymax)
        result = geometry.intersection(half)
        if isinstance(result, MultiPolygon):
            result = max(result.geoms, key=lambda part: part.area)
        if isinstance(result, Polygon):
            return result
        return geometry

    if side == "west":
        lo, hi = minx, maxx
        while hi - lo > 0.5:
            mid = (lo + hi) / 2
            if cut(mid - pad, miny - pad, maxx + pad, maxy + pad).area < target:
                hi = mid
            else:
                lo = mid
        return cut(lo - pad, miny - pad, maxx + pad, maxy + pad)
    if side == "east":
        lo, hi = minx, maxx
        while hi - lo > 0.5:
            mid = (lo + hi) / 2
            if cut(minx - pad, miny - pad, mid + pad, maxy + pad).area < target:
                lo = mid
            else:
                hi = mid
        return cut(minx - pad, miny - pad, lo + pad, maxy + pad)
    if side == "south":
        lo, hi = miny, maxy
        while hi - lo > 0.5:
            mid = (lo + hi) / 2
            if cut(minx - pad, mid - pad, maxx + pad, maxy + pad).area < target:
                hi = mid
            else:
                lo = mid
        return cut(minx - pad, lo - pad, maxx + pad, maxy + pad)
    lo, hi = miny, maxy
    while hi - lo > 0.5:
        mid = (lo + hi) / 2
        if cut(minx - pad, miny - pad, maxx + pad, mid + pad).area < target:
            lo = mid
        else:
            hi = mid
    return cut(minx - pad, miny - pad, maxx + pad, lo + pad)


@dataclass(frozen=True)
class GeometryMetrics:
    max_area_m2: float
    current_area_m2: float
    shrinkage_pct: float
    shared_boundary_ratio: float
    front_straightness: float
    asymmetry_index: float
    mean_retreat_m: float
    max_retreat_m: float
    style: Style


def _pca_straightness(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    array = np.asarray(points, dtype=np.float64)
    centered = array - array.mean(axis=0)
    covariance = centered.T @ centered
    eigenvalues = np.linalg.eigvalsh(covariance)
    total = float(eigenvalues.sum())
    return float(eigenvalues[-1] / total) if total > 0 else 0.0


def _sample_boundary(boundary: Any, count: int) -> list[Any]:
    """Sample points uniformly by length along a (possibly multi-part) boundary."""

    if isinstance(boundary, LineString):
        parts = [boundary]
    else:
        parts = [part for part in boundary.geoms if isinstance(part, LineString)]
    if not parts:
        return []

    lengths = [part.length for part in parts]
    total = float(sum(lengths))
    if total <= 0:
        return []

    points: list[Any] = []
    cumulative = 0.0
    part_index = 0
    for index in range(count):
        target = (index / count) * total
        while part_index < len(parts) - 1 and cumulative + lengths[part_index] < target:
            cumulative += lengths[part_index]
            part_index += 1
        length = lengths[part_index] if lengths[part_index] else 1.0
        local = max(0.0, min(1.0, (target - cumulative) / length))
        points.append(parts[part_index].interpolate(local, normalized=True))
    return points


def geometry_metrics(
    max_geometry: dict[str, Any], current_geometry: dict[str, Any]
) -> GeometryMetrics:
    max_geom = shape(max_geometry)
    current_geom = shape(current_geometry)
    if not max_geom.is_valid:
        max_geom = max_geom.buffer(0)
    if not current_geom.is_valid:
        current_geom = current_geom.buffer(0)
    if isinstance(current_geom, MultiPolygon):
        current_geom = max(current_geom.geoms, key=lambda part: part.area)
    if isinstance(max_geom, MultiPolygon):
        max_geom = max(max_geom.geoms, key=lambda part: part.area)

    projected_max, _ = _projector(max_geom)
    projected_current, _ = _projector(current_geom)

    max_area = float(projected_max.area)
    current_area = float(projected_current.area)
    shrinkage = (1 - current_area / max_area) * 100 if max_area else 0.0

    boundary = projected_current.boundary
    max_exterior: LineString = projected_max.exterior

    sample_points = _sample_boundary(boundary, _SAMPLE_POINTS)
    retreats: list[float] = [max_exterior.distance(point) for point in sample_points]

    if not retreats:
        return GeometryMetrics(
            max_area_m2=max_area,
            current_area_m2=current_area,
            shrinkage_pct=shrinkage,
            shared_boundary_ratio=0.0,
            front_straightness=0.0,
            asymmetry_index=0.0,
            mean_retreat_m=0.0,
            max_retreat_m=0.0,
            style="concentric",
        )

    shared = sum(1 for distance in retreats if distance <= _RESOLUTION_M)
    shared_ratio = shared / len(retreats)
    retreated = [
        (distance, point)
        for distance, point in zip(retreats, sample_points, strict=False)
        if distance > _RESOLUTION_M
    ]
    straightness = _pca_straightness([(point.x, point.y) for _, point in retreated])
    asymmetry = shared_ratio * straightness

    mean_retreat = float(np.mean(retreats))
    max_retreat = float(np.max(retreats))

    if shared_ratio >= 0.4 and straightness >= 0.8:
        style: Style = "one_sided"
    elif shared_ratio < 0.15:
        style = "concentric"
    else:
        style = "mixed"

    return GeometryMetrics(
        max_area_m2=max_area,
        current_area_m2=current_area,
        shrinkage_pct=shrinkage,
        shared_boundary_ratio=shared_ratio,
        front_straightness=straightness,
        asymmetry_index=asymmetry,
        mean_retreat_m=mean_retreat,
        max_retreat_m=max_retreat,
        style=style,
    )
