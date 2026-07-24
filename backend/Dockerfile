# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.29 AS uv

FROM python:3.14.6-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH
WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
RUN uv sync --frozen --no-dev

FROM base AS development
RUN uv sync --frozen --all-extras
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM python:3.14.6-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH
RUN groupadd --system --gid 10001 calograph && useradd --system --uid 10001 --gid calograph calograph
WORKDIR /app
COPY --from=base --chown=calograph:calograph /app /app
USER calograph
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips=\"${TRUSTED_PROXY_NETWORKS:-127.0.0.1/32}\""]
