"""In-memory cache for Phase 2 historical profiles.

Keyed by ``(water_body_id, month, index_version, source, language, speak)`` so a
new calendar month, a threshold-version bump, or a different briefing language
misses automatically. Replace this class with Redis/PostGIS without touching the
service or routes.
"""

from __future__ import annotations

import time
from threading import Lock

from app.models import HistoricalProfile


class HistoryCache:
    def __init__(self, ttl_s: float, max_entries: int = 256) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._items: dict[tuple[object, ...], tuple[float, HistoricalProfile]] = {}
        self._lock = Lock()

    def get(self, key: tuple[object, ...]) -> HistoricalProfile | None:
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            stored_at, profile = entry
            if time.monotonic() - stored_at > self._ttl_s:
                self._items.pop(key, None)
                return None
            return profile

    def put(self, key: tuple[object, ...], profile: HistoricalProfile) -> None:
        with self._lock:
            if len(self._items) >= self._max_entries:
                oldest = min(self._items, key=lambda item: self._items[item][0])
                self._items.pop(oldest, None)
            self._items[key] = (time.monotonic(), profile)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
