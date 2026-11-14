import random

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import (
    get_briefing_service,
    get_discovery_service,
    get_forensics_service,
    get_history_service,
    get_live_service,
)
from app.forensics.briefing import BriefingService
from app.forensics.geometry import current_water_polygon, geometry_metrics
from app.forensics.service import ForensicService
from app.forensics.vision import (
    analyze_vision,
    deterministic_inspection,
    render_patches,
)
from app.history.service import HistoryService
from app.live.service import LiveService
from app.main import app
from app.services.discovery import DiscoveryService
from app.services.store import store

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_SQUARE = {
    "type": "Polygon",
    "coordinates": [
        [
            [77.60, 12.97],
            [77.62, 12.97],
            [77.62, 12.99],
            [77.60, 12.99],
            [77.60, 12.97],
        ]
    ],
}


def test_render_patches_produce_valid_pngs(settings: Settings) -> None:
    max_area = geometry_metrics(_SQUARE, _SQUARE).max_area_m2
    current = current_water_polygon(_SQUARE, max_area * 0.7, "one_sided", random.Random(3))

    historical, current_png = render_patches(_SQUARE, current, "encroachment", settings)

    assert historical.startswith(PNG_MAGIC)
    assert current_png.startswith(PNG_MAGIC)
    assert len(historical) > 0
    assert len(current_png) > 0
    assert historical != current_png


def test_deterministic_inspection_covers_each_classification() -> None:
    encroachment = deterministic_inspection("encroachment", "the eastern bank")
    assert encroachment["markers"][0]["marker"] == "concrete_debris"
    assert encroachment["markers"][0]["severity"] == "high"
    assert encroachment["markers"][0]["location"] == "the eastern bank"

    siltation = deterministic_inspection("siltation_debris", "surface")
    assert siltation["markers"][0]["marker"] == "algal_bloom"

    drought = deterministic_inspection("seasonal_drought", "margin")
    assert drought["markers"][0]["marker"] == "exposed_lakebed"
    assert drought["markers"][0]["severity"] == "low"

    clear = deterministic_inspection("inconclusive", "n/a")
    assert clear["markers"][0]["marker"] == "clear"


async def test_analyze_vision_uses_nvidia_when_configured(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    fallback = deterministic_inspection("encroachment", "the eastern bank")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"headline": "Model vision report", '
                                '"report_en": "Model report.", "confidence": 0.9, '
                                '"markers": [{"marker": "concrete_debris", '
                                '"severity": "high", "confidence": 0.9, '
                                '"location": "east", "description": "Debris."}]}'
                            )
                        }
                    }
                ]
            },
        )

    inspection, source, warning = await analyze_vision(
        [PNG_MAGIC + b"x"], "prompt", fallback, settings, transport=httpx.MockTransport(handler)
    )
    assert source == "nvidia"
    assert warning is None
    assert inspection["headline"] == "Model vision report"


async def test_analyze_vision_malformed_output_falls_back(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    fallback = deterministic_inspection("encroachment", "the eastern bank")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "nope"}}]})

    inspection, source, warning = await analyze_vision(
        [PNG_MAGIC + b"x"], "prompt", fallback, settings, transport=httpx.MockTransport(handler)
    )
    assert source == "deterministic"
    assert warning is not None
    assert inspection["markers"][0]["marker"] == "concrete_debris"


@pytest.fixture
def client(settings: Settings) -> TestClient:
    discovery = DiscoveryService(settings, store)
    history = HistoryService(settings, store)
    live = LiveService(settings, store, history=history)
    forensics = ForensicService(settings, store, history=history, live=live)
    briefing = BriefingService(settings, store, forensics=forensics)
    app.dependency_overrides[get_discovery_service] = lambda: discovery
    app.dependency_overrides[get_history_service] = lambda: history
    app.dependency_overrides[get_live_service] = lambda: live
    app.dependency_overrides[get_forensics_service] = lambda: forensics
    app.dependency_overrides[get_briefing_service] = lambda: briefing
    store.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    store.clear()


def test_vision_endpoint_returns_patches_and_inspection(client: TestClient) -> None:
    query = client.post(
        "/api/query",
        json={"text": "Map the lakes in Bengaluru", "language": "en", "demo": True},
    )
    assert query.status_code == 200, query.text

    response = client.get(
        "/api/water-bodies/vision",
        params={"water_body_id": "osm:way/1", "demo": True, "speak": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["index_version"].startswith("vision-v1")
    assert len(body["patches"]) == 2
    assert body["patches"][0]["role"] == "historical"
    assert body["patches"][1]["role"] == "current"
    assert body["patches"][0]["image_base64"].startswith("iVBOR")  # base64 PNG
    assert body["inspection"]["headline"]
    assert body["inspection"]["source"] == "deterministic"
    assert len(body["inspection"]["markers"]) >= 1
    assert body["diagnosis"]["classification"]


def test_vision_websocket_streams_typed_messages(client: TestClient) -> None:
    with client.websocket_connect("/api/ws/vision/osm:way/1?demo=true&speak=false") as socket:
        first = socket.receive_json()
        second = socket.receive_json()

    assert [first["type"], second["type"]] == ["vision", "briefing"]
    assert len(first["patches"]) == 2
    assert second["inspection"]["report_en"]
    assert first["water_body_id"] == "osm:way/1"
