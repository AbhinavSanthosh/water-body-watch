from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=600)
    language: str = "en"
    speak: bool = True
    demo: bool = False


class Intent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    target_entity: Literal["water_bodies", "lakes", "ponds", "reservoirs"] = "water_bodies"
    location: str = Field(min_length=1)
    action: str = "map_and_monitor"
    radius_km: float | None = Field(default=None, ge=0.2, le=50)


class GeocodeResult(BaseModel):
    display_name: str
    lat: float
    lon: float
    bbox: tuple[float, float, float, float]  # west, south, east, north
    category: str = ""
    result_type: str = ""
    osm_type: str = ""
    osm_id: int | None = None


class WaterBodyProperties(BaseModel):
    water_body_id: str
    name: str
    area_m2: float
    area_hectares: float
    bbox: list[float]
    centroid: list[float]
    osm_type: str
    osm_id: int
    water_type: str
    tags: dict[str, str] = Field(default_factory=dict)
    source: str = "osm"
    mask_crs: str = "EPSG:4326"


class Step(BaseModel):
    name: str
    status: Literal["ok", "warning", "error"]
    detail: str


class DiscoveryResponse(BaseModel):
    query: str
    language: str
    transcript: str | None = None
    english_text: str
    intent: Intent
    location: GeocodeResult
    water_bodies: dict[str, Any]
    count: int
    confirmation_text: str
    confirmation_audio_base64: str | None = None
    confirmation_audio_mime: str | None = None
    steps: list[Step] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    environment: str
    bhashini_configured: bool
    nvidia_configured: bool
    gee_configured: bool = False
    demo_data_available: bool


class LanguageOption(BaseModel):
    code: str
    label: str


class WaterBodyMaskResponse(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any]


# --- Phase 2: historical profiling -------------------------------------------


class HistoryPoint(BaseModel):
    date: str  # first day of the month, YYYY-MM-DD
    area_m2: float | None = None
    observed: bool = True


class MonthlyClimatology(BaseModel):
    month: int
    median_area_m2: float
    min_area_m2: float
    max_area_m2: float
    samples: int


class SeasonalBaseline(BaseModel):
    window_years: int
    start_date: str
    end_date: str
    annual_median_m2: float
    survival_threshold_m2: float
    amplitude_pct: float
    drought_months: list[int]
    peak_months: list[int]
    climatology: list[MonthlyClimatology]


class TrendMetrics(BaseModel):
    slope_m2_per_year: float
    slope_pct_per_year: float
    recent_median_m2: float
    prior_median_m2: float
    permanent_change_pct: float
    months_observed: int
    months_missing: int


class HistoryFlag(BaseModel):
    water_body_id: str
    area_m2: float
    baseline_area_m2: float
    drop_pct: float
    flag: bool
    classification: Literal["normal", "seasonal_drought", "permanent_loss", "insufficient_data"]
    confidence: float
    evidence: list[str]
    evaluated_month: int
    threshold_pct: float
    index_version: str
    source: str


class TrendAnalysis(BaseModel):
    summary_en: str
    localized_summary: str | None = None
    language: str = "en"
    seasonal_pattern: str = ""
    drought_window: str = ""
    anomaly: str = ""
    confidence: float = 0.5
    evidence: list[str] = Field(default_factory=list)
    source: Literal["nvidia", "deterministic"] = "deterministic"
    audio_base64: str | None = None
    audio_mime: str | None = None


class HistoricalProfile(BaseModel):
    water_body_id: str
    name: str
    language: str
    source: str
    index_version: str
    generated_at: str
    series: list[HistoryPoint]
    baseline: SeasonalBaseline
    trend: TrendMetrics
    flag: HistoryFlag
    analysis: TrendAnalysis
    steps: list[Step] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cached: bool = False


class HistoricalMessage(BaseModel):
    type: Literal["historical"] = "historical"
    water_body_id: str
    source: str
    index_version: str
    series: list[HistoryPoint]
    baseline: SeasonalBaseline
    trend: TrendMetrics
    steps: list[Step] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FlagMessage(BaseModel):
    type: Literal["flag"] = "flag"
    water_body_id: str
    flag: HistoryFlag


class BriefingMessage(BaseModel):
    type: Literal["briefing"] = "briefing"
    water_body_id: str
    language: str
    analysis: TrendAnalysis


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    water_body_id: str
    detail: str
