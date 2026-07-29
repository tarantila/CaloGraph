# Reverse proxy and TLS

CaloGraph binds to `127.0.0.1:8180` by default. The external proxy terminates
TLS and connects locally to this port. The current application does not use
WebSockets.

## Canonical public URL

Set the externally reachable origin in `.env`:

```dotenv
ENVIRONMENT=production
CALOGRAPH_PUBLIC_URL=https://nutrition.example.com
COOKIE_SECURE=true
TRUSTED_HOSTS=nutrition.example.com
TRUSTED_ORIGINS=https://nutrition.example.com
TRUSTED_PROXY_NETWORKS=172.18.0.0/16
ENABLE_HSTS=true
```

Replace `nutrition.example.com` with the real domain. Reserved example domains
are deliberately rejected by the production preflight.

`CALOGRAPH_PUBLIC_URL` must be an absolute `http://` or `https://` origin
without a path, query, or embedded credentials. CaloGraph uses it for links
that are shared outside the current browser session, including one-time user
invitations. Its hostname is automatically added to `TRUSTED_HOSTS`, and the
complete origin is automatically added to `TRUSTED_ORIGINS`. Those two
variables remain available for additional aliases and origins. Production
mode additionally requires the canonical values to be present explicitly so
the deployed policy cannot silently diverge from `.env`.

Enable HSTS only after the domain works permanently and exclusively over HTTPS.
With `ENABLE_HSTS=true`, the bundled frontend emits HSTS for HTML, static
assets, and proxied API responses whenever the reverse proxy sends
`X-Forwarded-Proto: https`; the backend also enables it on direct responses.
The public reverse proxy should set HSTS itself so redirects and
proxy-generated error pages receive the same protection.

## Trusted proxy network

The browser and external reverse proxy reach the `frontend` container. Only
that container connects to the backend, so Uvicorn must trust forwarded headers
from the Docker bridge network rather than from every sender.

The development template covers Docker's standard bridge address pool:

```dotenv
TRUSTED_PROXY_NETWORKS=172.16.0.0/12
```

For a tighter rule, inspect the installation after the network has been
created:

```bash
docker network inspect calograph_internal \
  --format '{{(index .IPAM.Config 0).Subnet}}'
```

Then copy the reported subnet, for example `172.18.0.0/16`, into
`TRUSTED_PROXY_NETWORKS` and recreate the backend. Comma-separated IP addresses
or CIDRs are accepted. Wildcard trust is rejected, and production also rejects
IPv4 networks broader than `/16` and IPv6 networks broader than `/64`.

The bundled Nginx proxy preserves a valid upstream `X-Forwarded-Proto` value
from the external proxy, appends the forwarding chain, and forwards the
original host.

## Access logs and invitation links

Invitation secrets use a browser-only fragment:
`/einladung#token=invite_…`. The fragment is removed before the browser sends
the HTTP request and is exchanged for a short-lived, signed registration
cookie. Do not rewrite the fragment into a path or query parameter at the
external proxy.

The bundled Nginx access log records `$uri` through a redaction map instead of
the complete `$request`. Query strings are omitted, and a legacy
`/einladung/<token>` path is rendered as `/einladung/[redacted]`.

External Nginx installations should apply the same policy:

```nginx
map $uri $calograph_log_path {
    default $uri;
    ~^/einladung/ /einladung/[redacted];
}

log_format calograph_safe
    '$remote_addr [$time_local] '
    '"$request_method $calograph_log_path $server_protocol" '
    '$status $body_bytes_sent';

access_log /var/log/nginx/calograph-access.log calograph_safe;
```

Do not use `$request`, `$request_uri`, or `$args` in a CaloGraph access-log
format. Configure an equivalent path-only format in HAProxy, Caddy, Traefik, or
the selected log collector. Existing path-based invitation links should be
revoked and regenerated after upgrading.

Do not log request bodies for `/api/v1/auth/invitation/exchange` or
`/api/v1/auth/register`; the exchange body contains the raw invitation token.

## Response security headers

The bundled frontend refuses framing, applies a restrictive Content Security
Policy, isolates same-origin resources, and disables caching for the HTML shell
and API responses. Fingerprinted JavaScript, CSS, fonts, and images remain
publicly cacheable for seven days. Cross-origin opener isolation is emitted
only for HTTPS and browser-trusted local origins because browsers ignore it on
ordinary HTTP origins. The backend independently applies the API header
baseline so the protection also exists during direct service tests.

`Cross-Origin-Embedder-Policy` is intentionally not enabled. It is not required
for CaloGraph and can interfere with browser downloads or future integrations
without providing a current application benefit.

## HAProxy example

```haproxy
frontend https_in
  bind :443 ssl crt /etc/haproxy/certs/calograph.pem
  mode http
  http-request set-header X-Forwarded-Proto https
  http-request set-header X-Forwarded-Host %[req.hdr(Host)]
  default_backend calograph

backend calograph
  mode http
  option httpchk GET /health
  http-check expect status 200
  server local 127.0.0.1:8180 check
```

Forwarded headers must be accepted only from known proxy networks. Keep the
published Nginx port bound to loopback and expose only the TLS reverse proxy
through the firewall.
