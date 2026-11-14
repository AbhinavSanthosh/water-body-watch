# syntax=docker/dockerfile:1

# uv-managed Python 3.12. Deps are installed into /app/.venv; the app source is
# bind-mounted at runtime (see docker-compose.yml) for hot reload.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies only; the project itself is mounted for development.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
