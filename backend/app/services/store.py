"""In-memory water-body store for Phase 1.

This is intentionally a repository boundary, not a database. Phase 2 can swap
the implementation for PostGIS without changing the API routes.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from app.services.osm import WaterBodyRecord


class WaterBodyStore:
    def __init__(self) -> None:
        self._records: dict[str, WaterBodyRecord] = {}
        self._lock = Lock()

    def put_many(self, records: list[WaterBodyRecord]) -> None:
        with self._lock:
            for record in records:
                self._records[record.water_body_id] = record

    def get(self, water_body_id: str) -> WaterBodyRecord | None:
        with self._lock:
            return self._records.get(water_body_id)

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def mask_payload(self, water_body_id: str) -> dict[str, Any] | None:
        record = self.get(water_body_id)
        if record is None:
            return None
        properties = dict(record.feature["properties"])
        properties["mask_crs"] = "EPSG:4326"
        return {
            "type": "Feature",
            "geometry": record.mask_geometry,
            "properties": properties,
        }


store = WaterBodyStore()
