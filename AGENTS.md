# AGENTS.md

## Status

- Branch `niha`. **Phases 1-5 are implemented**: multilingual discovery, OSM lakebed masks, JRC seasonal baselines, live Sentinel-2 NDWI checks, LangGraph forensic diagnosis, VLM visual validation (NVIDIA VLM with deterministic fallback), and multilingual alerts.
- Backend: FastAPI on Python 3.12 (`backend/`), 73 unit tests passing. Frontend: SvelteKit + Svelte 5 + Mapbox GL + Chart.js (`frontend/`), `pnpm check` and `pnpm build` clean.
- Docker Compose orchestration is in place for both backend and frontend, with source bind-mounts for hot reload.
- Product: municipal dashboard that finds urban lakes/ponds, baselines their historical surface area, flags abnormal shrinkage, and produces a forensic + visual briefing.
- Keep this file current when conventions, commands, or module ownership change.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Service and provider configuration status |
| `GET /api/languages` | Bhashini-supported language list |
| `POST /api/query` | Text query: intent -> geocode -> OSM -> masks -> optional TTS |
| `POST /api/voice/query` | WAV upload: Bhashini ASR + translation, then the same pipeline |
| `GET /api/water-bodies/mask` | Full-resolution lakebed mask for a discovered water body |
| `GET /api/water-bodies/detail` | Feature properties for the click panel |
| `GET /api/water-bodies/history` | Phase 2: monthly series, seasonal baseline, flag, trend briefing |
| `WS /api/ws/history/{water_body_id}` | Phase 2 stream: `historical`, `flag`, `briefing` |
| `GET /api/water-bodies/live` | Phase 3: Sentinel-2 NDWI area, live flag, alert |
| `WS /api/ws/live/{water_body_id}` | Phase 3 stream: `live`, `flag`, `alert` |
| `GET /api/water-bodies/forensics` | Phase 4: LangGraph forensic diagnosis |
| `WS /api/ws/forensics/{water_body_id}` | Phase 4 stream: `forensic`, `diagnosis`, `briefing` |
| `GET /api/water-bodies/vision` | Phase 5: RGB patches + VLM inspection + briefing |
| `WS /api/ws/vision/{water_body_id}` | Phase 5 stream: `vision`, `briefing` |

`POST /api/query` accepts `{"text", "language", "speak", "demo"}`. `demo=true` loads the bundled Bengaluru OSM fixture (`backend/app/data/demo_water_bodies.geojson`) and skips geocoding, which keeps the UI usable offline and without provider keys. The history and live endpoints accept `demo=true` too and load that fixture on demand. Water-body IDs contain `/` (`osm:way/123`), so WebSocket routes use `{water_body_id:path}`.

Every provider has an offline path so the UI works without keys: no NVIDIA key -> deterministic intent/narration; no Bhashini key -> text-only; no GEE -> deterministic synthetic JRC-shaped series; no Sentinel Hub -> deterministic synthetic Sentinel-2 scene. Synthetic results are always labelled `synthetic-demo` and warned about in the response; never present them as real satellite data.

## Pipeline and module ownership

Create code in these areas; keep index math and external I/O separate so it can be tested without network access.

| Phase | Responsibility | Location |
|---|---|---|
| 1 | OSM/Overpass polygon ingest, geometry cleanup, map API | `backend/app/services/{overpass,osm,discovery}.py`, `backend/app/api/routes.py` |
| 2 | JRC Global Surface Water baseline, area-over-time series, trend narration | `backend/app/history/{jrc,baseline,analysis,service,cache}.py` |
| 3 | Sentinel-2 fetch, NDWI, live flag, alert narration | `backend/app/live/{sentinel,alert,service}.py`, `backend/app/geo/{indices,raster}.py` |
| 4 | LangGraph forensic graph: recession velocity, asymmetry, 100 m buffer/NDBI | `backend/app/forensics/{metrics,geometry,ndbi,classify,report,graph,service}.py` |
| 5 | RGB patch extraction, VLM inspection, briefing assembly | `backend/app/forensics/{vision,briefing}.py` |
| UI | Map, click panel, area chart, live pulse, briefing | `frontend/src/lib/components/{MapView,HistoryPanel,LivePanel,ForensicsPanel,VisionPanel}.svelte`, `frontend/src/lib/charts/AreaChart.svelte` |

