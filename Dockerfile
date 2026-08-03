# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Backend
# -----------------------------------------------------------------------------

FROM ghcr.io/astral-sh/uv:0.12.0@sha256:606e70c71c852d03f611b1e56a195d08648507018a7057fab82c4974c4eae105 AS uv

FROM python:3.14.6-alpine3.23@sha256:b165067c5afc37fa5608a3c05609cc3d51aafd808a30fbfd822ee594fef55ad4 AS backend-base

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
RUN uv sync --frozen --no-dev \
    && mkdir -p /third-party-licenses/backend \
    && find /opt/venv -type f \
        \( -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) \
        -print \
      | while IFS= read -r license_file; do \
          relative_path=${license_file#/opt/venv/}; \
          destination=/third-party-licenses/backend/${relative_path}; \
          mkdir -p "$(dirname "$destination")"; \
          cp "$license_file" "$destination"; \
        done

FROM backend-base AS backend-development

RUN uv sync --frozen --all-extras
COPY backend/tests ./tests

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM python:3.14.6-alpine3.23@sha256:b165067c5afc37fa5608a3c05609cc3d51aafd808a30fbfd822ee594fef55ad4 AS backend-runtime

ARG APP_VERSION=development
ARG APP_UID=10001
ARG APP_GID=10001

LABEL org.opencontainers.image.title="CaloGraph Backend" \
      org.opencontainers.image.description="CaloGraph nutrition analytics API" \
      org.opencontainers.image.source="https://github.com/tarantila/CaloGraph" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0" \
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

RUN addgroup -S -g "${APP_GID}" calograph \
    && adduser -S -D -H -u "${APP_UID}" -G calograph \
        -s /sbin/nologin calograph

WORKDIR /app

COPY --from=backend-base --chown=calograph:calograph /opt/venv /opt/venv
COPY --from=backend-base --chown=calograph:calograph /app /app
COPY --chmod=444 LICENSE /licenses/CaloGraph-LICENSE.md
COPY --chmod=444 THIRD_PARTY_NOTICES.md /licenses/THIRD_PARTY_NOTICES.md
COPY --from=backend-base /third-party-licenses/backend /licenses/backend
COPY --chmod=444 THIRD_PARTY_LICENSES/yazio-exporter-MIT.txt /licenses/yazio-exporter-MIT.txt
COPY --chmod=755 docker/backend-entrypoint.sh /usr/local/bin/backend-entrypoint
RUN chmod 0555 /licenses

USER calograph
RUN test -r /licenses/CaloGraph-LICENSE.md \
    && test -r /licenses/THIRD_PARTY_NOTICES.md \
    && test -r /licenses/yazio-exporter-MIT.txt \
    && test -n "$(find /licenses/backend -type f -print -quit)"
EXPOSE 8000

ENTRYPOINT ["backend-entrypoint"]
CMD ["serve"]

# -----------------------------------------------------------------------------
# Frontend
# -----------------------------------------------------------------------------

FROM node:26.5.1-alpine@sha256:233761595746769ebfdb6090f44fc7cdf818ae0ce62d2b37e0367723b9823e36 AS frontend-dependencies

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

FROM frontend-dependencies AS frontend-development

COPY frontend/ .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM frontend-dependencies AS frontend-build

COPY frontend/ .
RUN npm run build \
    && npm prune --omit=dev \
    && mkdir -p /third-party-licenses/frontend \
    && find node_modules -type f \
        \( -iname 'LICENSE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) \
        -print \
      | while IFS= read -r license_file; do \
          relative_path=${license_file#node_modules/}; \
          destination=/third-party-licenses/frontend/${relative_path}; \
          mkdir -p "$(dirname "$destination")"; \
          cp "$license_file" "$destination"; \
        done

FROM mcr.microsoft.com/playwright:v1.62.0-noble@sha256:baed2032d533817f3dbe6425de795788430ba345e819a1201337009ba17c9d07 AS frontend-e2e

WORKDIR /work
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .

CMD ["npx", "playwright", "test"]

FROM nginxinc/nginx-unprivileged:1.31.2-alpine@sha256:6320020c7da8714feab524e02c08c5a1958675c4e68700e93a2fd8970b065786 AS frontend-runtime

ARG APP_VERSION=development

LABEL org.opencontainers.image.title="CaloGraph Frontend" \
      org.opencontainers.image.description="CaloGraph nutrition analytics web interface" \
      org.opencontainers.image.source="https://github.com/tarantila/CaloGraph" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0" \
      org.opencontainers.image.version="${APP_VERSION}"

USER root

RUN rm -f \
    /etc/nginx/conf.d/default.conf \
    /docker-entrypoint.d/10-listen-on-ipv6-by-default.sh

COPY frontend/nginx.conf /etc/nginx/nginx.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY --chmod=444 LICENSE /licenses/CaloGraph-LICENSE.md
COPY --chmod=444 THIRD_PARTY_NOTICES.md /licenses/THIRD_PARTY_NOTICES.md
COPY --from=frontend-build /third-party-licenses/frontend /licenses/frontend
COPY --chmod=755 docker/frontend-entrypoint.sh /usr/local/bin/calograph-frontend-entrypoint
RUN chmod 0555 /licenses

USER nginx
RUN test -r /licenses/CaloGraph-LICENSE.md \
    && test -r /licenses/THIRD_PARTY_NOTICES.md \
    && test -r /licenses/frontend/@fontsource/inter/LICENSE \
    && test -r /licenses/frontend/echarts/NOTICE \
    && test -r /licenses/frontend/vue/LICENSE
EXPOSE 8080

ENTRYPOINT ["calograph-frontend-entrypoint"]
CMD ["nginx", "-g", "daemon off;"]
