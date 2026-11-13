from datetime import date
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.deps import get_discovery_service, get_history_service
from app.history.analysis import analyze_history, deterministic_analysis, metrics_payload
from app.history.baseline import (
    Observation,
    build_baseline,
    build_trend,
    evaluate_flag,
)
from app.history.jrc import last_complete_month, shift_month, synthetic_series
from app.history.service import HistoryService
from app.main import app
from app.services.discovery import DiscoveryService
from app.services.osm import WaterBodyRecord, load_demo_records
from app.services.store import store

BASE_AREA = 1_000_000.0


def _series(
    *,
    years: int = 10,
    amplitude: float = 0.4,
    trend_pct_per_year: float = 0.0,
    end: date = date(2026, 10, 1),
) -> list[Observation]:
    """Post-monsoon peak in October, dry-season low in April."""

    import math

    observations: list[Observation] = []
    for index in range(years * 12):
        day = shift_month(date(end.year - years + 1, 1, 1), index)
        seasonal = 1 - amplitude * 0.5 * (1 - math.cos(2 * math.pi * (day.month - 10) / 12))
        trend = 1 + trend_pct_per_year / 100 * (index / 12)
        observations.append(Observation(day, BASE_AREA * seasonal * trend))
    return observations


def test_climatology_uses_monthly_medians_not_annual_mean() -> None:
    baseline = build_baseline(_series(), years=10)
    assert baseline is not None
    assert len(baseline.climatology) == 12
    october = baseline.month_median(10)
    april = baseline.month_median(4)
    assert october > april
    assert baseline.survival_threshold_m2 == pytest.approx(april, rel=0.02)
    assert 10 in baseline.peak_months
    assert 4 in baseline.drought_months
    assert baseline.amplitude_pct == pytest.approx(40.0, abs=2.0)
    assert baseline.window_years == 10


def test_dry_season_low_is_not_flagged() -> None:
    observations = _series()
    baseline = build_baseline(observations)
    assert baseline is not None

    # A normal April reading is far below the annual mean but matches its own
    # calendar-month baseline, so it must not raise a flag.
    april = next(item for item in observations if item.date.month == 4)
    result = evaluate_flag(observations, baseline, current=april, drop_threshold_pct=15.0)
    assert result.drop_pct == pytest.approx(0.0, abs=1.0)
    assert result.flag is False
    assert result.classification == "normal"


def test_same_month_drop_above_threshold_is_flagged() -> None:
    observations = _series()
    baseline = build_baseline(observations)
    assert baseline is not None

    last = observations[-1]
    assert last.area_m2 is not None
    stressed = Observation(last.date, last.area_m2 * 0.5)
    observations = [*observations[:-1], stressed]

    result = evaluate_flag(observations, baseline, current=stressed, drop_threshold_pct=15.0)
    assert result.drop_pct == pytest.approx(50.0, abs=1.0)
    assert result.flag is True
    assert result.classification == "seasonal_drought"
    assert result.baseline_area_m2 == pytest.approx(
        baseline.month_median(last.date.month), rel=0.01
    )
    assert 0.0 < result.confidence <= 0.95


def test_small_drop_stays_below_threshold() -> None:
    observations = _series()
    baseline = build_baseline(observations)
    assert baseline is not None
    last = observations[-1]
    mild = Observation(last.date, last.area_m2 * 0.9)
    result = evaluate_flag(
        [*observations[:-1], mild], baseline, current=mild, drop_threshold_pct=15.0
    )
    assert result.drop_pct == pytest.approx(10.0, abs=1.0)
    assert result.flag is False


def test_multiyear_decline_is_permanent_loss() -> None:
    observations = _series(trend_pct_per_year=-4.0)
    baseline = build_baseline(observations)
    assert baseline is not None
    last = observations[-1]
    stressed = Observation(last.date, last.area_m2 * 0.5)
    observations = [*observations[:-1], stressed]

    result = evaluate_flag(observations, baseline, current=stressed, drop_threshold_pct=15.0)
    assert result.flag is True
    assert result.classification == "permanent_loss"
    trend = build_trend(observations)
    assert trend.slope_m2_per_year < 0
    assert trend.permanent_change_pct < -7.5


def test_short_history_is_insufficient_data() -> None:
    observations = _series(years=1)
    baseline = build_baseline(observations)
    assert baseline is not None
    result = evaluate_flag(observations, baseline, current=observations[-1], min_months=24)
    assert result.classification == "insufficient_data"
    assert result.flag is False


def test_synthetic_series_is_deterministic_and_complete(settings: Settings) -> None:
    records = load_demo_records(settings)
    record = records[0]
    first = synthetic_series(record, settings, end=date(2026, 10, 1))
    second = synthetic_series(record, settings, end=date(2026, 10, 1))

    assert len(first) == settings.history_years * 12
    assert [item.area_m2 for item in first] == [item.area_m2 for item in second]
    assert all(item.area_m2 is None or item.area_m2 > 0 for item in first)
    assert first[-1].observed is True

    other = WaterBodyRecord(
        feature={
            **record.feature,
            "properties": {**record.feature["properties"], "water_body_id": "osm:way/2"},
        },
        mask_geometry=record.mask_geometry,
    )
    assert [item.area_m2 for item in synthetic_series(other, settings)] != [
        item.area_m2 for item in first
    ]


