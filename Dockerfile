# syntax=docker/dockerfile:1.7
#
# Multi-stage production container for FastAPI backend boilerplate.
#
# Named runtime targets:
#   api    — FastAPI ASGI server supervised by Tini and Uvicorn
#   worker — Celery background jobs worker supervised by Tini
#
# Build targets:
#   docker build --target api -t fastapi-app:dev .
#   docker build --target worker -t fastapi-worker:dev .

ARG PYTHON_BASE=python:3.12-slim-bookworm

# Pin uv binary from Astral
FROM ghcr.io/astral-sh/uv:0.6.5 AS uv-bin

###############################################################################
# Stage 1: builder-base
# Installs pinned dependencies into a standalone /opt/venv using Astral uv.
###############################################################################
FROM ${PYTHON_BASE} AS builder-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_PROGRESS=1

COPY --from=uv-bin /uv /uvx /usr/local/bin/

WORKDIR /build

RUN uv venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Pre-compile bytecode to eliminate JIT compilation latency on container boot
RUN python -m compileall -q /opt/venv

###############################################################################
# Stage 2: runtime-base
# Minimal slim Python base with tini PID 1 supervision and non-root user.
###############################################################################
FROM ${PYTHON_BASE} AS runtime-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app \
    UVICORN_TIMEOUT_KEEP_ALIVE=65

# Install tini for clean signal forwarding and zombie reaping (PID 1)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*

# Non-root user (uid 10001)
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

###############################################################################
# Target: api
# FastAPI ASGI application server
###############################################################################
FROM runtime-base AS api

COPY --from=builder-base /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app migrations/ ./migrations/
COPY --chown=app:app schema.graphql ./

# Invariant check: enforce zero markdown files in the final runtime container
RUN find /app -name "*.md" | (! grep .)

USER app
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys\ntry:\n    r = urllib.request.urlopen('http://localhost:3000/health/live', timeout=2)\n    sys.exit(0 if r.status == 200 else 1)\nexcept Exception:\n    sys.exit(1)\n"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]

###############################################################################
# Target: worker
# Celery distributed background job worker
###############################################################################
FROM runtime-base AS worker

COPY --from=builder-base /opt/venv /opt/venv
COPY --chown=app:app app/ ./app/
COPY --chown=app:app migrations/ ./migrations/

# Invariant check: enforce zero markdown files in the final runtime container
RUN find /app -name "*.md" | (! grep .)

USER app

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["celery", "-A", "app.core.background_jobs.celery_app", "worker", "-Q", "background-jobs,celery,default", "-l", "INFO", "-c", "2"]
