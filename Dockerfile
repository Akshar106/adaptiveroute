# syntax=docker/dockerfile:1.7
# One image runs every process (api, worker, beat, migrations); the command differs.

# --- 1. dashboard build -------------------------------------------------------------
FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. python dependencies -----------------------------------------------------------
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Install third-party deps first so this layer is cached across code changes.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# Bake the embedding model into the image so containers start without network access.
ENV AR_EMBEDDING_CACHE_DIR=/app/models
RUN /app/.venv/bin/python -c "\
from fastembed import TextEmbedding; \
list(TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models').embed(['warmup']))"

# --- 3. runtime -------------------------------------------------------------------------
FROM python:3.12-slim AS runtime
RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /tmp/prometheus && chown app:app /tmp/prometheus
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/models /app/models
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations/ migrations/
COPY --chown=app:app config/ config/
COPY --chown=app:app data/ data/
COPY --chown=app:app benchmarks/ benchmarks/
COPY --from=frontend --chown=app:app /frontend/dist frontend/dist

# AR_PROJECT_ROOT anchors config/, data/, benchmarks/ and frontend/dist (the package
# itself is installed under site-packages, so __file__ can't be used for that).
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AR_PROJECT_ROOT=/app \
    AR_EMBEDDING_CACHE_DIR=/app/models \
    HF_HUB_OFFLINE=1

USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "adaptiveroute.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
