# AGENTS.md

## Status

- Branch `niha`. **Phase 1 is implemented**: multilingual discovery, intent parsing, OSM ingest, lakebed masks, Mapbox UI, and voice confirmation.
- Backend: FastAPI on Python 3.12 (`backend/`), 29 unit tests passing. Frontend: SvelteKit + Svelte 5 + Mapbox GL (`frontend/`), `pnpm check` and `pnpm build` clean.
- Product: municipal dashboard that finds urban lakes/ponds, baselines their historical surface area, flags abnormal shrinkage, and produces a forensic + visual briefing.
- Keep this file current when conventions, commands, or module ownership change.

## Phase 1 endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Service and provider configuration status |
| `GET /api/languages` | Bhashini-supported language list |
| `POST /api/query` | Text query: intent -> geocode -> OSM -> masks -> optional TTS |
| `POST /api/voice/query` | WAV upload: Bhashini ASR + translation, then the same pipeline |
| `GET /api/water-bodies/mask` | Full-resolution lakebed mask for a discovered water body |
| `GET /api/water-bodies/detail` | Feature properties for the click panel |

`POST /api/query` accepts `{"text", "language", "speak", "demo"}`. `demo=true` loads the bundled Bengaluru OSM fixture (`backend/app/data/demo_water_bodies.geojson`) and skips geocoding, which keeps the UI usable offline and without provider keys.

## Pipeline and module ownership

Create code in these areas; keep index math and external I/O separate so it can be tested without network access.

| Phase | Responsibility | Planned location |
|---|---|---|
| 1 | OSM/Overpass polygon ingest, geometry cleanup, map API | `backend/app/services/{overpass,osm,discovery}.py`, `backend/app/api/routes.py` |
| 2 | JRC Global Surface Water baseline, area-over-time series | `backend/app/history/jrc.py` |
| 3 | Sentinel-2 fetch, NDWI, threshold + flag generation | `backend/app/live/sentinel.py`, `backend/app/geo/indices.py`, `backend/app/flags.py` |
| 4 | LangGraph forensic graph: recession velocity, asymmetry, 100 m buffer/NDBI | `backend/app/forensics/` |
| 5 | Aligned RGB patch extraction, VLM inspection, briefing assembly | `backend/app/forensics/vision.py`, `backend/app/briefing.py` |
| UI | Map, click panel, area chart, flag pulse, briefing | `frontend/src/lib/{map,charts,briefing}/` |

Phase 1 provider clients live in `backend/app/services/`: `bhashini.py` (ASR/translation/TTS), `intent.py` (NVIDIA NIM + deterministic fallback), `overpass.py` (mirror racing), and `store.py` (in-memory repository boundary).

Data flow: map load -> REST polygon snapshot -> historical series (REST or WS) -> flag -> LangGraph job -> briefing pushed over WebSocket. The UI must render the last good snapshot on WS reconnect; do not make the map depend on a live socket.

## Domain rules (do not improvise these)

- **NDWI** = `(B03 - B08) / (B03 + B08)`; active water is `NDWI > 0.5`. B03 = green, B08 = NIR.
- **Flag threshold**: current area more than 15% below the baseline. Compare against the **same calendar month** baseline (median of that month across the 10-year window), not the annual mean, or normal dry-season evaporation will trigger false alarms.
- **JRC baseline** is 30 m Landsat-derived occurrence, not a single-year area. Define baseline area as the median annual water area over the decade, computed on a mask consistent with the Sentinel-2 grid. Never compare raw JRC pixel counts to 10 m Sentinel-2 pixel counts.
- **NDBI** = `(B11 - B08) / (B11 + B08)` for the 100 m buffer ring; SWIR1 = B11.
- **Retreat metrics are computed deterministically first** (m²/day, %/month, per-shoreline-segment retreat, straight-edge score). The LLM classifies against an explicit rubric (e.g. concentric = seasonal drought, straight one-sided cut = encroachment, accelerated drop = siltation/debris). Do not let free-form LLM output override the numbers; store both.
- **VLM is supporting evidence, not the trigger.** Only flag from NDWI/JRC metrics. VLM output must be structured JSON with confidence and must cite what is visible.
- Thresholds and rubric constants live in one config module; persist the values/version used with every flag so old flags remain explainable.

## Geospatial gotchas

