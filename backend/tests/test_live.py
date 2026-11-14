from datetime import date

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import get_discovery_service, get_history_service, get_live_service
from app.geo.indices import ndwi, pixel_area_m2
from app.history.service import HistoryService
from app.live.alert import analyze_live, deterministic_alert
from app.live.sentinel import scene_size, synthetic_scene
from app.live.service import LiveService
from app.main import app
from app.services.discovery import DiscoveryService
from app.services.osm import load_demo_records
from app.services.store import store


def _payload(**overrides: object) -> dict:
    base = {
        "lake": "Test Lake",
        "area_m2": 200_000.0,
        "baseline_area_m2": 400_000.0,
        "drop_pct": 50.0,
        "flag": True,
        "classification": "seasonal_drought",
        "threshold_pct": 15.0,
        "month_name": "November",
        "ndwi_threshold": 0.5,
        "cloud_pct": 8.0,
        "water_pixels": 2000,
        "valid_pixels": 2400,
        "pixel_area_m2": 100.0,
        "scene_source": "synthetic-demo",
        "observed_at": "2026-11-13",
        "confidence": 0.8,
        "permanent_change_pct": -2.0,
    }
    base.update(overrides)
    return base


def test_synthetic_scene_hits_requested_area_and_stays_inside(
    settings: Settings,
) -> None:
    record = load_demo_records(settings)[0]
    target = 250_000.0
    scene = synthetic_scene(record, settings, target_area_m2=target, today=date(2026, 11, 13))

    assert scene.source == "synthetic-demo"
    assert scene.b03.shape == (scene.height, scene.width)
    assert scene.scl.shape == scene.b03.shape

    values = ndwi(scene.b03, scene.b08)
    cell_area = pixel_area_m2(scene.bounds, scene.width, scene.height)
    water_pixels = int(np.nansum(values > 0.5))
    assert water_pixels * cell_area == pytest.approx(target, rel=0.08)

    # All water must sit inside the polygon; NaN/cloud pixels are excluded.
    water = np.isfinite(values) & (values > 0.5)
    assert water.sum() > 0
    assert np.isfinite(scene.scl).all()

    again = synthetic_scene(record, settings, target_area_m2=target, today=date(2026, 11, 13))
    assert np.array_equal(np.nan_to_num(scene.b03), np.nan_to_num(again.b03))


def test_scene_size_keeps_pixels_square() -> None:
    width, height = scene_size((77.60, 12.97, 77.64, 12.99), 200)
    assert width == 200
    assert height < width
    assert height >= 32


def test_deterministic_alert_severity_follows_flag() -> None:
    critical = deterministic_alert(_payload())
    assert critical["severity"] == "critical"
    assert "Test Lake" in critical["headline"]
    assert "inspection" in critical["recommended_action"].lower()

    permanent = deterministic_alert(
        _payload(classification="permanent_loss", permanent_change_pct=-12.0)
    )
    assert permanent["severity"] == "critical"
    assert "permanently" in permanent["headline"]

    normal = deterministic_alert(
        _payload(
            flag=False,
            classification="normal",
            drop_pct=-3.0,
            area_m2=410_000.0,
        )
    )
    assert normal["severity"] == "normal"
    assert "No action required" in normal["recommended_action"]

    provisional = deterministic_alert(_payload(classification="insufficient_data"))
    assert provisional["severity"] == "watch"


async def test_nvidia_alert_is_used_when_valid(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"headline": "Model headline", '
                                '"summary_en": "Model alert text.", '
                                '"recommended_action": "Inspect the bund.", '
                                '"severity": "critical", "confidence": 0.9, '
                                '"evidence": ["50% below baseline."]}'
                            )
                        }
                    }
                ]
            },
        )

    alert, source, warning = await analyze_live(
        _payload(), settings, transport=httpx.MockTransport(handler)
    )
    assert source == "nvidia"
    assert warning is None
    assert alert["headline"] == "Model headline"


async def test_nvidia_alert_malformed_output_falls_back(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "nope"}}]})

    alert, source, warning = await analyze_live(
        _payload(), settings, transport=httpx.MockTransport(handler)
    )
    assert source == "deterministic"
    assert warning is not None
    assert alert["severity"] == "critical"


@pytest.fixture
def client(settings: Settings) -> TestClient:
    discovery = DiscoveryService(settings, store)
    history = HistoryService(settings, store)
    live = LiveService(settings, store, history=history)
    app.dependency_overrides[get_discovery_service] = lambda: discovery
    app.dependency_overrides[get_history_service] = lambda: history
    app.dependency_overrides[get_live_service] = lambda: live
    store.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    store.clear()


def test_live_endpoint_returns_ndwi_flag_and_alert(client: TestClient, settings: Settings) -> None:
    query = client.post(
        "/api/query",
        json={"text": "Map the lakes in Bengaluru", "language": "en", "demo": True},
    )
    assert query.status_code == 200, query.text

    response = client.get(
        "/api/water-bodies/live",
        params={"water_body_id": "osm:way/1", "demo": True, "speak": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["source"] == "synthetic-demo"
    assert body["index_version"].startswith(settings.live_index_version)
    assert body["scene"]["cloud_pct"] >= 0
    assert body["scene"]["valid_pixel_pct"] > 0
    assert body["flag"]["area_m2"] > 0
    assert body["flag"]["ndwi_threshold"] == settings.live_ndwi_threshold
    assert body["flag"]["water_pixels"] > 0
    assert body["flag"]["threshold_pct"] == settings.flag_drop_pct
    assert body["flag"]["classification"] in {
        "normal",
        "seasonal_drought",
        "permanent_loss",
        "insufficient_data",
    }
    assert body["alert"]["headline"]
    assert body["alert"]["source"] == "deterministic"
    assert body["alert"]["severity"] in {"normal", "watch", "critical"}
    assert len(body["baseline"]["climatology"]) == 12

    cached = client.get(
        "/api/water-bodies/live",
        params={"water_body_id": "osm:way/1", "demo": True, "speak": False},
    )
    assert cached.status_code == 200
    assert cached.json()["cached"] is True


def test_live_endpoint_unknown_lake_returns_404(client: TestClient) -> None:
    response = client.get(
        "/api/water-bodies/live",
        params={"water_body_id": "osm:way/999", "demo": True},
    )
    assert response.status_code == 404


def test_live_websocket_streams_live_flag_alert(client: TestClient) -> None:
    with client.websocket_connect("/api/ws/live/osm:way/1?demo=true&speak=false") as socket:
        first = socket.receive_json()
        second = socket.receive_json()
        third = socket.receive_json()

    assert [first["type"], second["type"], third["type"]] == ["live", "flag", "alert"]
    assert first["scene"]["width"] > 0
    assert second["flag"]["area_m2"] > 0
    assert third["alert"]["headline"]
    for message in (first, second, third):
        assert message["water_body_id"] == "osm:way/1"
