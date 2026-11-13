"""Overpass HTTP client with mirror racing.

The public Overpass instances are often saturated. Racing the configured
mirrors and accepting the first non-empty response keeps discovery responsive
instead of waiting through every mirror's timeout in sequence.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class OverpassError(RuntimeError):
    pass


class _EmptyOverpassResponse(OverpassError):
    pass


def build_query(bbox: tuple[float, float, float, float], timeout_s: int) -> str:
    west, south, east, north = bbox
    bbox_text = f"{south},{west},{north},{east}"
    return (
        f"[out:json][timeout:{timeout_s}];"
        "("
        f'way["natural"="water"]({bbox_text});'
        f'relation["natural"="water"]({bbox_text});'
        f'way["landuse"~"^(reservoir|basin)$"]({bbox_text});'
        f'relation["landuse"~"^(reservoir|basin)$"]({bbox_text});'
        ");"
        "out geom;"
    )


async def _query_mirror(
    mirror: str,
    query: str,
    settings: Settings,
    timeout_s: float,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        response = await client.post(
            f"{mirror.rstrip('/')}/interpreter",
            data={"data": query},
            headers={"User-Agent": settings.osm_user_agent},
        )
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict) or "elements" not in payload:
        raise OverpassError(f"{mirror} returned an unexpected payload")
    if not payload["elements"]:
        raise _EmptyOverpassResponse(f"{mirror} returned no elements")
    return payload


async def query_overpass(
    bbox: tuple[float, float, float, float], settings: Settings
) -> dict[str, Any]:
    timeout_s = int(settings.overpass_request_timeout_s)
    query = build_query(bbox, timeout_s)
    mirrors = settings.overpass_urls
    tasks = {
        asyncio.create_task(_query_mirror(mirror, query, settings, timeout_s + 5))
        for mirror in mirrors
    }
    errors: list[str] = []

    try:
        while tasks:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            tasks = pending
            for task in done:
                try:
                    payload = await task
                    for remaining in tasks:
                        remaining.cancel()
                    return payload
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
                    logger.warning("Overpass attempt failed: %s", exc)
    finally:
        for task in tasks:
            task.cancel()

    raise OverpassError("All Overpass mirrors failed: " + " | ".join(errors))
