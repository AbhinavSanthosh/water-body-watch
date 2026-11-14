import math
import random
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import (
    get_discovery_service,
    get_forensics_service,
    get_history_service,
    get_live_service,
)
from app.forensics.classify import classify
from app.forensics.geometry import current_water_polygon, geometry_metrics
from app.forensics.graph import build_graph
from app.forensics.metrics import recession_metrics
from app.forensics.ndbi import synthetic_ndbi
from app.forensics.service import ForensicService
from app.history.baseline import Observation, build_baseline
from app.history.jrc import shift_month
from app.history.service import HistoryService
from app.live.service import LiveService
from app.main import app
from app.services.discovery import DiscoveryService
from app.services.osm import load_demo_records
from app.services.store import store

BASE = 1_000_000.0


def _seasonal(
    *, years: int = 10, amplitude: float = 0.4, end: date = date(2026, 10, 1)
) -> list[Observation]:
    observations: list[Observation] = []
    for index in range(years * 12):
        day = shift_month(date(end.year - years + 1, 1, 1), index)
        seasonal = 1 - amplitude * 0.5 * (1 - math.cos(2 * math.pi * (day.month - 10) / 12))
        observations.append(Observation(day, BASE * seasonal))
    return observations


def test_recession_velocity_is_higher_for_abrupt_retreat() -> None:
    gentle = _seasonal()
    baseline = build_baseline(gentle)
    assert baseline is not None

    abrupt = gentle[:-3]
    abrupt.extend(
        [
            Observation(date=gentle[-3].date, area_m2=1_000_000.0),
            Observation(date=gentle[-2].date, area_m2=750_000.0),
            Observation(date=gentle[-1].date, area_m2=550_000.0),
        ]
    )

    gentle_rec = recession_metrics(gentle, baseline)
    abrupt_rec = recession_metrics(abrupt, baseline)

    assert gentle_rec.natural_velocity_m2_per_day > 0
    assert abrupt_rec.velocity_m2_per_day > gentle_rec.velocity_m2_per_day
    assert abrupt_rec.velocity_ratio > gentle_rec.velocity_ratio


def test_geometry_distinguishes_concentric_from_one_sided() -> None:
    square = {
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
    max_area = geometry_metrics(square, square).max_area_m2
    target = max_area * 0.7
    rng = random.Random(7)

    concentric = current_water_polygon(square, target, "concentric", rng)
    one_sided = current_water_polygon(square, target, "one_sided", rng)

    gm_concentric = geometry_metrics(square, concentric)
    gm_one_sided = geometry_metrics(square, one_sided)

    assert gm_concentric.shared_boundary_ratio < 0.2
    assert gm_one_sided.shared_boundary_ratio > 0.4
    assert gm_one_sided.front_straightness > 0.8
    assert gm_one_sided.asymmetry_index > gm_concentric.asymmetry_index
    assert gm_concentric.style == "concentric"
    assert gm_one_sided.style == "one_sided"


def test_synthetic_ndbi_follows_style(settings: Settings) -> None:
    record = load_demo_records(settings)[0]
    rng = random.Random(11)

    rural = synthetic_ndbi(record, settings, style="concentric", rng=rng)
    built = synthetic_ndbi(record, settings, style="one_sided", rng=rng)

    assert rural.ndbi_built_pct < 15.0
    assert built.ndbi_built_pct > rural.ndbi_built_pct
    assert built.ndbi_anomaly > rural.ndbi_anomaly
    assert rural.ring_area_m2 > 0


def test_classify_rubric(settings: Settings) -> None:
    from app.forensics.geometry import GeometryMetrics
    from app.forensics.metrics import Recession
    from app.forensics.ndbi import BufferMetrics

    fast = Recession(
        start_date="2026-03-01",
        end_date="2026-05-01",
        months=2,
        days=61,
        start_area_m2=1_000_000,
        end_area_m2=700_000,
        loss_m2=300_000,
        velocity_m2_per_day=5_000,
        natural_velocity_m2_per_day=1_000,
        velocity_ratio=5.0,
    )
    concentric = GeometryMetrics(
        max_area_m2=1_000_000,
        current_area_m2=700_000,
        shrinkage_pct=30.0,
        shared_boundary_ratio=0.05,
        front_straightness=0.3,
        asymmetry_index=0.015,
        mean_retreat_m=10.0,
        max_retreat_m=12.0,
        style="concentric",
    )
    one_sided = GeometryMetrics(
        max_area_m2=1_000_000,
        current_area_m2=700_000,
        shrinkage_pct=30.0,
        shared_boundary_ratio=0.75,
        front_straightness=0.95,
        asymmetry_index=0.71,
        mean_retreat_m=30.0,
        max_retreat_m=120.0,
        style="one_sided",
    )
    rural = BufferMetrics(
        buffer_m=100, ring_area_m2=50_000, ndbi_mean=-0.2, ndbi_built_pct=4.0, ndbi_anomaly=0.08
    )
    built = BufferMetrics(
        buffer_m=100, ring_area_m2=50_000, ndbi_mean=0.1, ndbi_built_pct=40.0, ndbi_anomaly=0.8
    )
    live = {"flag": True, "classification": "permanent_loss", "drop_pct": 22.0}

    silt = classify(fast, concentric, rural, live, settings)
    assert silt.classification == "siltation_debris"

    encroachment = classify(fast, one_sided, built, live, settings)
    assert encroachment.classification == "encroachment"

    suspected = classify(fast, one_sided, rural, live, settings)
    assert suspected.classification == "encroachment_suspected"

    slow = Recession(
        start_date="2026-03-01",
        end_date="2026-05-01",
        months=2,
        days=61,
        start_area_m2=1_000_000,
        end_area_m2=950_000,
        loss_m2=50_000,
        velocity_m2_per_day=820,
        natural_velocity_m2_per_day=1_000,
        velocity_ratio=0.82,
    )
    drought = classify(slow, concentric, rural, live, settings)
    assert drought.classification == "seasonal_drought"


async def test_graph_runs_end_to_end(settings: Settings) -> None:
    records = load_demo_records(settings)
    record = records[0]
    series = _seasonal()
    baseline = build_baseline(series)
    assert baseline is not None

    graph = build_graph(settings)
    final = await graph.ainvoke(
        {
            "water_body_id": "osm:way/1",
            "name": "Test Lake",
            "language": "en",
            "speak": False,
            "record": record,
            "series": series,
            "baseline": baseline,
            "max_geometry": record.mask_geometry,
            "target_area_m2": 300_000.0,
            "style": "concentric",
            "live_flag": {
                "flag": True,
                "classification": "permanent_loss",
                "source": "synthetic-demo",
            },
            "index_version": "forensic-v1-demo",
            "steps": [],
            "warnings": [],
        }
    )

    assert final["diagnosis"]["classification"] in {
        "seasonal_drought",
        "siltation_debris",
        "encroachment",
        "encroachment_suspected",
        "inconclusive",
    }
    assert final["report"]["report_en"]
    assert final["report"]["source"] == "deterministic"
    assert len(final["steps"]) == 5


async def test_graph_uses_nvidia_when_configured(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    records = load_demo_records(settings)
    record = records[0]
    series = _seasonal()
    baseline = build_baseline(series)
    assert baseline is not None

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"headline": "Model diagnosis", '
                                '"report_en": "Model report.", '
                                '"recommended_action": "Inspect.", '
                                '"confidence": 0.9, '
                                '"evidence": ["Evidence."]}'
                            )
                        }
                    }
                ]
            },
        )

    graph = build_graph(settings, report_transport=httpx.MockTransport(handler))
    final = await graph.ainvoke(
        {
            "water_body_id": "osm:way/1",
            "name": "Test Lake",
            "language": "en",
            "speak": False,
            "record": record,
            "series": series,
            "baseline": baseline,
            "max_geometry": record.mask_geometry,
            "target_area_m2": 300_000.0,
            "style": "concentric",
            "live_flag": {
                "flag": True,
                "classification": "permanent_loss",
                "source": "synthetic-demo",
            },
            "index_version": "forensic-v1-demo",
            "steps": [],
            "warnings": [],
        }
    )
    assert final["report"]["source"] == "nvidia"
    assert final["report"]["headline"] == "Model diagnosis"


