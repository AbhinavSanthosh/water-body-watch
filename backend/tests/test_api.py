from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import get_discovery_service
from app.main import app
from app.services.discovery import DiscoveryService
from app.services.store import store


@pytest.fixture
def client(settings: Settings):
    service = DiscoveryService(settings, store)
    app.dependency_overrides[get_discovery_service] = lambda: service
    store.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    store.clear()


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_languages(client: TestClient) -> None:
    response = client.get("/api/languages")
    assert response.status_code == 200
    assert any(item["code"] == "kn" for item in response.json())


def test_demo_query_returns_masks(client: TestClient) -> None:
    response = client.post(
        "/api/query",
        json={"text": "Map the lakes in Bengaluru", "language": "en", "demo": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    assert body["intent"]["location"] == "Bengaluru"
    feature = body["water_bodies"]["features"][0]
    assert feature["properties"]["water_body_id"] == "osm:way/1"
    assert feature["properties"]["mask_crs"] == "EPSG:4326"
    assert body["confirmation_audio_base64"] is None

    mask = client.get("/api/water-bodies/mask", params={"water_body_id": "osm:way/1"})
    assert mask.status_code == 200
    assert mask.json()["type"] == "Feature"

    detail = client.get("/api/water-bodies/detail", params={"water_body_id": "osm:way/1"})
    assert detail.status_code == 200
    assert detail.json()["properties"]["name"] == "Test Lake"


def test_unknown_water_body_returns_404(client: TestClient) -> None:
    response = client.get("/api/water-bodies/mask", params={"water_body_id": "osm:way/999"})
    assert response.status_code == 404


def test_unsupported_language_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/query",
        json={"text": "Map lakes", "language": "xx", "demo": True},
    )
    assert response.status_code == 422


def test_voice_without_bhashini_returns_503(client: TestClient) -> None:
    response = client.post(
        "/api/voice/query",
        data={"language": "kn"},
        files={"audio": ("query.wav", b"RIFF", "audio/wav")},
    )
    assert response.status_code == 503


def test_demo_fixture_exists() -> None:
    fixture = Path(__file__).parent / "fixtures" / "demo_water_bodies.geojson"
    assert fixture.exists()