- Store GeoJSON in **EPSG:4326** with RFC 7946 `[longitude, latitude]` order. Compute areas in a local UTM CRS chosen from the polygon centroid (EPSG:326xx/327xx). Never compute area in 4326.
- Use the **polygon mask**, not its bounding box, for pixel counts. Bboxes are only for tile/request extents.
- Use Overpass API for OSM polygons (`natural=water`, `water=lake|pond|reservoir`), not the main OSM editing API. Set a descriptive `User-Agent` and back off on 429/504.
- Public Overpass instances are unreliable. `overpass.py` races every URL in `OVERPASS_URL` + `OVERPASS_MIRRORS` and accepts the first non-empty response. If all fail, `osm.py` falls back to throttled Nominatim named-lake search (1 req/s, `polygon_geojson=1`, bounded viewbox); surface that fallback as a warning because unnamed ponds will be missing. `overpass.osm.ch` is fast but often has no data outside Switzerland.
- Sentinel-2: use L2A surface reflectance. Mask clouds/shadows/cirrus/snow via the SCL band before computing indices. Do not mix scaled integer reflectance with float reflectance.
- Simplify polygons for Mapbox display, but keep full-resolution geometry for area math.
- Stable water-body ID = OSM type + ID (e.g. `osm:way/123456`), never an array index or display name.
- Cache expensive results by `(water_body_id, date, index_version, source)`; bump `index_version` when thresholds or band math change.

## External services

- **NVIDIA API (NeMo/NIM, OpenAI-compatible)** is the default for both LangGraph LLM nodes and the VLM. Keep model IDs in env, not in code. A local Moondream2/Qwen2.5-VL fallback is optional and must sit behind the same interface.
- **Bhashini** handles both translation and speech for briefings/UI. Call it asynchronously after the English briefing is assembled; never block the forensic response on it.
- **Google Earth Engine** authenticates with a service account in server environments. Do not rely on interactive `earthengine authenticate` on a server.
- **Sentinel Hub** uses OAuth client credentials; cache the token until expiry and respect rate limits.
- All secrets stay server-side. The frontend may only receive `PUBLIC_MAPBOX_TOKEN` (restrict it by URL/scopes); never expose GEE, Sentinel Hub, NVIDIA, or Bhashini keys via `VITE_`/`PUBLIC_` vars.
- Expected env names: `GEE_SERVICE_ACCOUNT_JSON`, `GEE_PROJECT`, `SH_CLIENT_ID`, `SH_CLIENT_SECRET`, `NVIDIA_API_KEY`, `NVIDIA_LLM_MODEL`, `NVIDIA_VLM_MODEL`, `BHASHINI_API_KEY`, `BHASHINI_USER_ID`, `OVERPASS_URL`, `OVERPASS_MIRRORS`, `OVERPASS_REQUEST_TIMEOUT_S`, `PUBLIC_MAPBOX_TOKEN`.
- Bhashini is a two-call ULCA flow: fetch the pipeline config (callback URL, auth header, service IDs), then call the compute endpoint. Cache the config for about an hour; it is the slow half of the voice round trip.
- Without `NVIDIA_API_KEY` the deterministic `fallback_extract` parser runs. Without Bhashini keys, voice input returns 503 and confirmation audio is skipped; text discovery still works.
- NVIDIA model IDs are account- and catalog-dependent. A `410 Gone` means the model was retired; a `404 ... not found for account` means the key cannot use that function. Verify with `GET {NVIDIA_BASE_URL}/models` and keep the working ID in `NVIDIA_LLM_MODEL`, never hard-coded.
- Bhashini may be blocked by a campus/corporate web filter (it answers `303` to a proxy block page). `bhashini.py` reports the status and redirect target instead of an empty error; if you see that, try a different network or ask the network owner to allow `meity-auth.ulcacontrib.org`.

## API and data contracts

- GeoJSON `FeatureCollection` for map payloads; areas in **m²** as `*_m2`; dates `YYYY-MM-DD`; timestamps ISO 8601 UTC.
- Flag/briefing payload should carry `water_body_id`, `area_m2`, `baseline_area_m2`, `drop_pct`, `flag`, `classification`, `confidence`, `evidence`, `index_version`.
- Keep HTTP routes thin; geospatial and agent logic belong in service modules. No database has been chosen yet, so isolate persistence behind a repository module instead of coupling routes to an ORM.
- WebSocket messages are typed (`historical`, `flag`, `forensic`, `briefing`); include the water-body ID in every message.

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
- Keep live-API tests behind an `integration` marker and skip by default.
- Store small GeoJSON/JSON fixtures in `backend/tests/fixtures/`; do not commit large rasters or full satellite scenes.
- LangGraph nodes should be testable with a fake model; assert the graph produces the deterministic metrics even when the LLM output is mocked or malformed.

## Style

- Python: ruff defaults, type hints on public functions, pure helpers in `geo/`, external I/O in `services/` clients.
- TypeScript/Svelte: strict mode, typed API clients, no `any` in new code.
- Keep phase-specific logic out of route handlers and Svelte components; components consume typed payloads only.
