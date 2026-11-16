# syntax=docker/dockerfile:1

# uv-managed Python 3.12. Deps live in /app/.venv and the app source is copied
# into the image so it can run standalone (Railway). For local development,
# docker-compose bind-mounts ./backend/app over /app/app and runs --reload.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for a cached layer; the project itself is not
# installed as a package (it is importable via PYTHONPATH=/app).
COPY pyproject.toml uv.lock ./
# `--extra gee` pulls in earthengine-api so the configured GEE service account
# can query real JRC data (the extra was previously omitted from the image).
RUN uv sync --frozen --extra gee --no-dev --no-install-project

# Copy the application source into the image. Local dev overrides this path
# with a bind mount, so hot reload still works; Railway uses the baked-in copy.
COPY app ./app

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Railway injects PORT; default to 8000 for local/docker use. --proxy-headers
# makes uvicorn trust the platform's X-Forwarded-* headers so HTTPS links and
# WebSocket upgrades are correct behind the Railway proxy.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
