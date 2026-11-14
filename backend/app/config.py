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

    # Sentinel Hub / Sentinel-2 L2A (Phase 3). OAuth client credentials; the
    # token is cached until shortly before expiry.
    sh_base_url: str = "https://services.sentinel-hub.com"
    sh_token_url: str = "https://services.sentinel-hub.com/oauth/token"
    sh_client_id: str | None = None
    sh_client_secret: str | None = None
    sh_collection: str = "sentinel-2-l2a"
    sh_timeout_s: float = 60.0
    live_lookback_days: int = 20
    live_max_cloud_pct: int = 60
    live_pixel_target: int = 192
    live_ndwi_threshold: float = 0.5
    live_allow_synthetic: bool = True
    live_index_version: str = "s2-ndwi-v1"

    # Phase 4 forensic thresholds. Persisted with each diagnosis so old reports
    # stay explainable.
    forensic_buffer_m: float = 100.0
    forensic_velocity_ratio_threshold: float = 1.5
    forensic_shared_ratio_threshold: float = 0.4
    forensic_straightness_threshold: float = 0.8
    forensic_ndbi_built_threshold: float = 10.0  # % of ring pixels that are built-up
    forensic_index_version: str = "forensic-v1"
    forensic_allow_synthetic: bool = True
    forensic_llm_enabled: bool = True

    # Phase 5 vision validation. The NVIDIA VLM is the default; a local
    # Moondream2/Qwen2.5-VL fallback would sit behind the same interface.
    nvidia_vlm_model: str = "meta/llama-3.2-90b-vision-instruct"
    nvidia_vlm_timeout_s: float = 8.0
    vision_llm_enabled: bool = True
    vision_allow_synthetic: bool = True
    vision_index_version: str = "vision-v1"
    vision_patch_size_px: int = 256

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
    def sentinel_hub_configured(self) -> bool:
        return bool(self.sh_client_id and self.sh_client_secret)

    @property
    def demo_file(self) -> Path:
        return self.demo_geojson_path or DATA_DIR / "demo_water_bodies.geojson"


@lru_cache
def get_settings() -> Settings:
    return Settings()
