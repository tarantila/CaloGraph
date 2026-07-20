# Reverse Proxy und TLS

CaloGraph bindet standardmäßig nur `127.0.0.1:8180`. Der externe Proxy terminiert TLS und verbindet sich lokal mit diesem Port. WebSockets werden im MVP nicht verwendet.

## HAProxy-Beispiel

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

In `.env` die öffentliche HTTPS-Origin in `TRUSTED_ORIGINS` und den Host in `TRUSTED_HOSTS` aufnehmen. Danach `COOKIE_SECURE=true` setzen. `ENABLE_HSTS=true` erst aktivieren, wenn die Domain dauerhaft ausschließlich per HTTPS funktioniert. Nur bekannte Proxy-Netze dürfen Forwarded-Header liefern; der veröffentlichte Nginx-Port bleibt auf Loopback gebunden.

