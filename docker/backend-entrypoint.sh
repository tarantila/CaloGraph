#!/bin/sh
set -eu

if [ "${1:-serve}" != "serve" ]; then
  exec "$@"
fi

python -m app.config --check-runtime

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  alembic upgrade head
fi

set -- uvicorn "${ASGI_APP:-app.main:app}" \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${WEB_CONCURRENCY:-2}" \
  --timeout-keep-alive "${UVICORN_TIMEOUT_KEEP_ALIVE:-5}" \
  --timeout-graceful-shutdown "${UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN:-30}" \
  --log-level "${UVICORN_LOG_LEVEL:-info}" \
  --proxy-headers \
  --forwarded-allow-ips "${TRUSTED_PROXY_NETWORKS:-127.0.0.1/32}"

if [ "${UVICORN_ACCESS_LOG:-true}" = "false" ]; then
  set -- "$@" --no-access-log
fi

exec "$@"
