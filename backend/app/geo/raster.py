"""Rasterize a lakebed polygon onto the satellite scene grid.

Used to count only pixels inside the exact Phase 1 mask, never its bounding box.
Rows follow the GeoTIFF convention: row 0 is the northern edge.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from shapely import contains_xy
from shapely.geometry import shape

from app.geo.indices import Bounds


def pixel_centers(bounds: Bounds, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """Longitude/latitude grid of pixel centers, row 0 = north."""

    west, south, east, north = bounds
    xs = west + (np.arange(width) + 0.5) * (east - west) / width
    ys = north - (np.arange(height) + 0.5) * (north - south) / height
    return np.meshgrid(xs, ys)


def polygon_pixel_mask(
    mask_geometry: dict[str, Any],
    bounds: Bounds,
    width: int,
    height: int,
) -> np.ndarray:
    """Boolean mask that is true inside the lakebed polygon."""

    xx, yy = pixel_centers(bounds, width, height)
    geometry = shape(mask_geometry)
    return contains_xy(geometry, xx, yy)
