# Water Body Watch

A multilingual municipal dashboard for monitoring urban lakes and ponds in India. It discovers water bodies from OpenStreetMap, builds a seasonal surface-area baseline from JRC Global Surface Water, runs live Sentinel-2 NDWI checks, and produces a forensic + visual briefing when abnormal shrinkage is detected.

## Features

- **Phase 1 — Multilingual discovery**: Text or voice queries (via Bhashini ASR/TTS) → intent extraction → geocoding → OSM Overpass polygon ingest.
- **Phase 2 — Historical baseline**: 10-year monthly surface-area climatology from JRC GSW, same-month flagging, permanent-vs-seasonal classification.
- **Phase 3 — Live alert**: Sentinel-2 L2A NDWI area measurement, same-month comparison, spoken alert generation.
- **Phase 4 — Forensic diagnosis**: LangGraph agent computes recession velocity, asymmetry, 100 m buffer NDBI, and classifies drivers (`encroachment`, `siltation_debris`, `seasonal_drought`, etc.).
- **Phase 5 — Visual validation**: Before/after RGB patches + NVIDIA VLM inspection; falls back to deterministic visual markers when NVIDIA is unavailable.
- **Offline demo mode**: Bundled Bengaluru fixture lets you run the entire UI without any provider keys.

## Tech stack

| Layer | Technology |
| --- | --- |
| Backend | Python 3.12, FastAPI, uv, Pydantic, LangGraph |
| Geospatial | GeoPandas, Shapely, pyproj, `osm2geojson`, `tifffile` |
| Satellite data | JRC GSW via Google Earth Engine (optional), Sentinel-2 via Sentinel Hub (optional) |
| LLM/VLM | NVIDIA NIM (optional; deterministic fallback when unavailable) |
| Speech/translation | Bhashini ULCA (optional) |
| Frontend | SvelteKit, Svelte 5, Vite, Mapbox GL, Chart.js |
| Deployment | Docker Compose |

## Repository structure

```
water-body-watch/
├── AGENTS.md              # Agent-facing architecture, conventions, and domain rules
├── docker-compose.yml     # Backend + frontend orchestration
├── backend/
│   ├── app/
│   │   ├── api/           # FastAPI routes
│   │   ├── config.py      # Pydantic Settings (all env vars)
│   │   ├── deps.py        # FastAPI dependency overrides
│   │   ├── services/      # Provider clients (Bhashini, NVIDIA, Overpass, store)
│   │   ├── history/       # Phase 2: JRC baseline, trend, narration
│   │   ├── live/          # Phase 3: Sentinel-2, NDWI, live alert
│   │   ├── forensics/     # Phases 4 & 5: LangGraph + vision + briefing
│   │   └── geo/           # Pure geospatial helpers (indices, raster, CRS)
│   ├── tests/             # Unit tests (no live network calls by default)
│   ├── .env.example       # Documented environment variables
│   ├── Dockerfile
│   └── pyproject.toml
└── frontend/
    ├── src/lib/
    │   ├── components/    # MapView, HistoryPanel, LivePanel, ForensicsPanel, VisionPanel
    │   ├── charts/        # AreaChart
    │   ├── api.ts         # REST + WebSocket clients
    │   └── types.ts       # Shared TypeScript types
    ├── .env.example       # PUBLIC_MAPBOX_TOKEN only
    ├── Dockerfile
    └── package.json
```

## Quick start

### 1. Clone and enter the repo

```bash
cd water-body-watch
```

### 2. Run with Docker Compose (recommended)

No provider keys are required for the demo path.

```bash
# Build and start both services
docker compose up -d

# Verify backend health
curl http://127.0.0.1:8000/api/health
```

- Backend: http://127.0.0.1:8000
- Frontend: http://127.0.0.1:5173

Bind mounts make source-code edits hot-reload inside the containers.

### 3. Run locally without Docker

**Backend**

```bash
cd backend
uv sync --extra dev
uv run uvicorn app.main:app --reload
```

**Frontend**

```bash
cd frontend
pnpm install
pnpm dev
```

### 4. Enable real provider keys

