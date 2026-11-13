"""OpenStreetMap discovery: geocode a place, fetch water polygons, build masks."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from typing import Any

import geopandas as gpd
import httpx
import osm2geojson
from shapely import make_valid
from shapely.geometry import MultiPolygon, Polygon, mapping, shape

from app.config import Settings
from app.models import GeocodeResult
from app.services.overpass import OverpassError, query_overpass

logger = logging.getLogger(__name__)

EXCLUDED_WATER_TYPES = {
    "river",
    "stream",
    "canal",
    "ditch",
    "drain",
    "wastewater",
    "pool",
    "swimming_pool",
    "reflecting_pool",
    "fountain",
    "moat",
}


class GeocodeError(ValueError):
    pass


class OsmFetchError(RuntimeError):
    pass


@dataclass
class WaterBodyRecord:
    feature: dict[str, Any]
    mask_geometry: dict[str, Any]

    @property
    def water_body_id(self) -> str:
        return str(self.feature["properties"]["water_body_id"])


def _bbox_around(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    lat_delta = radius_km / 111.32
    lon_delta = radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.01))
    return (lon - lon_delta, lat - lat_delta, lon + lon_delta, lat + lat_delta)


def _clamp_bbox(
    bbox: tuple[float, float, float, float], max_span: float
) -> tuple[float, float, float, float]:
    west, south, east, north = bbox
    lon_span = east - west
    lat_span = north - south
    center_lon = (west + east) / 2
    center_lat = (south + north) / 2
    if lon_span > max_span:
        west, east = center_lon - max_span / 2, center_lon + max_span / 2
    if lat_span > max_span:
        south, north = center_lat - max_span / 2, center_lat + max_span / 2
    return (west, south, east, north)


async def geocode(query: str, settings: Settings, radius_km: float | None = None) -> GeocodeResult:
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 5,
        "addressdetails": 1,
        "accept-language": "en",
    }
    headers = {"User-Agent": settings.osm_user_agent}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{settings.nominatim_url.rstrip('/')}/search", params=params, headers=headers
            )
            response.raise_for_status()
            results = response.json()
    except Exception as exc:  # noqa: BLE001
        raise GeocodeError(f"Could not reach Nominatim: {exc}") from exc

    if not results:
        raise GeocodeError(f"No place found for '{query}'. Try a nearby landmark or city.")

    result = results[0]
    lat = float(result["lat"])
    lon = float(result["lon"])
    raw_bbox = result.get("boundingbox") or []
    if len(raw_bbox) == 4:
        south, north, west, east = (float(value) for value in raw_bbox)
        bbox = (west, south, east, north)
    else:
        bbox = _bbox_around(lat, lon, radius_km or settings.osm_default_radius_km)

    category = str(result.get("category") or "")
    result_type = str(result.get("type") or "")
    lon_span = bbox[2] - bbox[0]
    lat_span = bbox[3] - bbox[1]
    point_like = result_type in {
        "road",
        "highway",
        "street",
        "footway",
        "path",
        "stop",
        "station",
        "railway",
        "bus_stop",
        "locality",
        "neighbourhood",
    }
    tiny = lon_span < 0.01 or lat_span < 0.01

    if point_like or tiny:
        bbox = _bbox_around(lat, lon, radius_km or settings.osm_default_radius_km)
    else:
        bbox = _clamp_bbox(bbox, settings.osm_max_bbox_span_deg)

    return GeocodeResult(
        display_name=str(result.get("display_name") or query),
        lat=lat,
        lon=lon,
        bbox=bbox,
        category=category,
        result_type=result_type,
        osm_type=str(result.get("osm_type") or ""),
        osm_id=int(result["osm_id"]) if result.get("osm_id") else None,
    )


def _as_polygonal(geometry: Any) -> Polygon | MultiPolygon | None:
    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    repaired = make_valid(geometry)
    if repaired.geom_type in {"Polygon", "MultiPolygon"}:
        return repaired
    polygons = [part for part in getattr(repaired, "geoms", []) if part.geom_type == "Polygon"]
    if not polygons:
        return None
    return MultiPolygon(polygons)


def _utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180) / 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _tags_for_row(row: Any) -> dict[str, str]:
    tags: dict[str, str] = {}
    for key in (
        "name",
        "name:en",
        "water",
        "natural",
        "landuse",
        "wikidata",
        "wikipedia",
        "source",
    ):
        value = row.get(key)
        if value is not None and str(value) not in {"", "nan", "None"}:
            tags[key] = str(value)
    return tags


def _records_from_gdf(
    gdf: gpd.GeoDataFrame, settings: Settings, source: str = "osm"
) -> list[WaterBodyRecord]:
    if gdf.empty:
        return []

    gdf = gdf[gdf.geometry.notna()].copy()
    gdf["geometry"] = gdf.geometry.apply(_as_polygonal)
    gdf = gdf[gdf.geometry.notna()].copy()
    if gdf.empty:
        return []

    gdf = gdf.reset_index()
    # OSMnx 2.1 renamed the MultiIndex levels to "element"/"id"; older versions
    # used "element_type"/"osmid". Accept both so upgrades do not create fake IDs.
    if "element_type" not in gdf.columns and "element" in gdf.columns:
        gdf = gdf.rename(columns={"element": "element_type"})
    if "osmid" not in gdf.columns and "id" in gdf.columns:
        gdf = gdf.rename(columns={"id": "osmid"})
    if "element_type" not in gdf.columns:
        gdf["element_type"] = "way"
    if "osmid" not in gdf.columns:
        gdf["osmid"] = range(len(gdf))

    # Compute area in a local UTM zone, never in EPSG:4326.
    centroid = gdf.geometry.union_all().centroid
    utm = _utm_epsg(centroid.x, centroid.y)
    gdf["area_m2"] = gdf.to_crs(epsg=utm).area.astype(float)
    gdf = gdf[gdf["area_m2"] >= settings.osm_min_area_m2]

    records: list[WaterBodyRecord] = []
    seen: set[str] = set()
    for _, row in gdf.iterrows():
        tags = _tags_for_row(row)
        water_type = tags.get("water") or tags.get("landuse") or tags.get("natural") or "water"
        if water_type.lower() in EXCLUDED_WATER_TYPES:
            continue

        element_type = str(row["element_type"])
        osm_id = int(row["osmid"])
        water_body_id = f"osm:{element_type}/{osm_id}"
        if water_body_id in seen:
            continue
        seen.add(water_body_id)

        geometry = row.geometry
        name = tags.get("name:en") or tags.get("name") or f"Unnamed {water_type}"
        display_geometry = geometry.simplify(0.00004, preserve_topology=True)
        minx, miny, maxx, maxy = geometry.bounds
        point = geometry.representative_point()
        area_m2 = round(float(row["area_m2"]), 1)
        properties = {
            "water_body_id": water_body_id,
            "name": name,
            "area_m2": area_m2,
            "area_hectares": round(area_m2 / 10_000, 3),
            "bbox": [round(minx, 7), round(miny, 7), round(maxx, 7), round(maxy, 7)],
            "centroid": [round(point.x, 7), round(point.y, 7)],
            "osm_type": element_type,
            "osm_id": osm_id,
            "water_type": water_type,
            "tags": tags,
            "source": source,
            "mask_crs": "EPSG:4326",
        }
        records.append(
            WaterBodyRecord(
                feature={
                    "type": "Feature",
                    "geometry": mapping(display_geometry),
                    "properties": properties,
                },
                mask_geometry=mapping(geometry),
            )
        )

    records.sort(key=lambda record: record.feature["properties"]["area_m2"], reverse=True)
    return records[: settings.osm_max_results]


NOMINATIM_TERMS = (
    "lake",
    "kere",
    "pond",
    "reservoir",
    "talab",
    "jheel",
    "eri",
    "cheruvu",
)


def _gdf_from_osm2geojson(payload: dict[str, Any]) -> gpd.GeoDataFrame:
    """Flatten an Overpass JSON response into a tagged GeoDataFrame."""

    geojson = osm2geojson.json2geojson(payload)
    rows: list[dict[str, Any]] = []
    for feature in geojson.get("features", []):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry")
        if not geometry:
            continue
        tags = properties.get("tags") or {}
        element_type = properties.get("type")
        osm_id = properties.get("id")
        if element_type is None or osm_id is None:
            continue
        try:
            parsed = shape(geometry)
        except Exception:  # noqa: BLE001 - skip malformed OSM geometry
            continue
        row = dict(tags)
        row["element_type"] = str(element_type)
        row["osmid"] = int(osm_id)
        row["geometry"] = parsed
        rows.append(row)

    if not rows:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")


async def _nominatim_fallback(
    bbox: tuple[float, float, float, float], settings: Settings
) -> list[WaterBodyRecord]:
    """Ask Nominatim for named lakes when Overpass is unavailable.

    This is deliberately a small, throttled fallback: Nominatim only returns
    named features, so it is not a replacement for a polygon query.
    """

    west, south, east, north = bbox
    viewbox = f"{west},{north},{east},{south}"
    headers = {"User-Agent": settings.osm_user_agent}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=30.0) as client:
        for index, term in enumerate(NOMINATIM_TERMS):
            if index:
                await asyncio.sleep(1.05)  # Nominatim usage policy: max 1 req/s
            params = {
                "q": term,
                "format": "jsonv2",
                "viewbox": viewbox,
                "bounded": 1,
                "polygon_geojson": 1,
                "limit": 50,
                "addressdetails": 0,
                "accept-language": "en",
            }
            try:
                response = await client.get(
                    f"{settings.nominatim_url.rstrip('/')}/search",
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
                results = response.json()
            except Exception as exc:  # noqa: BLE001 - try the next term
                logger.warning("Nominatim fallback term '%s' failed: %s", term, exc)
                continue

            for result in results:
                geojson = result.get("geojson")
                osm_type = str(result.get("osm_type") or "")
                osm_id = result.get("osm_id")
                if not geojson or not osm_type or osm_id is None:
                    continue
                key = f"{osm_type}/{osm_id}"
                if key in seen:
                    continue
                seen.add(key)
                try:
                    geometry = shape(geojson)
                except Exception:  # noqa: BLE001
                    continue
                name = str(result.get("name") or result.get("display_name") or "")
                row: dict[str, Any] = {
                    "element_type": osm_type,
                    "osmid": int(osm_id),
                    "geometry": geometry,
                }
                if name:
                    row["name"] = name
                if result.get("type"):
                    row["water"] = str(result["type"])
                rows.append(row)

    if not rows:
        return []
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    return _records_from_gdf(gdf, settings, source="osm-nominatim")


async def fetch_water_bodies(
    bbox: tuple[float, float, float, float], settings: Settings
) -> list[WaterBodyRecord]:
    try:
        payload = await query_overpass(bbox, settings)
        records = _records_from_gdf(_gdf_from_osm2geojson(payload), settings)
    except OverpassError as exc:
        logger.warning("Overpass unavailable, falling back to Nominatim: %s", exc)
        records = await _nominatim_fallback(bbox, settings)

    if not records:
        raise OsmFetchError(
            "No lakes or ponds were found in that area. Try a larger radius or a nearby landmark."
        )
    return records


def load_demo_records(settings: Settings) -> list[WaterBodyRecord]:
    path = settings.demo_file
    if not path.exists():
        raise OsmFetchError(
            f"Demo data is not available at {path}. Run the fixture generator or use live OSM."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    records: list[WaterBodyRecord] = []
    for feature in payload.get("features", []):
        properties = dict(feature.get("properties") or {})
        geometry = feature.get("geometry")
        if not geometry:
            continue
        properties.setdefault("source", "demo-osm")
        records.append(
            WaterBodyRecord(
                feature={"type": "Feature", "geometry": geometry, "properties": properties},
                mask_geometry=geometry,
            )
        )
    return records


def feature_collection(records: list[WaterBodyRecord]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [record.feature for record in records],
    }