@pytest.fixture
def client(settings: Settings) -> TestClient:
    discovery = DiscoveryService(settings, store)
    history = HistoryService(settings, store)
    live = LiveService(settings, store, history=history)
    forensics = ForensicService(settings, store, history=history, live=live)
    app.dependency_overrides[get_discovery_service] = lambda: discovery
    app.dependency_overrides[get_history_service] = lambda: history
    app.dependency_overrides[get_live_service] = lambda: live
    app.dependency_overrides[get_forensics_service] = lambda: forensics
    store.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    store.clear()


def test_forensics_endpoint_returns_diagnosis(client: TestClient) -> None:
    query = client.post(
        "/api/query",
        json={"text": "Map the lakes in Bengaluru", "language": "en", "demo": True},
    )
    assert query.status_code == 200, query.text

    response = client.get(
        "/api/water-bodies/forensics",
        params={"water_body_id": "osm:way/1", "demo": True, "speak": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["index_version"].startswith("forensic-v1")
    assert body["recession"]["natural_velocity_m2_per_day"] > 0
    assert body["geometry"]["max_area_m2"] > body["geometry"]["current_area_m2"]
    assert body["geometry"]["current_geometry"]["type"] == "Polygon"
    assert body["ndbi"]["buffer_m"] == 100.0
    assert body["diagnosis"]["classification"] in {
        "seasonal_drought",
        "siltation_debris",
        "encroachment",
        "encroachment_suspected",
        "inconclusive",
        "insufficient_data",
    }
    assert body["report"]["report_en"]
    assert body["report"]["source"] == "deterministic"


def test_forensics_websocket_streams_typed_messages(client: TestClient) -> None:
    with client.websocket_connect("/api/ws/forensics/osm:way/1?demo=true&speak=false") as socket:
        first = socket.receive_json()
        second = socket.receive_json()
        third = socket.receive_json()

    assert [first["type"], second["type"], third["type"]] == [
        "forensic",
        "diagnosis",
        "briefing",
    ]
    assert first["recession"]["velocity_m2_per_day"] >= 0
    assert second["diagnosis"]["classification"]
    assert third["report"]["report_en"]
