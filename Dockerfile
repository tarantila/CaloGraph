# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Backend
# -----------------------------------------------------------------------------

FROM ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc AS uv

FROM python:3.14.7-alpine3.23@sha256:6b8f06d04d5305c1d1288435388df9165ab41e681fae6439d6349d8053cc3f83 AS backend-base

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

FROM python:3.14.7-alpine3.23@sha256:6b8f06d04d5305c1d1288435388df9165ab41e681fae6439d6349d8053cc3f83 AS backend-runtime

ARG APP_VERSION=development
ARG APP_REVISION=unknown
ARG APP_UID=10001
ARG APP_GID=10001

LABEL org.opencontainers.image.title="CaloGraph Backend" \
      org.opencontainers.image.description="CaloGraph nutrition analytics API" \
      org.opencontainers.image.source="https://github.com/tarantila/CaloGraph" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${APP_REVISION}"

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

FROM node:24.18.0-alpine@sha256:a0b9bf06e4e6193cf7a0f58816cc935ff8c2a908f81e6f1a95432d679c54fbfd AS frontend-dependencies

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

FROM mcr.microsoft.com/playwright:v1.62.1-noble@sha256:dcc5531e97840b9b5e794f2814476b21571c5124a3fca2267d73041f56e7580e AS frontend-e2e

WORKDIR /work
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .

CMD ["npx", "playwright", "test"]

FROM nginxinc/nginx-unprivileged:1.31.3-alpine@sha256:334d92979f15aaecd5dd50af5105e1230e2bb70765d45b1e2f964e7c5eda81c3 AS frontend-runtime

ARG APP_VERSION=development
ARG APP_REVISION=unknown

LABEL org.opencontainers.image.title="CaloGraph Frontend" \
      org.opencontainers.image.description="CaloGraph nutrition analytics web interface" \
      org.opencontainers.image.source="https://github.com/tarantila/CaloGraph" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${APP_REVISION}"

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
