# Reverse proxy and TLS

CaloGraph binds to `127.0.0.1:8180` by default. The external proxy terminates
TLS and connects locally to this port. The current application does not use
WebSockets.

## Canonical public URL

Set the externally reachable origin in `.env`:

```dotenv
CALOGRAPH_PUBLIC_URL=https://nutrition.example.com
COOKIE_SECURE=true
ENABLE_HSTS=true
```

`CALOGRAPH_PUBLIC_URL` must be an absolute `http://` or `https://` origin
without a path, query, or embedded credentials. CaloGraph uses it for links
that are shared outside the current browser session, including one-time user
invitations. Its hostname is automatically added to `TRUSTED_HOSTS`, and the
complete origin is automatically added to `TRUSTED_ORIGINS`. Those two
variables remain available for additional aliases and origins.

Enable HSTS only after the domain works permanently and exclusively over HTTPS.

## Trusted proxy network

The browser and external reverse proxy reach the `frontend` container. Only
that container connects to the backend, so Uvicorn must trust forwarded headers
from the Docker bridge network rather than from every sender.

The default covers Docker's standard bridge address pool:

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
or CIDRs are accepted. Wildcard trust is rejected.

The bundled Nginx proxy preserves a valid upstream `X-Forwarded-Proto` value
from the external proxy, appends the forwarding chain, and forwards the
original host.

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
