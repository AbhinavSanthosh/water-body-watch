import numpy as np
import pytest

from app.geo.indices import (
    cloud_fraction,
    masked_area_m2,
    ndwi,
    pixel_area_m2,
    valid_pixel_mask,
    water_mask,
)
from app.geo.raster import polygon_pixel_mask


def test_ndwi_formula_and_threshold() -> None:
    green = np.array([[0.18, 0.06], [0.18, 0.06]])
    nir = np.array([[0.02, 0.32], [0.02, 0.32]])

    values = ndwi(green, nir)
    expected = (0.18 - 0.02) / (0.18 + 0.02)
    assert values[0, 0] == pytest.approx(expected)
    assert values[0, 1] < 0

    mask = water_mask(values, 0.5)
    assert mask.tolist() == [[True, False], [True, False]]


def test_ndwi_is_nan_for_zero_and_undefined() -> None:
    values = ndwi(np.array([0.0, np.nan]), np.array([0.0, 0.2]))
    assert np.isnan(values).all()


def test_ndwi_requires_matching_shapes() -> None:
    with pytest.raises(ValueError):
        ndwi(np.zeros((2, 2)), np.zeros((2, 3)))


def test_scl_masks_clouds_shadow_and_nodata() -> None:
    scl = np.array([[4, 8], [9, 3], [0, 11], [6, 5]], dtype=float)
    valid = valid_pixel_mask(scl, scl.shape)
    assert valid.tolist() == [
        [True, False],
        [False, False],
        [False, False],
        [True, True],
    ]
    assert cloud_fraction(scl) == pytest.approx(62.5)

    values = np.full(scl.shape, 0.9)
    inside = np.ones(scl.shape, dtype=bool)
    area, water_pixels, valid_pixels = masked_area_m2(values, inside, valid, 100.0, threshold=0.5)
    assert water_pixels == 3
    assert valid_pixels == 3
    assert area == pytest.approx(300.0)


def test_mask_counts_only_pixels_inside_polygon() -> None:
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [77.60, 12.97],
                [77.61, 12.97],
                [77.61, 12.98],
                [77.60, 12.98],
                [77.60, 12.97],
            ]
        ],
    }
    bounds = (77.59, 12.96, 77.62, 12.99)
    inside = polygon_pixel_mask(geometry, bounds, 60, 60)
    assert 0 < inside.sum() < inside.size
    # Water everywhere, but the bbox corners stay excluded.
    values = np.full(inside.shape, 0.9)
    valid = np.ones(inside.shape, dtype=bool)
    area, pixels, _ = masked_area_m2(values, inside, valid, 100.0)
    assert pixels == int(inside.sum())
    assert area == pytest.approx(pixels * 100.0)


def test_pixel_area_uses_metres_not_degrees() -> None:
    # Roughly 1.1 km x 1.2 km at Bengaluru's latitude.
    area = pixel_area_m2((77.60, 12.97, 77.61, 12.98), 100, 100)
    assert 100 < area < 160
    total = area * 100 * 100
    assert 1_000_000 < total < 1_600_000
