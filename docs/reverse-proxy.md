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
CALOGRAPH_EDGE_SUBNET=172.30.0.0/24
CALOGRAPH_EDGE_GATEWAY_IP=172.30.0.1
CALOGRAPH_FRONTEND_PROXY_IP=172.30.0.10
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
assets, and proxied API responses when the trusted host-side reverse proxy
sends `X-Forwarded-Proto: https`; the backend also enables it on direct
responses. Forwarded protocol headers from any other peer are ignored. The
public reverse proxy should set HSTS itself so redirects and proxy-generated
error pages receive the same protection.

## Trusted proxy boundary

The browser and host-side reverse proxy reach the `frontend` container. Only
that container connects to the backend. Compose therefore assigns the frontend
a fixed address and derives Uvicorn's trusted proxy value as an exact `/32`:

```dotenv
CALOGRAPH_EDGE_SUBNET=172.30.0.0/24
CALOGRAPH_EDGE_GATEWAY_IP=172.30.0.1
CALOGRAPH_FRONTEND_PROXY_IP=172.30.0.10
```

Override all three values together if the example subnet overlaps another
local or routed network. Production rejects wildcard and subnet-wide proxy
trust. The bundled Nginx accepts client IP and protocol metadata only from the
exact edge gateway, replaces the forwarding chain with the resulting client
address, and forwards it to the backend.

An existing installation must recreate the edge network once after upgrading.
This does not remove the PostgreSQL volume:

```bash
docker compose down
docker compose up -d
```

Do not add `--volumes` to the `down` command.

The public Nginx must overwrite forwarding headers at the Internet trust
boundary. In particular, do not use `$proxy_add_x_forwarded_for` here. A
complete configuration is provided in the [Nginx example](#nginx-example).

When a CDN is placed in front of Nginx, configure `set_real_ip_from` only for
the CIDRs published by that CDN, select its documented client-IP header, and
enable `real_ip_recursive`. Nginx may then pass the validated `$remote_addr` as
shown above. Never trust a CDN header from arbitrary Internet clients.

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
    escape=json
    '{"time":"$time_iso8601","remote_addr":"$remote_addr",'
    '"method":"$request_method","path":"$calograph_log_path",'
    '"protocol":"$server_protocol","status":$status,'
    '"bytes":$body_bytes_sent,"request_id":"$request_id"}';

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

## Nginx example

The following configuration can be stored as
`/etc/nginx/conf.d/calograph.conf` on the Docker host. It assumes that the
distribution includes `conf.d/*.conf` from Nginx's `http` context. Replace the
hostname and certificate paths before enabling it.

```nginx
map $uri $calograph_log_path {
    default $uri;
    ~^/einladung/ /einladung/[redacted];
}

log_format calograph_safe
    escape=json
    '{"time":"$time_iso8601","remote_addr":"$remote_addr",'
    '"method":"$request_method","path":"$calograph_log_path",'
    '"protocol":"$server_protocol","status":$status,'
    '"bytes":$body_bytes_sent,"request_id":"$request_id",'
    '"user_agent":"$http_user_agent"}';

upstream calograph_frontend {
    server 127.0.0.1:8180;
    keepalive 16;
}

server {
    listen 80;
    listen [::]:80;
    server_name nutrition.example.com;

    access_log /var/log/nginx/calograph-access.log calograph_safe;
    return 308 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name nutrition.example.com;

    ssl_certificate /etc/letsencrypt/live/nutrition.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/nutrition.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # Matches the bundled proxy limit and leaves room above the 500 MiB
    # application upload limit for multipart framing.
    client_max_body_size 512m;

    access_log /var/log/nginx/calograph-access.log calograph_safe;

    # Set HSTS at the public TLS boundary so Nginx redirects and error pages
    # receive the same policy as application responses.
    add_header Strict-Transport-Security
        "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://calograph_frontend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";

        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Request-ID $request_id;

        # Avoid duplicate HSTS fields; the public proxy owns this header.
        proxy_hide_header Strict-Transport-Security;

        # Apple Health uploads can be large and are streamed by CaloGraph.
        proxy_request_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

This example intentionally overwrites `X-Forwarded-For` with `$remote_addr`.
If a trusted CDN is added, configure its published CIDRs with
`set_real_ip_from` first; `$remote_addr` then contains the client address that
Nginx validated at that trust boundary.

## HAProxy example

```haproxy
frontend https_in
  bind :443 ssl crt /etc/haproxy/certs/calograph.pem
  mode http
  unique-id-format %[uuid()]
  http-request set-header X-Request-ID %[unique-id]
  http-request set-header X-Forwarded-Proto https
  http-request set-header X-Forwarded-Host %[req.hdr(Host)]
  http-request set-header X-Forwarded-For %[src]
  default_backend calograph

backend calograph
  mode http
  option httpchk GET /health
  http-check expect status 200
  server local 127.0.0.1:8180 check
```

Forwarded headers must be accepted only from exact, known proxy addresses.
Keep the bundled frontend port bound to loopback and expose only the TLS
reverse proxy through the firewall.
