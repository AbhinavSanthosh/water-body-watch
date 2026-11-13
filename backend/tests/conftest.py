from pathlib import Path

import pytest

from app.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def demo_geojson() -> Path:
    return FIXTURES / "demo_water_bodies.geojson"


@pytest.fixture
def settings(demo_geojson: Path) -> Settings:
    return Settings(
        _env_file=None,
        bhashini_user_id=None,
        bhashini_api_key=None,
        nvidia_api_key=None,
        demo_geojson_path=demo_geojson,
        osm_min_area_m2=100.0,
        osm_max_results=10,
    )
