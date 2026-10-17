# Build/test only. Registry publication requires an explicit release instruction.
ARG NODE_IMAGE=node:22-bookworm-slim
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS frontend
WORKDIR /ui
RUN corepack enable && corepack prepare pnpm@10.33.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:0.9.10 AS uv
FROM ${PYTHON_IMAGE} AS python-build
COPY --from=uv /uv /bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy
WORKDIR /opt/reeldock
COPY pyproject.toml uv.lock ./
COPY backend/src ./backend/src
RUN uv sync --frozen --no-dev --no-editable --compile-bytecode

FROM ${PYTHON_IMAGE} AS runtime
LABEL org.opencontainers.image.title="ReelDock · 影坞" \
      org.opencontainers.image.source="https://github.com/gteall/reeldock" \
      org.opencontainers.image.description="WebDAV media metadata and safe archive workflow"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/reeldock/.venv/bin:$PATH" HOME=/tmp \
    REELDOCK_DATA_DIR=/data REELDOCK_CACHE_DIR=/cache \
    REELDOCK_FRONTEND_DIR=/opt/reeldock/frontend
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata && \
    rm -rf /var/lib/apt/lists/* && \
    groupadd --gid 10001 reeldock && useradd --uid 10001 --gid 10001 --no-create-home reeldock && \
    mkdir /data /cache && chown 10001:10001 /data /cache
WORKDIR /opt/reeldock
COPY --from=python-build /opt/reeldock/.venv /opt/reeldock/.venv
COPY --from=frontend /ui/dist ./frontend
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD ["reeldock", "healthcheck"]
ENTRYPOINT ["reeldock"]
CMD ["serve"]