Provider clients live in `backend/app/services/`: `bhashini.py` (ASR/translation/TTS), `intent.py` (NVIDIA NIM + deterministic fallback), `overpass.py` (mirror racing), and `store.py` (in-memory repository boundary). Phase 2/3 orchestration lives in `backend/app/history/service.py` and `backend/app/live/service.py`; both emit typed WebSocket messages and expose the same data over REST.

Data flow: map load -> REST polygon snapshot -> Phase 2 history (REST or WS: `historical` -> `flag` -> `briefing`) -> Phase 3 live check (WS: `live` -> `flag` -> `alert`) -> map pulse + spoken alert. The UI must render the last good snapshot on WS reconnect; do not make the map depend on a live socket.

Phase 4 is a real LangGraph `StateGraph` (`forensics/graph.py`) with nodes `velocity -> geometry -> ndbi -> classify -> narrate -> localize`. Phase 5 is a second `StateGraph` (`forensics/briefing.py`) with `extract -> inspect -> localize` that renders before/after RGB patches (Pillow), runs a VLM, and localizes the report. The deterministic nodes compute numbers; narration/vision nodes write text from them. Build the graphs with closures over settings and optional fake transports so the deterministic core stays testable offline. Phases 2/3 remain plain typed async state machines.

## Domain rules (do not improvise these)

- **NDWI** = `(B03 - B08) / (B03 + B08)`; active water is `NDWI > 0.5`. B03 = green, B08 = NIR.
- **Flag threshold**: current area more than 15% below the baseline. Compare against the **same calendar month** baseline (median of that month across the 10-year window), not the annual mean, or normal dry-season evaporation will trigger false alarms.
- **JRC baseline** is 30 m Landsat-derived occurrence. `history/baseline.py` builds a monthly climatology (median per calendar month) and uses the lowest monthly median as the lake's **survival threshold**. The flag compares the live NDWI area with the same-month climatology, never with the annual mean. Never compare raw JRC pixel counts to 10 m Sentinel-2 pixel counts.
- **Two-stage decision**: Phase 2 decides the seasonal baseline and multi-year trend; Phase 3 computes live NDWI and compares it to that baseline. `classification` is deterministic: `permanent_loss` when flagged and the recent 3-year median is down more than half the threshold, `seasonal_drought` when flagged but the multi-year median is stable, `normal` otherwise, `insufficient_data` when coverage is thin.
- **LLM narration only**: NVIDIA NeMo writes summary/alert/report text from numbers that were already computed. It cannot set or clear a flag or a diagnosis. Both the deterministic metrics and the narration source (`nvidia` or `deterministic`) are stored in every payload.
- **Forensic rubric** (`forensics/classify.py`): fast + concentric retreat -> `siltation_debris`; straight one-sided cut + concrete spike -> `encroachment`; straight cut without concrete -> `encroachment_suspected`; slow + concentric -> `seasonal_drought`; mixed -> `inconclusive`. The asymmetry index is `shared_boundary_ratio × front_straightness`, both computed in a local UTM CRS from the current NDWI boundary vs the OSM maximum boundary.
- **NDBI** = `(B11 - B08) / (B11 + B08)` over the 100 m buffer ring (GeoPandas/shapely buffer minus the lake). Positive NDBI is built-up; `ndbi_built_pct` is the share of ring pixels above zero. SWIR1 = B11.
- **Retreat metrics are computed deterministically first** (m²/day, %/month, per-shoreline-segment retreat, straight-edge score). The LLM classifies against an explicit rubric (e.g. concentric = seasonal drought, straight one-sided cut = encroachment, accelerated drop = siltation/debris). Do not let free-form LLM output override the numbers; store both.
- **VLM is supporting evidence, not the trigger.** Only flag from NDWI/JRC metrics. VLM output must be structured JSON with confidence and must cite what is visible. Phase 5 `analyze_vision` uses `NVIDIA_VLM_MODEL` when configured; otherwise it falls back to the deterministic inspection built from the Phase 4 classification and rendered markers.
- Thresholds and rubric constants live in one config module; persist the values/version used with every flag so old flags remain explainable.

## Geospatial gotchas

