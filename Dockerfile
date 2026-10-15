# syntax=docker/dockerfile:1

# ---- build stage: resolve locked dependencies with uv ----
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# Version comes from the release tag (hatch-vcs). .git is not in the build
# context, so CI passes the version in explicitly.
ARG VERSION=0.0.0.dev0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

WORKDIR /app
# Dependencies first, so this layer caches while only source changes
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --locked --no-dev

# ---- runtime stage ----
FROM python:3.12-slim AS runtime

RUN groupadd --system app && useradd --system --gid app --home /app app \
    && mkdir -p /data && chown app:app /data

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL="sqlite:////data/kilometrix.db"

VOLUME ["/data"]
USER app
EXPOSE 8000

# Web by default. Override the command for the poller:
#   command: ["python", "-m", "kilometrix.poller"]
# and for migrations:
#   command: ["alembic", "upgrade", "head"]
CMD ["uvicorn", "kilometrix.web:app", "--host", "0.0.0.0", "--port", "8000"]
