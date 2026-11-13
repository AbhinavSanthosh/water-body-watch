import pytest

from app.config import Settings
from app.services import osm
from app.services.osm import (
    OsmFetchError,
    _gdf_from_osm2geojson,
    _records_from_gdf,
    fetch_water_bodies,
)
from app.services.overpass import OverpassError, build_query


def _way_element() -> dict:
    return {
        "type": "way",
        "id": 182177278,
        "tags": {"name": "Kadiranpalaya Kere", "natural": "water", "water": "lake"},
        "geometry": [
            {"lat": 12.98, "lon": 77.61},
            {"lat": 12.98, "lon": 77.62},
            {"lat": 12.99, "lon": 77.62},
            {"lat": 12.99, "lon": 77.61},
            {"lat": 12.98, "lon": 77.61},
        ],
    }


def _relation_element() -> dict:
    return {
        "type": "relation",
        "id": 1857615,
        "tags": {
            "name": "Ulsoor Lake",
            "natural": "water",
            "water": "lake",
            "type": "multipolygon",
        },
        "members": [
            {
                "type": "way",
                "ref": 1,
                "role": "outer",
                "geometry": [
                    {"lat": 12.97, "lon": 77.60},
                    {"lat": 12.97, "lon": 77.61},
                    {"lat": 12.98, "lon": 77.61},
                    {"lat": 12.98, "lon": 77.60},
                    {"lat": 12.97, "lon": 77.60},
                ],
            }
        ],
    }


def test_build_query_uses_south_west_north_east_order() -> None:
    query = build_query((77.60, 12.96, 77.64, 13.00), 25)
    assert "(12.96,77.6,13.0,77.64)" in query
    assert 'way["natural"="water"]' in query
    assert 'relation["natural"="water"]' in query
    assert "out geom;" in query


def test_overpass_payload_is_flattened(settings: Settings) -> None:
    gdf = _gdf_from_osm2geojson({"elements": [_way_element(), _relation_element()]})
    assert len(gdf) == 2
    assert set(gdf["element_type"]) == {"way", "relation"}
    assert set(gdf["osmid"]) == {182177278, 1857615}

    records = _records_from_gdf(gdf, settings)
    ids = {record.water_body_id for record in records}
    assert "osm:relation/1857615" in ids
    assert "osm:way/182177278" in ids


async def test_overpass_failure_falls_back_to_nominatim(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*_: object, **__: object) -> dict:
        raise OverpassError("all mirrors busy")

    async def fallback(*_: object, **__: object) -> list[osm.WaterBodyRecord]:
        return _records_from_gdf(_gdf_from_osm2geojson({"elements": [_way_element()]}), settings)

    monkeypatch.setattr(osm, "query_overpass", fail)
    monkeypatch.setattr(osm, "_nominatim_fallback", fallback)

    records = await fetch_water_bodies((77.60, 12.96, 77.64, 13.00), settings)
    assert [record.water_body_id for record in records] == ["osm:way/182177278"]
    assert records[0].feature["properties"]["source"] == "osm"


async def test_empty_result_raises(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*_: object, **__: object) -> dict:
        raise OverpassError("all mirrors busy")

    async def empty(*_: object, **__: object) -> list[osm.WaterBodyRecord]:
        return []

    monkeypatch.setattr(osm, "query_overpass", fail)
    monkeypatch.setattr(osm, "_nominatim_fallback", empty)

    with pytest.raises(OsmFetchError):
        await fetch_water_bodies((77.60, 12.96, 77.64, 13.00), settings)