- Store GeoJSON in **EPSG:4326** with RFC 7946 `[longitude, latitude]` order. Compute areas in a local UTM CRS chosen from the polygon centroid (EPSG:326xx/327xx). Never compute area in 4326.
- Use the **polygon mask**, not its bounding box, for pixel counts. Bboxes are only for tile/request extents.
- Use Overpass API for OSM polygons (`natural=water`, `water=lake|pond|reservoir`), not the main OSM editing API. Set a descriptive `User-Agent` and back off on 429/504.
- Public Overpass instances are unreliable. `overpass.py` races every URL in `OVERPASS_URL` + `OVERPASS_MIRRORS` and accepts the first non-empty response. If all fail, `osm.py` falls back to throttled Nominatim named-lake search (1 req/s, `polygon_geojson=1`, bounded viewbox); surface that fallback as a warning because unnamed ponds will be missing. `overpass.osm.ch` is fast but often has no data outside Switzerland.
- Sentinel-2: use L2A surface reflectance. Mask clouds/shadows/cirrus/snow via the SCL band before computing indices. Do not mix scaled integer reflectance with float reflectance.
- Phase 3 requests B03/B08/SCL as float32 GeoTIFF from the Sentinel Hub Process API, parses it with `tifffile`, and stores arrays with **row 0 = north**. `geo/raster.py` rasterizes the lakebed polygon on that same grid so pixel counts never use the bbox.
- Pixel area comes from `geo/indices.py:pixel_area_m2`, which transforms the bbox corners into the local UTM zone. Never multiply a pixel count by 100 m² (10 m Sentinel-2) without checking the requested raster resolution.
- Simplify polygons for Mapbox display, but keep full-resolution geometry for area math.
- Stable water-body ID = OSM type + ID (e.g. `osm:way/123456`), never an array index or display name.
- Cache expensive results by `(water_body_id, date, index_version, source)`; bump `index_version` when thresholds or band math change.

## External services

- **NVIDIA API (NeMo/NIM, OpenAI-compatible)** is the default for both LangGraph LLM nodes and the VLM. Keep model IDs in env, not in code. When NVIDIA is unavailable the deterministic narration/vision fallback is used.
- **Bhashini** handles both translation and speech for briefings/UI. Call it asynchronously after the English briefing is assembled; never block the forensic response on it.
- **Google Earth Engine** authenticates with a service account in server environments; never rely on interactive `earthengine authenticate` on a server. It needs the optional `gee` extra (`uv sync --extra gee`) plus `GEE_SERVICE_ACCOUNT_JSON` and `GEE_PROJECT`; without it the history series is synthetic.
- **Sentinel Hub** uses OAuth client credentials (`SH_CLIENT_ID`/`SH_CLIENT_SECRET`); the token is cached until shortly before expiry. The Process API is queried over the lake bbox with `mosaickingOrder: leastCC` and `maxCloudCoverage`. No cloud-free scene in the window -> `SentinelHubError`, which falls back to the synthetic scene when `LIVE_ALLOW_SYNTHETIC=true`.
- All secrets stay server-side. The frontend may only receive `PUBLIC_MAPBOX_TOKEN` (restrict it by URL/scopes); never expose GEE, Sentinel Hub, NVIDIA, or Bhashini keys via `VITE_`/`PUBLIC_` vars.
- Expected env names: `GEE_SERVICE_ACCOUNT_JSON`, `GEE_PROJECT`, `JRC_COLLECTION`, `HISTORY_YEARS`, `HISTORY_MIN_MONTHS`, `FLAG_DROP_PCT`, `SH_CLIENT_ID`, `SH_CLIENT_SECRET`, `SH_COLLECTION`, `LIVE_LOOKBACK_DAYS`, `LIVE_MAX_CLOUD_PCT`, `LIVE_NDWI_THRESHOLD`, `NVIDIA_API_KEY`, `NVIDIA_LLM_MODEL`, `NVIDIA_VLM_MODEL`, `BHASHINI_API_KEY`, `BHASHINI_USER_ID`, `OVERPASS_URL`, `OVERPASS_MIRRORS`, `OVERPASS_REQUEST_TIMEOUT_S`, `PUBLIC_MAPBOX_TOKEN`.
- Bhashini is a two-call ULCA flow: fetch the pipeline config (callback URL, auth header, service IDs), then call the compute endpoint. Cache the config for about an hour; it is the slow half of the voice round trip.
- Without `NVIDIA_API_KEY` the deterministic `fallback_extract` parser runs. Without Bhashini keys, voice input returns 503 and confirmation audio is skipped; text discovery still works.
- NVIDIA model IDs are account- and catalog-dependent. A `410 Gone` means the model was retired; a `404 ... not found for account` means the key cannot use that function. Verify with `GET {NVIDIA_BASE_URL}/models` and keep the working ID in `NVIDIA_LLM_MODEL`, never hard-coded.
- Bhashini may be blocked by a campus/corporate web filter (it answers `303` to a proxy block page). `bhashini.py` reports the status and redirect target instead of an empty error; if you see that, try a different network or ask the network owner to allow `meity-auth.ulcacontrib.org`.

