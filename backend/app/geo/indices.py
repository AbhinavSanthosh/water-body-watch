"""Pure geospatial index math for Phase 3.

No network or raster-file I/O lives here: callers pass numpy arrays (Sentinel-2
surface reflectance, already scaled to 0-1) and get index values, masks, and
areas back.

NDWI = (B03 - B08) / (B03 + B08), where B03 is green and B08 is NIR. Active
water is NDWI > 0.5.
"""

from __future__ import annotations

import numpy as np
from pyproj import Transformer

# Sentinel-2 L2A Scene Classification Layer values that are not usable water
# observations: no data, saturated/defective, cloud shadow, clouds, cirrus, snow.
INVALID_SCL_CLASSES = frozenset({0, 1, 3, 8, 9, 10, 11})

Bounds = tuple[float, float, float, float]


def ndwi(green: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """Normalized Difference Water Index, with undefined pixels as NaN."""

    green = np.asarray(green, dtype=np.float64)
    nir = np.asarray(nir, dtype=np.float64)
    if green.shape != nir.shape:
        raise ValueError("Green and NIR bands must have the same shape.")

    denominator = green + nir
    with np.errstate(divide="ignore", invalid="ignore"):
        values = (green - nir) / denominator
    values[~np.isfinite(values)] = np.nan
    values[denominator == 0] = np.nan
    return values


def valid_pixel_mask(scl: np.ndarray | None, shape: tuple[int, int]) -> np.ndarray:
    """Pixels that are usable: finite and not cloud/shadow/snow/no-data."""

    if scl is None:
        return np.ones(shape, dtype=bool)
    classes = np.asarray(scl)
    valid = np.isfinite(classes)
    for value in INVALID_SCL_CLASSES:
        valid &= classes != value
    return valid


def cloud_fraction(scl: np.ndarray | None) -> float:
    """Share of pixels masked by the SCL as cloud, shadow, snow, or no-data."""

    if scl is None:
        return 0.0
    classes = np.asarray(scl)
    masked = ~valid_pixel_mask(classes, classes.shape)
    return float(masked.mean() * 100) if classes.size else 0.0


def water_mask(values: np.ndarray, threshold: float = 0.5) -> np.ndarray:
    return np.isfinite(values) & (values > threshold)


def ndbi(swir: np.ndarray, nir: np.ndarray) -> np.ndarray:
    """Normalized Difference Built-up Index: (B11 - B08) / (B11 + B08).

    B11 = SWIR1, B08 = NIR. Built-up pixels (concrete, bare soil) are positive.
    """

    swir = np.asarray(swir, dtype=np.float64)
    nir = np.asarray(nir, dtype=np.float64)
    if swir.shape != nir.shape:
        raise ValueError("SWIR and NIR bands must have the same shape.")

    denominator = swir + nir
    with np.errstate(divide="ignore", invalid="ignore"):
        values = (swir - nir) / denominator
    values[~np.isfinite(values)] = np.nan
    values[denominator == 0] = np.nan
    return values


def masked_area_m2(
    values: np.ndarray,
    inside: np.ndarray,
    valid: np.ndarray,
    pixel_area_m2: float,
    *,
    threshold: float = 0.5,
) -> tuple[float, int, int]:
    """Return (area_m2, water_pixel_count, valid_inside_pixel_count)."""

    usable = inside & valid
    water = water_mask(values, threshold) & usable
    water_pixels = int(water.sum())
    valid_pixels = int(usable.sum())
    return water_pixels * pixel_area_m2, water_pixels, valid_pixels


def pixel_area_m2(bounds: Bounds, width: int, height: int) -> float:
    """Approximate ground area of one pixel using a local UTM CRS.

    Areas are never computed in EPSG:4326. At lake scale a single UTM scale
    factor for the bbox is accurate to well under a percent.
    """

    if width <= 0 or height <= 0:
        raise ValueError("Raster width and height must be positive.")

    west, south, east, north = bounds
    zone = int((((west + east) / 2) + 180) // 6) + 1
    epsg = (32600 if (south + north) / 2 >= 0 else 32700) + zone
    transformer = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    x0, y0 = transformer.transform(west, south)
    x1, y1 = transformer.transform(east, north)
    return abs((x1 - x0) * (y1 - y0)) / (width * height)