def test_last_complete_month_and_shift() -> None:
    assert last_complete_month(date(2026, 1, 15)) == date(2025, 12, 1)
    assert last_complete_month(date(2026, 11, 13)) == date(2026, 10, 1)
    assert shift_month(date(2026, 1, 1), -1) == date(2025, 12, 1)
    assert shift_month(date(2026, 12, 1), 1) == date(2027, 1, 1)


def _metrics(settings: Settings) -> dict:
    observations = _series()
    baseline = build_baseline(observations)
    assert baseline is not None
    trend = build_trend(observations)
    last = observations[-1]
    flag = evaluate_flag(observations, baseline, current=last, drop_threshold_pct=15.0)
    return metrics_payload(
        name="Test Lake",
        baseline=baseline,
        trend=trend,
        flag=flag,
        current_area_m2=flag.area_m2,
        current_date=last.date.isoformat(),
        drop_threshold_pct=settings.flag_drop_pct,
    )


def test_deterministic_analysis_cites_computed_numbers(settings: Settings) -> None:
    payload = _metrics(settings)
    analysis = deterministic_analysis(payload)
    assert "Test Lake" in analysis["summary_en"]
    assert "October" in analysis["summary_en"]
    assert any("baseline" in item.lower() for item in analysis["evidence"])
    assert 0.0 <= analysis["confidence"] <= 1.0


async def test_nvidia_analysis_is_used_when_valid(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    payload = _metrics(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"summary_en": "Model summary.", '
                                '"seasonal_pattern": "Peaks in October.", '
                                '"drought_window": "Lows in April.", '
                                '"anomaly": "Stable.", "confidence": 0.8, '
                                '"evidence": ["Same-month baseline is stable."]}'
                            )
                        }
                    }
                ]
            },
        )

    analysis, source, warning = await analyze_history(
        payload, settings, transport=httpx.MockTransport(handler)
    )
    assert source == "nvidia"
    assert warning is None
    assert analysis["summary_en"] == "Model summary."
    assert analysis["confidence"] == 0.8


async def test_nvidia_malformed_output_falls_back(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "nvidia_api_key", "test-key")
    payload = _metrics(settings)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    analysis, source, warning = await analyze_history(
        payload, settings, transport=httpx.MockTransport(handler)
    )
    assert source == "deterministic"
    assert warning is not None
    assert "Test Lake" in analysis["summary_en"]


@pytest.fixture
def client(settings: Settings) -> TestClient:
    discovery = DiscoveryService(settings, store)
    history = HistoryService(settings, store)
    app.dependency_overrides[get_discovery_service] = lambda: discovery
    app.dependency_overrides[get_history_service] = lambda: history
    store.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    store.clear()


def test_history_endpoint_returns_baseline_and_briefing(
    client: TestClient, settings: Settings
) -> None:
    query = client.post(
        "/api/query",
        json={"text": "Map the lakes in Bengaluru", "language": "en", "demo": True},
    )
    assert query.status_code == 200, query.text

    response = client.get(
        "/api/water-bodies/history",
        params={"water_body_id": "osm:way/1", "demo": True, "speak": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["water_body_id"] == "osm:way/1"
    assert body["name"] == "Test Lake"
    assert body["source"] == "synthetic-demo"
    assert body["index_version"].endswith("-demo")
    assert len(body["series"]) == settings.history_years * 12
    assert len(body["baseline"]["climatology"]) == 12
    assert body["baseline"]["window_years"] == settings.history_years
    assert body["flag"]["threshold_pct"] == settings.flag_drop_pct
    assert body["flag"]["classification"] in {
        "normal",
        "seasonal_drought",
        "permanent_loss",
        "insufficient_data",
    }
    assert body["analysis"]["source"] == "deterministic"
    assert body["analysis"]["summary_en"]
    assert body["cached"] is False
    assert any("synthetic" in warning.lower() for warning in body["warnings"])

    cached = client.get(
        "/api/water-bodies/history",
        params={"water_body_id": "osm:way/1", "demo": True, "speak": False},
    )
    assert cached.status_code == 200
    assert cached.json()["cached"] is True


def test_history_endpoint_unknown_lake_returns_404(client: TestClient) -> None:
    response = client.get(
        "/api/water-bodies/history",
        params={"water_body_id": "osm:way/999", "demo": True},
    )
    assert response.status_code == 404


def test_history_endpoint_rejects_bad_language(client: TestClient) -> None:
    response = client.get(
        "/api/water-bodies/history",
        params={"water_body_id": "osm:way/1", "demo": True, "language": "xx"},
    )
    assert response.status_code == 422


def test_history_websocket_streams_typed_messages(client: TestClient) -> None:
    with client.websocket_connect("/api/ws/history/osm:way/1?demo=true&speak=false") as socket:
        first = socket.receive_json()
        second = socket.receive_json()
        third = socket.receive_json()

    assert [first["type"], second["type"], third["type"]] == [
        "historical",
        "flag",
        "briefing",
    ]
    for message in (first, second, third):
        assert message["water_body_id"] == "osm:way/1"
    assert len(first["series"]) > 0
    assert second["flag"]["index_version"].endswith("-demo")
    assert third["analysis"]["summary_en"]


def test_history_fixture_path(settings: Settings) -> None:
    assert Path(settings.demo_file).exists()