Copy and fill the example files:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env   # only PUBLIC_MAPBOX_TOKEN goes here
```

Then either restart the backend container (`docker compose up -d backend`) or restart `uvicorn`.

## Environment variables

All secrets stay server-side. Only `PUBLIC_MAPBOX_TOKEN` reaches the frontend.

### Backend (`backend/.env`)

| Variable | Purpose | Required? |
| --- | --- | --- |
| `ENVIRONMENT` | `development` or `production` | No |
| `LOG_LEVEL` | Python log level | No |
| `CORS_ORIGINS` | Comma-separated allowed origins | No |
| `BHASHINI_USER_ID`, `BHASHINI_API_KEY` | Bhashini ASR/translation/TTS | No |
| `NVIDIA_API_KEY`, `NVIDIA_LLM_MODEL`, `NVIDIA_VLM_MODEL` | NVIDIA NIM narration/vision | No |
| `NVIDIA_BASE_URL` | Custom NIM endpoint | No |
| `OSM_USER_AGENT` | Descriptive User-Agent for Nominatim | Recommended |
| `OVERPASS_URL`, `OVERPASS_MIRRORS` | Overpass API endpoints | No |
| `GEE_SERVICE_ACCOUNT_JSON`, `GEE_PROJECT` | Google Earth Engine service account | No |
| `SH_CLIENT_ID`, `SH_CLIENT_SECRET` | Sentinel Hub OAuth credentials | No |
| `PUBLIC_MAPBOX_TOKEN` | Sent to frontend for map tiles | No |

See `backend/.env.example` for the full list and defaults.

### Frontend (`frontend/.env`)

```env
PUBLIC_MAPBOX_TOKEN=pk.eyJ...
```

## API overview

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Service + provider status |
| `GET` | `/api/languages` | Supported languages |
| `POST` | `/api/query` | Text query → water bodies (`demo=true` uses bundled fixture) |
| `POST` | `/api/voice/query` | WAV upload → ASR → same pipeline |
| `GET` | `/api/water-bodies/mask` | Full-resolution lakebed mask |
| `GET` | `/api/water-bodies/detail` | Feature properties |
| `GET` | `/api/water-bodies/history` | Phase 2: monthly series + flag + briefing |
| `GET` | `/api/water-bodies/live` | Phase 3: live NDWI area + alert |
| `GET` | `/api/water-bodies/forensics` | Phase 4: LangGraph diagnosis |
| `GET` | `/api/water-bodies/vision` | Phase 5: RGB patches + VLM inspection |
| `WS` | `/api/ws/{history\|live\|forensics\|vision}/{water_body_id}` | Streaming versions of phases 2–5 |

Water-body IDs look like `osm:way/123456`, so WebSocket routes accept `{water_body_id:path}`.

## Testing

```bash
# Backend
cd backend
uv run pytest                 # unit tests (no network)
uv run pytest -m integration  # live provider tests (skipped by default)
uv run ruff check . && uv run ruff format --check .
uv run mypy app

# Frontend
cd frontend
pnpm test
pnpm check
```

## Domain rules

These are implemented deterministically; LLM/VLM output only narrates or confirms numbers that were already computed.

- **NDWI**: `(B03 - B08) / (B03 + B08)`; water = `NDWI > 0.5`.
- **Flag**: current area more than 15% below the **same calendar month** baseline.
- **JRC baseline**: monthly median climatology from a 10-year window.
- **Classification**: `permanent_loss`, `seasonal_drought`, `normal`, or `insufficient_data`.
- **Forensic rubric**: fast + concentric → `siltation_debris`; straight one-sided cut + concrete → `encroachment`; slow + concentric → `seasonal_drought`; mixed → `inconclusive`.
- **NDBI**: `(B11 - B08) / (B11 + B08)` over the 100 m ring outside the lake.

See `AGENTS.md` for the complete specification, geospatial gotchas, and module ownership.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Map shows a fallback panel | `PUBLIC_MAPBOX_TOKEN` is unset. Add it to `frontend/.env` and restart the frontend. |
| `intent` step shows a warning | Your NVIDIA key cannot serve the configured model. Verify with `curl {NVIDIA_BASE_URL}/models` and update `NVIDIA_LLM_MODEL`. |
| `voice/query` returns 503 | Bhashini keys are missing or the network is blocking `meity-auth.ulcacontrib.org`. |
| History/live always `synthetic-demo` | GEE / Sentinel Hub credentials are missing. This is expected in offline demo mode. |

## Roadmap / open items

- [ ] Verify backend hot reload inside Docker after a source edit.
- [ ] Verify frontend Vite HMR inside Docker after a source edit.
- [ ] Add a working NVIDIA model entitlement (account-side) to switch `source` from `deterministic` to `nvidia`.
- [ ] Add GEE + Sentinel Hub credentials to move from synthetic to real satellite data.
- [ ] Commit the current branch once the workspace is stable.

## License

Add your license here.
