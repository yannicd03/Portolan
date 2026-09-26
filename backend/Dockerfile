# syntax=docker/dockerfile:1
# Build context: the repository root (the image needs ontology/ and eval/golden/ too).
# The package resolves ontology/ relative to its own path, so the runtime keeps the
# repository layout under /app: /app/backend, /app/ontology, /app/eval/golden.

FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app/backend

# Dependencies first, so source edits do not invalidate this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    uv sync --frozen --no-dev --no-install-project

COPY backend/ /app/backend/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.13-slim AS runtime
LABEL org.opencontainers.image.source="https://github.com/yannicd03/Portolan" \
      org.opencontainers.image.title="portolan-backend"
RUN groupadd --system --gid 1001 portolan \
 && useradd --system --uid 1001 --gid portolan --home-dir /app --no-create-home portolan \
 && mkdir -p /data \
 && chown 1001:1001 /data
WORKDIR /app

COPY --link --from=build /app/.venv /app/.venv
COPY --link --from=build /app/backend /app/backend
COPY --link ontology/ /app/ontology/
COPY --link eval/golden/ /app/eval/golden/

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORTOLAN_DATA_DIR=/data
USER 1001:1001
EXPOSE 8000
ENTRYPOINT ["portolan"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
