from __future__ import annotations

import ipaddress
import threading
import time
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from urllib.parse import quote

import requests

from app.config import settings


@dataclass(frozen=True, slots=True)
class GeoIpInfo:
    location: str | None
    provider: str | None


_CACHE_LIMIT = 512
_cache: dict[str, tuple[float, GeoIpInfo | None]] = {}
_cache_lock = threading.Lock()


def _private_ip_info(address: IPv4Address | IPv6Address) -> GeoIpInfo | None:
    if not address.is_private and not address.is_loopback and not address.is_link_local:
        return None
    return GeoIpInfo(
        location="Lokal" if address.is_loopback else "Privates Netzwerk",
        provider=None,
    )


def _cached(ip: str) -> GeoIpInfo | object | None:
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(ip)
        if cached and cached[0] > now:
            return cached[1]
        if cached:
            _cache.pop(ip, None)
    return _MISSING


def _store(ip: str, value: GeoIpInfo | None) -> None:
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            oldest = min(_cache, key=lambda key: _cache[key][0])
            _cache.pop(oldest, None)
        _cache[ip] = (time.monotonic() + settings.security_audit_geoip_cache_seconds, value)


def lookup_client_ip(ip: str | None) -> GeoIpInfo | None:
    if not ip:
        return None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    private_info = _private_ip_info(address)
    if private_info is not None:
        return private_info
    if settings.security_audit_geoip_provider == "disabled":
        return None
    cached = _cached(str(address))
    if cached is not _MISSING:
        return cached  # type: ignore[return-value]
    try:
        if settings.security_audit_geoip_provider != "ipwhois":
            return None
        response = requests.get(
            f"https://ipwho.is/{quote(str(address), safe='')}"
            "?fields=success,city,country_code,connection.org",
            timeout=settings.security_audit_geoip_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("success") is not True:
            info = None
        else:
            city = payload.get("city")
            country_code = payload.get("country_code")
            organization = (payload.get("connection") or {}).get("org") if isinstance(payload.get("connection"), dict) else None
            location_parts = [part for part in (city, country_code) if isinstance(part, str) and part.strip()]
            info = GeoIpInfo(
                location=", ".join(location_parts) or None,
                provider=organization.strip() if isinstance(organization, str) and organization.strip() else None,
            )
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        info = None
    _store(str(address), info)
    return info


def clear_geoip_cache() -> None:
    with _cache_lock:
        _cache.clear()


_MISSING = object()
