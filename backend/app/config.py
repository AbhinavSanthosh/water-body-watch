from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Water Body Watch API"
    environment: str = "development"
    log_level: str = "info"
    api_prefix: str = "/api"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Bhashini (ULCA)
    bhashini_base_url: str = "https://meity-auth.ulcacontrib.org"
    bhashini_pipeline_id: str = "64392f96daac500b55c543cd"
    bhashini_user_id: str | None = None
    bhashini_api_key: str | None = None
    bhashini_timeout_s: float = 60.0

    # NVIDIA NIM / NeMo (OpenAI-compatible chat completions)
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_api_key: str | None = None
    nvidia_llm_model: str = "nvidia/llama-3.1-nemotron-70b-instruct"
    nvidia_timeout_s: float = 45.0

    # OpenStreetMap
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass.osm.ch/api"
    overpass_mirrors: str = (
        "https://overpass-api.de/api,"
        "https://overpass.kumi.systems/api,"
        "https://overpass.private.coffee/api"
    )
    osm_user_agent: str = "water-body-watch/0.1 (local development)"
    osm_default_radius_km: float = 5.0
    osm_max_bbox_span_deg: float = 0.20
    osm_max_results: int = 60
    osm_min_area_m2: float = 1_000.0
    overpass_request_timeout_s: float = 25.0

    # Google Earth Engine / JRC Global Surface Water (Phase 2)
    # GEE authenticates with a service account on servers; interactive auth is
    # never assumed. Without credentials the synthetic series keeps the chart,
    # LLM and Bhashini pipeline testable offline.
    gee_service_account_json: str | None = None
    gee_project: str | None = None
    jrc_collection: str = "JRC/GSW1_4/MonthlyHistory"
    jrc_water_value: int = 1
    jrc_scale_m: int = 30
    jrc_max_pixels: int = 100_000_000
    jrc_timeout_s: float = 120.0

    # Phase 2 historical profiling thresholds. These values are persisted with
    # every flag (see `HistoryFlag`) so old flags stay explainable.
    history_years: int = 10
    history_min_months: int = 24
    flag_drop_pct: float = 15.0
    history_index_version: str = "jrc-gsw-v1"
    history_cache_ttl_s: float = 21_600.0
    history_allow_synthetic: bool = True
    history_llm_enabled: bool = True

    demo_geojson_path: Path | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def overpass_urls(self) -> list[str]:
        candidates = [self.overpass_url, *self.overpass_mirrors.split(",")]
        seen: set[str] = set()
        urls: list[str] = []
        for candidate in candidates:
            url = candidate.strip().rstrip("/")
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    @property
    def bhashini_configured(self) -> bool:
        return bool(self.bhashini_user_id and self.bhashini_api_key)

    @property
    def nvidia_configured(self) -> bool:
        return bool(self.nvidia_api_key)

    @property
    def gee_configured(self) -> bool:
        return bool(self.gee_service_account_json and self.gee_project)

    @property
    def demo_file(self) -> Path:
        return self.demo_geojson_path or DATA_DIR / "demo_water_bodies.geojson"


@lru_cache
def get_settings() -> Settings:
    return Settings()
