import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from app.config import Settings
from app.services.osm import (
    _bbox_around,
    _clamp_bbox,
    _records_from_gdf,
    feature_collection,
    load_demo_records,
)


def _square() -> Polygon:
    return Polygon(
        [
            (77.60, 12.97),
            (77.61, 12.97),
            (77.61, 12.98),
            (77.60, 12.98),
            (77.60, 12.97),
        ]
    )


def test_records_compute_area_in_metres_and_keep_mask(settings: Settings) -> None:
    gdf = gpd.GeoDataFrame(
        {"name": ["Test Lake"], "water": ["lake"]}, geometry=[_square()], crs="EPSG:4326"
    )
    records = _records_from_gdf(gdf, settings)

    assert len(records) == 1
    props = records[0].feature["properties"]
    # 0.01 degrees near Bengaluru is roughly 1.2 km x 1.1 km.
    assert 1_000_000 < props["area_m2"] < 1_500_000
    assert props["area_hectares"] == round(props["area_m2"] / 10_000, 3)
    assert props["water_body_id"] == "osm:way/0"
    assert props["mask_crs"] == "EPSG:4326"
    assert records[0].mask_geometry["type"] == "Polygon"
    assert props["bbox"] == [77.6, 12.97, 77.61, 12.98]


def test_osmnx_2_index_names_are_preserved(settings: Settings) -> None:
    index = pd.MultiIndex.from_tuples([("relation", 1857615)], names=["element", "id"])
    gdf = gpd.GeoDataFrame(
        {"name": ["Ulsoor Lake"], "water": ["lake"]},
        geometry=[_square()],
        index=index,
        crs="EPSG:4326",
    )
    records = _records_from_gdf(gdf, settings)
    assert records[0].water_body_id == "osm:relation/1857615"


def test_feature_collection_shape(settings: Settings) -> None:
    gdf = gpd.GeoDataFrame({"name": ["Test Lake"]}, geometry=[_square()], crs="EPSG:4326")
    records = _records_from_gdf(gdf, settings)
    collection = feature_collection(records)
    assert collection["type"] == "FeatureCollection"
    assert collection["features"][0]["geometry"]["type"] == "Polygon"


def test_bbox_helpers() -> None:
    west, south, east, north = _bbox_around(12.97, 77.6, 5)
    assert west < 77.6 < east
    assert south < 12.97 < north
    assert east - west > north - south  # longitude degrees are smaller near the equator


def test_clamp_bbox_limits_span() -> None:
    west, south, east, north = _clamp_bbox((70.0, 10.0, 80.0, 20.0), 0.2)
    assert round(east - west, 6) == 0.2
    assert round(north - south, 6) == 0.2


def test_load_demo_records(settings: Settings) -> None:
    records = load_demo_records(settings)
    assert len(records) == 1
    assert records[0].water_body_id == "osm:way/1"
    assert records[0].feature["properties"]["name"] == "Test Lake"
