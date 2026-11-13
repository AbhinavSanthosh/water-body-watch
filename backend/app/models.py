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
    demo_data_available: bool


class LanguageOption(BaseModel):
    code: str
    label: str


class WaterBodyMaskResponse(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: dict[str, Any]
