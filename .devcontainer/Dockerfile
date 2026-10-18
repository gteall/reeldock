ARG NODE_IMAGE=node:22-bookworm-slim
FROM ${NODE_IMAGE} AS node
FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    ln -s /usr/local/lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack && \
    corepack enable && corepack prepare pnpm@10.33.0 --activate
COPY --from=ghcr.io/astral-sh/uv:0.9.10 /uv /uvx /usr/local/bin/