## API and data contracts

- GeoJSON `FeatureCollection` for map payloads; areas in **m²** as `*_m2`; dates `YYYY-MM-DD`; timestamps ISO 8601 UTC.
- Flag/briefing payload should carry `water_body_id`, `area_m2`, `baseline_area_m2`, `drop_pct`, `flag`, `classification`, `confidence`, `evidence`, `index_version`. `drop_pct` is positive when the lake shrank; the UI shows it as a signed change (`-22.5%`).
- Keep HTTP routes thin; geospatial and agent logic belong in service modules. No database has been chosen yet, so isolate persistence behind a repository module instead of coupling routes to an ORM. `history/cache.py` is the Phase 2/3 cache boundary, keyed by water-body ID, month/day, `index_version`, source, language, and speak flag.
- WebSocket messages are typed (`historical`, `flag`, `briefing` for Phase 2; `live`, `flag`, `alert` for Phase 3; `error` on failure); include the water-body ID in every message. The frontend opens the socket on lake selection, falls back to REST if the socket closes early, and keeps the last good payload on screen.

## Commands

Backend (`backend/`, Python 3.12 via `.python-version`):

```bash
uv sync --extra dev
uv run uvicorn app.main:app --reload
uv run pytest
uv run pytest tests/test_overpass.py -k fallback   # single test
uv run ruff check . && uv run ruff format --check .
uv run mypy app
```

Frontend (`frontend/`):

```bash
pnpm install
pnpm dev
pnpm check          # svelte-check + tsc
pnpm test
pnpm build
```

`frontend/pnpm-workspace.yaml` must keep `allowBuilds: {esbuild: true}` and the `onlyBuiltDependencies` list, or pnpm 12 aborts on ignored build scripts. Run `pnpm check` and `uv run ruff check .` before considering a change done. Add a migration/config step here if one is introduced.

## Testing

- Unit-test index math, thresholding, CRS area conversion, and geometry metrics with synthetic arrays/polygons; these tests must not call OSM, GEE, Sentinel Hub, NVIDIA, or Bhashini.
- Provider tests use `httpx.MockTransport`: Bhashini config/compute flow and NVIDIA chat completions are covered in `tests/test_bhashini.py` and `tests/test_nvidia.py`. Overpass parsing and the Nominatim fallback are covered in `tests/test_overpass.py`.
- Phase 2/3 tests cover monthly climatology, same-month flagging, permanent-vs-seasonal classification, synthetic determinism, NDWI math, SCL masking, UTM pixel area, polygon rasterization, the live REST/WS contracts, and deterministic-vs-NVIDIA narration (`tests/test_history.py`, `tests/test_indices.py`, `tests/test_live.py`).
- Phase 4 tests cover recession velocity, concentric-vs-one-sided geometry, synthetic NDBI, the classification rubric, running the LangGraph with a real and a mocked NVIDIA transport, and the forensic REST/WS contracts (`tests/test_forensics.py`).
- Phase 5 tests cover PNG patch rendering, deterministic inspection per classification, mocked-VLM vs deterministic vision, and the vision REST/WS contracts (`tests/test_vision.py`).
- Keep live-API tests behind an `integration` marker and skip by default.
- Store small GeoJSON/JSON fixtures in `backend/tests/fixtures/`; do not commit large rasters or full satellite scenes.
- LangGraph nodes should be testable with a fake model; assert the graph produces the deterministic metrics even when the LLM output is mocked or malformed.

## Style

- Python: ruff defaults, type hints on public functions, pure helpers in `geo/` (`indices.py`, `raster.py`), external I/O in the phase clients (`services/`, `history/jrc.py`, `live/sentinel.py`).
- TypeScript/Svelte: strict mode, typed API clients, no `any` in new code.
- Keep phase-specific logic out of route handlers and Svelte components; components consume typed payloads only.
