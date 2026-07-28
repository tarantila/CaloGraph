#!/bin/sh
set -eu

upload_limit="${NGINX_MAX_UPLOAD_BYTES:-536870912}"
hsts_enabled="${ENABLE_HSTS:-false}"
case "$upload_limit" in
  ""|*[!0-9]*)
    echo "[calograph] NGINX_MAX_UPLOAD_BYTES must be a positive integer" >&2
    exit 1
    ;;
esac

if [ "$upload_limit" -lt 1048576 ]; then
  echo "[calograph] NGINX_MAX_UPLOAD_BYTES must be at least 1048576" >&2
  exit 1
fi

case "$hsts_enabled" in
  true|false)
    ;;
  *)
    echo "[calograph] ENABLE_HSTS must be true or false" >&2
    exit 1
    ;;
esac

printf 'client_max_body_size %s;\n' "$upload_limit" \
  > /tmp/calograph-upload-limit.conf

{
  printf 'map $http_x_forwarded_proto $calograph_hsts {\n'
  printf '  default "";\n'
  if [ "$hsts_enabled" = "true" ]; then
    printf '  https "max-age=31536000; includeSubDomains";\n'
  fi
  printf '}\n'
} > /tmp/calograph-hsts-map.conf

exec /docker-entrypoint.sh "$@"
