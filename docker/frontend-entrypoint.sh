#!/bin/sh
set -eu

upload_limit="${NGINX_MAX_UPLOAD_BYTES:-536870912}"
hsts_enabled="${ENABLE_HSTS:-false}"
hsts_include_subdomains="${HSTS_INCLUDE_SUBDOMAINS:-false}"
proxy_gateway_ip="${CALOGRAPH_EDGE_GATEWAY_IP:-172.30.0.1}"
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

case "$hsts_include_subdomains" in
  true|false)
    ;;
  *)
    echo "[calograph] HSTS_INCLUDE_SUBDOMAINS must be true or false" >&2
    exit 1
    ;;
esac

if ! printf '%s\n' "$proxy_gateway_ip" | awk -F. '
  NF != 4 { exit 1 }
  {
    for (i = 1; i <= 4; i++) {
      if ($i !~ /^[0-9]+$/ || length($i) > 3 || $i ~ /^0[0-9]/ || $i > 255) {
        exit 1
      }
    }
  }
'; then
  echo "[calograph] CALOGRAPH_EDGE_GATEWAY_IP must be a canonical IPv4 address" >&2
  exit 1
fi

printf 'client_max_body_size %s;\n' "$upload_limit" \
  > /tmp/calograph-upload-limit.conf

{
  printf 'set_real_ip_from %s/32;\n' "$proxy_gateway_ip"
  printf 'real_ip_header X-Forwarded-For;\n'
  printf 'real_ip_recursive on;\n'
} > /tmp/calograph-real-ip.conf

{
  printf 'map "$realip_remote_addr:$http_x_forwarded_proto" $calograph_forwarded_proto {\n'
  printf '  default $scheme;\n'
  printf '  "%s:http" http;\n' "$proxy_gateway_ip"
  printf '  "%s:https" https;\n' "$proxy_gateway_ip"
  printf '}\n'
} > /tmp/calograph-forwarded-proto-map.conf

{
  printf 'map $calograph_forwarded_proto $calograph_hsts {\n'
  printf '  default "";\n'
  if [ "$hsts_enabled" = "true" ]; then
    if [ "$hsts_include_subdomains" = "true" ]; then
      printf '  https "max-age=31536000; includeSubDomains";\n'
    else
      printf '  https "max-age=31536000";\n'
    fi
  fi
  printf '}\n'
} > /tmp/calograph-hsts-map.conf

exec /docker-entrypoint.sh "$@"
