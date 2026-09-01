# syntax=docker/dockerfile:1.7

# -----------------------------------------------------------------------------
# Backend
# -----------------------------------------------------------------------------

FROM ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 AS uv

FROM python:3.14.7-alpine3.23@sha256:6b8f06d04d5305c1d1288435388df9165ab41e681fae6439d6349d8053cc3f83 AS backend-base
RUN apk upgrade --no-cache

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
RUN apk upgrade --no-cache

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
RUN test -d /usr/local/lib/python3.14/site-packages/pip \
    && test -d /usr/local/lib/python3.14/ensurepip \
    && test -L /usr/local/bin/pip \
    && test -x /usr/local/bin/pip3 \
    && test -x /usr/local/bin/pip3.14 \
    && rm -rf \
        /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.14/ensurepip \
        /usr/local/bin/pip \
        /usr/local/bin/pip3 \
        /usr/local/bin/pip3.14 \
    && test ! -e /usr/local/lib/python3.14/site-packages/pip \
    && test ! -e /usr/local/lib/python3.14/ensurepip \
    && test ! -e /usr/local/bin/pip \
    && test ! -e /usr/local/bin/pip3 \
    && test ! -e /usr/local/bin/pip3.14
RUN chmod 0555 /licenses

USER calograph
RUN test -r /licenses/CaloGraph-LICENSE.md \
    && test -r /licenses/THIRD_PARTY_NOTICES.md \
    && test -r /licenses/yazio-exporter-MIT.txt \
    && test -n "$(find /licenses/backend -type f -print -quit)"
EXPOSE 8000

ENTRYPOINT ["backend-entrypoint"]
CMD ["serve"]

FROM alpine:3.23@sha256:fd791d74b68913cbb027c6546007b3f0d3bc45125f797758156952bc2d6daf40 AS backup-agent-runtime

ARG APP_VERSION=development
ARG APP_UID=10001
ARG APP_GID=10001
LABEL org.opencontainers.image.title="CaloGraph Backup Agent" \
      org.opencontainers.image.description="Opt-in encrypted backup scheduler with public-key-only access" \
      org.opencontainers.image.source="https://github.com/tarantila/CaloGraph" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0" \
      org.opencontainers.image.version="${APP_VERSION}"

RUN apk add --no-cache age bash coreutils tzdata \
    && addgroup -S -g "${APP_GID}" calograph-backup \
    && adduser -S -D -H -u "${APP_UID}" -G calograph-backup calograph-backup \
    && mkdir -p /var/lib/calograph-backups/artifacts /var/lib/calograph-backups/status \
    && chown -R "${APP_UID}:${APP_GID}" /var/lib/calograph-backups
WORKDIR /app
COPY --chmod=755 scripts/backup-agent.sh scripts/backup-postgres.sh \
  scripts/backup-secrets.sh scripts/backup-retention.sh /app/scripts/
ENV BACKUP_AGENT_ENABLED=false \
    BACKUP_DIR=/var/lib/calograph-backups/artifacts \
    BACKUP_STATUS_FILE=/var/lib/calograph-backups/status/status.json
USER calograph-backup
ENTRYPOINT ["/app/scripts/backup-agent.sh"]

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

FROM nginxinc/nginx-unprivileged:1.31.4-alpine@sha256:d9083fe47768377ef55dedafd67d4da7c2f2bc2bece7554954f29359deb0dce9 AS frontend-runtime

ARG APP_VERSION=development
ARG APP_REVISION=unknown

LABEL org.opencontainers.image.title="CaloGraph Frontend" \
      org.opencontainers.image.description="CaloGraph nutrition analytics web interface" \
      org.opencontainers.image.source="https://github.com/tarantila/CaloGraph" \
      org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${APP_REVISION}"

USER root
RUN apk upgrade --no-cache

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
