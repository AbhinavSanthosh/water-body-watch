# Deployment

This repository is **two deployables on two platforms**:

| Piece | Platform | Config | Why |
|---|---|---|---|
| `frontend/` (SvelteKit) | **Vercel** | `frontend/vercel.json`, `frontend/svelte.config.js` | Client-side dashboard; `@sveltejs/adapter-vercel` |
| `backend/` (FastAPI) | **Railway** | `backend/railway.json`, `backend/Dockerfile` | Needs Docker, WebSockets, heavy geospatial deps, and long-running GEE/Sentinel/LLM calls |

The frontend calls the backend over an absolute URL (`PUBLIC_API_BASE`). CORS is handled by the
backend's `CORS_ORIGINS` + `CORS_ORIGIN_REGEX`. They are decoupled and can live on different hosts.

---

## 1. Deploy the backend to Railway

`backend/railway.json` declares a `DOCKERFILE` build and a `/api/health` healthcheck, and the
`backend/Dockerfile` now **copies the app source into the image** and runs a production CMD
(no `--reload`, binds `$PORT`). So the backend is a self-contained Docker image on Railway.

### Steps

1. Push the repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub repo** → select the repo.
3. On the service, set **Root Directory** to `backend`. Railway reads `backend/railway.json` and
   builds `backend/Dockerfile`.
4. Add the environment variables (copy from `backend/.env` — see the table below).
5. Set `CORS_ORIGINS` to your production Vercel domain, e.g. `https://<project>.vercel.app`.
   (`CORS_ORIGIN_REGEX` already matches every `*.vercel.app` preview deployment.)
6. Deploy. `PORT` is injected by Railway and `/api/health` is used as the healthcheck.

> Railway runs the container 24/7, so the in-memory `WaterBodyStore` and caches behave as
> designed (discovery → detail flow works). This is why the backend is on Railway, not Vercel.

### Backend environment variables (Railway)

Copy these from `backend/.env`. Full list in `backend/.env.example`.

| Group | Variables |
|---|---|
| Runtime/CORS | `ENVIRONMENT`, `LOG_LEVEL`, `CORS_ORIGINS`, `CORS_ORIGIN_REGEX` |
| Bhashini (voice) | `BHASHINI_USER_ID`, `BHASHINI_API_KEY`, `BHASHINI_PIPELINE_ID` |
| NVIDIA (LLM/VLM) | `NVIDIA_API_KEY`, `NVIDIA_LLM_MODEL`, `NVIDIA_VLM_MODEL`, `NVIDIA_BASE_URL`, `NVIDIA_TIMEOUT_S`, `NVIDIA_VLM_TIMEOUT_S` |
| OpenStreetMap | `OSM_USER_AGENT`, `OVERPASS_URL`, `OVERPASS_MIRRORS`, `OSM_*` |
| Google Earth Engine | `GEE_SERVICE_ACCOUNT_JSON`, `GEE_PROJECT`, `JRC_COLLECTION`, `HISTORY_*` |
| Sentinel Hub | `SH_CLIENT_ID`, `SH_CLIENT_SECRET`, `SH_COLLECTION`, `LIVE_*` |
| Thresholds | `FLAG_DROP_PCT`, `FORENSIC_*`, `VISION_*`, `*_INDEX_VERSION` |

Notes:

- `GEE_SERVICE_ACCOUNT_JSON` accepts either a file path or the raw JSON. On Railway, paste the
  **raw service-account JSON** into the variable (Railway supports multiline values).
- Use a **~1 GB** plan or higher: `earthengine-api`, `geopandas`, `shapely`, `rasterio`, `tifffile`
  and the NumPy/scipy stack need the headroom.
- WebSockets are proxied by Railway automatically; no extra config is required.

---

## 2. Deploy the frontend to Vercel

`frontend/vercel.json` sets the framework and install/build commands, and `frontend/svelte.config.js`
uses `@sveltejs/adapter-vercel` with `runtime: 'nodejs22.x'`. `package.json` pins
`packageManager: pnpm@12.18.0` and `engines.node: 22.x` so Vercel builds with the same toolchain
that passes locally.

### Steps

1. Push the repo to GitHub.
2. In Vercel: **Import Project** → select the repo → set **Root Directory** to `frontend`.
3. Framework is auto-detected as **SvelteKit** (see `frontend/vercel.json`).
4. Add the environment variables (below).
5. Deploy.

### Frontend environment variables (Vercel)

| Variable | Value |
|---|---|
| `PUBLIC_API_BASE` | `https://<your-service>.up.railway.app` |
| `PUBLIC_MAPBOX_TOKEN` | your Mapbox public token (`pk.eyJ…`) |

Only `PUBLIC_*` variables reach the browser. Never put backend secrets on Vercel.

---

## 3. Local development (unchanged)

```bash
docker compose up -d --build
```

Backend on `http://127.0.0.1:8000`, frontend on `http://127.0.0.1:5173`. The local
`frontend/.env` keeps `PUBLIC_API_BASE=http://127.0.0.1:8000`; when `PUBLIC_API_BASE` is empty
the frontend assumes the backend is on the same origin.

---

## 4. Post-deploy checks

1. `GET https://<backend>/api/health` → `status: "ok"` and provider flags reflect your keys.
2. `CORS_ORIGINS` includes the production Vercel domain (previews are covered by `CORS_ORIGIN_REGEX`).
3. **HTTPS everywhere** — the Vercel site is HTTPS, so the backend must be HTTPS too (Railway
   provides this by default).
4. **WebSockets** — select a lake and confirm the History/Live/Forensics/Vision panels stream.
5. Base map renders (Mapbox token valid and its URL restriction allows the Vercel domain).
6. Run a text query and confirm the response has no `synthetic-demo`/failure warnings when real
   providers are configured.

---

## 5. Known gotchas (verified against this account)

- **NVIDIA model entitlement.** `GET https://integrate.api.nvidia.com/v1/models` lists the whole
  catalog, but a key is only entitled to a few of them — most return `404 … Not found for account`.
  This key is entitled to `meta/llama-3.2-90b-vision-instruct` (used for both narration and vision
  here). Always confirm a model with a real `chat/completions` call before wiring it into
  `NVIDIA_LLM_MODEL`. Large models can also time out; keep `NVIDIA_TIMEOUT_S`/`NVIDIA_VLM_TIMEOUT_S`
  short so the deterministic fallback answers quickly instead of hanging.
- **GEE service account permission.** If the history step logs `403 Forbidden … PERMISSION_DENIED`,
  the service account hasn't been granted Earth Engine access. Enable the Earth Engine API and
  register the service account in the GCP project; until then the history series falls back to the
  synthetic demo series (labelled `synthetic-demo`).
- **Overpass mirrors.** Public Overpass instances are flaky and some are blocked on campus/corporate
  networks. `overpass.py` races all mirrors and falls back to a throttled Nominatim named-lake
  search; the response warns when this happens (unnamed ponds may be missing).
- **Nominatim 403.** Nominatim returns 403 for User-Agents on reserved placeholder domains
  (`example.org`/`example.com`). Use a real, descriptive `OSM_USER_AGENT` with a real contact.
