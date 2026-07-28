# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.14.6
ARG UV_VERSION=0.11.29
ARG NODE_VERSION=24.18.0
ARG PLAYWRIGHT_VERSION=1.61.1
ARG NGINX_VERSION=1.30.4

# -----------------------------------------------------------------------------
# Backend
# -----------------------------------------------------------------------------

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim AS backend-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:${PATH}

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
RUN uv sync --frozen --no-dev

FROM backend-base AS backend-development

RUN uv sync --frozen --all-extras
COPY backend/tests ./tests

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM python:${PYTHON_VERSION}-slim AS backend-runtime

ARG APP_VERSION=development
ARG APP_UID=10001
ARG APP_GID=10001

LABEL org.opencontainers.image.title="CaloGraph Backend" \
      org.opencontainers.image.description="CaloGraph nutrition analytics API" \
      org.opencontainers.image.source="https://github.com/tarantila/CaloGraph" \
      org.opencontainers.image.version="${APP_VERSION}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:${PATH} \
    ASGI_APP=app.main:app \
    WEB_CONCURRENCY=2 \
    UVICORN_LOG_LEVEL=info \
    UVICORN_TIMEOUT_KEEP_ALIVE=5 \
    UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=30 \
    UVICORN_ACCESS_LOG=true \
    RUN_MIGRATIONS=true

RUN groupadd --gid "${APP_GID}" calograph \
    && useradd --uid "${APP_UID}" --gid calograph \
        --home-dir /nonexistent --no-create-home \
        --shell /usr/sbin/nologin calograph

WORKDIR /app

COPY --from=backend-base --chown=calograph:calograph /opt/venv /opt/venv
COPY --from=backend-base --chown=calograph:calograph /app /app
COPY --chmod=755 docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint

USER calograph
EXPOSE 8000

ENTRYPOINT ["backend-entrypoint"]
CMD ["serve"]

# -----------------------------------------------------------------------------
# Frontend
# -----------------------------------------------------------------------------

FROM node:${NODE_VERSION}-alpine AS frontend-dependencies

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM frontend-dependencies AS frontend-development

COPY frontend/ .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM frontend-dependencies AS frontend-build

COPY frontend/ .
RUN npm run build

FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-noble AS frontend-e2e

WORKDIR /work
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .

CMD ["npx", "playwright", "test"]

FROM nginxinc/nginx-unprivileged:${NGINX_VERSION}-alpine AS frontend-runtime

ARG APP_VERSION=development

LABEL org.opencontainers.image.title="CaloGraph Frontend" \
      org.opencontainers.image.description="CaloGraph nutrition analytics web interface" \
      org.opencontainers.image.source="https://github.com/tarantila/CaloGraph" \
      org.opencontainers.image.version="${APP_VERSION}"

USER root

RUN rm -f \
    /etc/nginx/conf.d/default.conf \
    /docker-entrypoint.d/10-listen-on-ipv6-by-default.sh

COPY frontend/nginx.conf /etc/nginx/nginx.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY --chmod=755 docker/frontend-entrypoint.sh /usr/local/bin/calograph-frontend-entrypoint

USER nginx
EXPOSE 8080

ENTRYPOINT ["calograph-frontend-entrypoint"]
CMD ["nginx", "-g", "daemon off;"]
