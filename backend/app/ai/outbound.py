"""Bounded, DNS-pinned JSON GET transport for AI model discovery."""

from __future__ import annotations

import http.client
import io
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from collections.abc import Buffer
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from app.config import settings

OutboundCode = Literal[
    "destination_blocked",
    "timeout",
    "unavailable",
    "invalid_response",
    "authentication",
    "rate_limited",
    "http_error",
]

_PUBLIC_MESSAGES: dict[OutboundCode, str] = {
    "destination_blocked": "The configured provider destination is not allowed.",
    "timeout": "The provider request timed out.",
    "unavailable": "The provider is unavailable.",
    "invalid_response": "The provider returned an invalid response.",
    "authentication": "The provider rejected the configured credentials.",
    "rate_limited": "The provider temporarily rate limited the request.",
    "http_error": "The provider request failed.",
}
MAX_PROVIDER_RESPONSE_BYTES = 256 * 1024
MAX_PROVIDER_TIMEOUT_SECONDS = 300
_DNS_WORKERS = 4
_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=_DNS_WORKERS, thread_name_prefix="ai-provider-dns")
_DNS_SLOTS = threading.BoundedSemaphore(_DNS_WORKERS)
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_FORBIDDEN_HEADER_NAMES = frozenset(
    {"host", "connection", "content-length", "transfer-encoding", "proxy-authorization", "proxy-connection"}
)
_ALLOWED_HEADER_NAMES = frozenset(
    {"authorization", "x-api-key", "anthropic-version", "x-goog-api-key"}
)
_BLOCKED_ADDRESS_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "fe80::/10",
        "ff00::/8",
    )
)
_PRIVATE_ADDRESS_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128", "fc00::/7")
)


class OutboundError(Exception):
    """Safe transport failure with no URL, request header, or response body."""

    def __init__(self, code: OutboundCode, *, status: int | None = None) -> None:
        self.code = code
        self.status = status
        super().__init__(f"{_PUBLIC_MESSAGES[code]}{f' (HTTP {status})' if status is not None else ''}")


@dataclass(frozen=True, slots=True)
class _Address:
    family: int
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address


@dataclass(frozen=True, slots=True)
class _Endpoint:
    scheme: str
    host: str
    port: int
    path: str

    @property
    def base_url(self) -> str:
        display_host = f"[{self.host}]" if ":" in self.host else self.host
        default_port = (self.scheme == "https" and self.port == 443) or (self.scheme == "http" and self.port == 80)
        authority = display_host if default_port else f"{display_host}:{self.port}"
        return f"{self.scheme}://{authority}{self.path}"


class _DeadlineReader(io.RawIOBase):
    """Socket file adapter which reapplies the absolute deadline on every recv."""

    def __init__(
        self,
        sock: socket.socket | ssl.SSLSocket,
        stream: io.RawIOBase,
        deadline: float,
        timeout_cap: float,
    ) -> None:
        super().__init__()
        self._sock, self._stream = sock, stream
        self._deadline, self._timeout_cap = deadline, timeout_cap

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Buffer, /) -> int:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("provider request deadline elapsed")
        self._sock.settimeout(min(self._timeout_cap, remaining))
        return self._stream.readinto(buffer)

    def close(self) -> None:
        if not self.closed:
            self._stream.close()
        super().close()


class _DeadlineSocket:
    """Narrow socket facade used by http.client, with absolute I/O deadlines."""

    def __init__(self, sock: socket.socket | ssl.SSLSocket, deadline: float, timeout_cap: float) -> None:
        self._sock, self._deadline, self._timeout_cap = sock, deadline, timeout_cap

    def _apply_timeout(self) -> None:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("provider request deadline elapsed")
        self._sock.settimeout(min(self._timeout_cap, remaining))

    def settimeout(self, _timeout: float | None) -> None:
        self._apply_timeout()

    def sendall(self, data: bytes, flags: int = 0) -> None:
        self._apply_timeout()
        self._sock.sendall(data, flags)

    def makefile(self, mode: str = "r", buffering: int | None = None, *, encoding: str | None = None,
                 errors: str | None = None, newline: str | None = None) -> io.BufferedReader:
        del buffering, encoding, errors, newline
        if mode not in {"r", "rb"}:
            raise ValueError("provider transport only reads response bytes")
        stream = self._sock.makefile("rb", buffering=0)
        return io.BufferedReader(_DeadlineReader(self._sock, stream, self._deadline, self._timeout_cap))

    def close(self) -> None:
        self._sock.close()

    def __getattr__(self, name: str) -> object:
        return getattr(self._sock, name)


def _endpoint(value: str) -> _Endpoint:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise OutboundError("destination_blocked")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise OutboundError("destination_blocked") from None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in parsed.path
        or "%" in hostname
        or "%" in parsed.path
        or any(part in {".", ".."} for part in parsed.path.split("/"))
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
        raise OutboundError("destination_blocked")
    try:
        host = hostname.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise OutboundError("destination_blocked") from None
    if not host or host.startswith("."):
        raise OutboundError("destination_blocked")
    if port == 0:
        raise OutboundError("destination_blocked")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*", host):
            raise OutboundError("destination_blocked") from None
    scheme = parsed.scheme.lower()
    return _Endpoint(
        scheme,
        host,
        port if port is not None else (443 if scheme == "https" else 80),
        parsed.path.rstrip("/"),
    )


def _ip_allowed(address: ipaddress.IPv4Address | ipaddress.IPv6Address, *, native: bool) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if any(address in network for network in _BLOCKED_ADDRESS_NETWORKS):
        return False
    private = any(address in network for network in _PRIVATE_ADDRESS_NETWORKS)
    if address.is_unspecified or address.is_multicast or (address.is_reserved and not private):
        return False
    return address.is_global if native else (private or address.is_global)


def _resolve(host: str, port: int, deadline: float, timeout_seconds: int, *, native: bool) -> tuple[_Address, ...]:
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        answers: tuple[_Address, ...] = (
            _Address(socket.AF_INET6 if literal.version == 6 else socket.AF_INET, literal),
        )
    else:
        if not _DNS_SLOTS.acquire(blocking=False):
            raise OutboundError("unavailable")
        future = _DNS_EXECUTOR.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
        future.add_done_callback(lambda _future: _DNS_SLOTS.release())
        try:
            rows = future.result(timeout=min(timeout_seconds, max(0.0, deadline - time.monotonic())))
        except FutureTimeout:
            raise OutboundError("timeout") from None
        except OSError:
            raise OutboundError("unavailable") from None
        answers_list: list[_Address] = []
        seen: set[tuple[int, str]] = set()
        for family, _socktype, _protocol, _canonical_name, sockaddr in rows:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            try:
                address = ipaddress.ip_address(sockaddr[0])
            except (TypeError, ValueError):
                raise OutboundError("destination_blocked") from None
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                address = address.ipv4_mapped
                family = socket.AF_INET
            key = (family, str(address))
            if key not in seen:
                seen.add(key)
                answers_list.append(_Address(family, address))
        answers = tuple(answers_list)
    if not answers or any(not _ip_allowed(item.ip, native=native) for item in answers):
        # `native` is set by the caller below, never inferred from DNS.
        raise OutboundError("destination_blocked")
    return answers


class _PinnedConnection:
    """Construct one socket directly to a prevalidated numeric address."""

    def __init__(self, endpoint: _Endpoint, address: _Address, deadline: float) -> None:
        self.endpoint, self.address, self.deadline = endpoint, address, deadline
        self.sock: socket.socket | ssl.SSLSocket | None = None

    def connect(self, timeout_seconds: int) -> socket.socket | ssl.SSLSocket:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise OutboundError("timeout")
        raw = socket.socket(self.address.family, socket.SOCK_STREAM)
        try:
            raw.settimeout(min(float(timeout_seconds), remaining))
            destination = (str(self.address.ip), self.endpoint.port)
            if self.address.family == socket.AF_INET6:
                raw.connect((destination[0], destination[1], 0, 0))
            else:
                raw.connect(destination)
            peer = ipaddress.ip_address(raw.getpeername()[0])
            if peer != self.address.ip:
                raise OutboundError("destination_blocked")
            connected: socket.socket | ssl.SSLSocket = raw
            if self.endpoint.scheme == "https":
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise OutboundError("timeout")
                raw.settimeout(min(float(timeout_seconds), remaining))
                connected = ssl.create_default_context().wrap_socket(raw, server_hostname=self.endpoint.host)
                connected.settimeout(min(float(timeout_seconds), self.deadline - time.monotonic()))
            self.sock = connected
            return connected
        except BaseException:
            raw.close()
            raise


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, endpoint: _Endpoint, address: _Address, deadline: float, timeout_seconds: int) -> None:
        super().__init__(endpoint.host, endpoint.port, timeout=min(timeout_seconds, max(0.001, deadline - time.monotonic())))
        self._endpoint, self._address = endpoint, address
        self._deadline, self._timeout_seconds = deadline, timeout_seconds

    def connect(self) -> None:
        pinned = _PinnedConnection(self._endpoint, self._address, self._deadline)
        self.sock = _DeadlineSocket(pinned.connect(self._timeout_seconds), self._deadline, float(self._timeout_seconds))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, endpoint: _Endpoint, address: _Address, deadline: float, timeout_seconds: int) -> None:
        super().__init__(endpoint.host, endpoint.port, timeout=min(timeout_seconds, max(0.001, deadline - time.monotonic())))
        self._endpoint, self._address = endpoint, address
        self._deadline, self._timeout_seconds = deadline, timeout_seconds

    def connect(self) -> None:
        pinned = _PinnedConnection(self._endpoint, self._address, self._deadline)
        self.sock = _DeadlineSocket(pinned.connect(self._timeout_seconds), self._deadline, float(self._timeout_seconds))


def _validate_path(path: str) -> str:
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or "?" in path
        or "#" in path
        or "\\" in path
        or "%" in path
        or any(ord(char) <= 32 or ord(char) == 127 for char in path)
    ):
        raise OutboundError("destination_blocked")
    segments = path.split("/")[1:]
    if not segments or any(segment in {"", ".", ".."} for segment in segments):
        raise OutboundError("destination_blocked")
    if any(not re.fullmatch(r"[A-Za-z0-9_.~-]+", segment) for segment in segments):
        raise OutboundError("destination_blocked")
    return path


def _validate_headers(headers: dict[str, str]) -> None:
    if not isinstance(headers, dict) or len(headers) > 12:
        raise OutboundError("destination_blocked")
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or not _HEADER_NAME.fullmatch(name)
            or name.lower() in _FORBIDDEN_HEADER_NAMES
            or name.lower() not in _ALLOWED_HEADER_NAMES
            or not isinstance(value, str)
            or len(value) > 16_384
            or any(ord(char) < 32 or ord(char) > 126 for char in value)
        ):
            raise OutboundError("destination_blocked")


def fetch_provider_json(
    base_url: str,
    path: str,
    *,
    headers: dict[str, str],
    timeout_seconds: int,
    native: bool,
) -> object:
    """Fetch one bounded JSON resource from a fixed native or allowlisted host."""
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, int)
        or not 1 <= timeout_seconds <= MAX_PROVIDER_TIMEOUT_SECONDS
        or not isinstance(native, bool)
    ):
        raise OutboundError("destination_blocked")
    endpoint = _endpoint(base_url)
    relative_path = _validate_path(path)
    _validate_headers(headers)
    if native:
        if endpoint.scheme != "https" or endpoint.port != 443:
            raise OutboundError("destination_blocked")
    else:
        try:
            allowed = {_endpoint(item.strip()).base_url for item in settings.ai_allowed_endpoints.split(",") if item.strip()}
        except OutboundError:
            raise OutboundError("destination_blocked") from None
        if endpoint.base_url not in allowed:
            raise OutboundError("destination_blocked")

    target_path = f"{endpoint.path}{relative_path}"
    if len(target_path) > 2_500:
        raise OutboundError("destination_blocked")
    deadline = time.monotonic() + timeout_seconds
    addresses = _resolve(endpoint.host, endpoint.port, deadline, timeout_seconds, native=native)
    # Every answer is checked before selecting one. A failed connect never retries
    # against another address or asks the resolver to look up the hostname again.
    selected = addresses[0]
    if not native and endpoint.scheme == "http" and any(address.ip.is_global for address in addresses):
        raise OutboundError("destination_blocked")
    connection: http.client.HTTPConnection
    if endpoint.scheme == "https":
        connection = _PinnedHTTPSConnection(endpoint, selected, deadline, timeout_seconds)
    else:
        connection = _PinnedHTTPConnection(endpoint, selected, deadline, timeout_seconds)
    response: http.client.HTTPResponse | None = None
    try:
        if time.monotonic() >= deadline:
            raise OutboundError("timeout")
        connection.putrequest("GET", target_path, skip_accept_encoding=True)
        connection.putheader("Accept", "application/json")
        connection.putheader("Accept-Encoding", "identity")
        connection.putheader("Connection", "close")
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.endheaders()
        if connection.sock is not None:
            connection.sock.settimeout(min(float(timeout_seconds), max(0.001, deadline - time.monotonic())))
        response = connection.getresponse()
        status = response.status
        if status in (401, 403):
            raise OutboundError("authentication", status=status)
        if status == 429:
            raise OutboundError("rate_limited", status=status)
        if 500 <= status <= 599:
            raise OutboundError("unavailable", status=status)
        if not 200 <= status <= 299:
            raise OutboundError("http_error", status=status)
        content_encoding = response.getheader("Content-Encoding", "identity").strip().lower()
        content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_encoding not in {"", "identity"} or not (
            content_type == "application/json" or content_type.endswith("+json")
        ):
            raise OutboundError("invalid_response", status=status)
        content_length = response.getheader("Content-Length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_PROVIDER_RESPONSE_BYTES:
                    raise OutboundError("invalid_response", status=status)
            except ValueError:
                raise OutboundError("invalid_response", status=status) from None
        if connection.sock is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OutboundError("timeout")
            connection.sock.settimeout(min(float(timeout_seconds), remaining))
        body = bytearray()
        while len(body) <= MAX_PROVIDER_RESPONSE_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OutboundError("timeout")
            if connection.sock is not None:
                connection.sock.settimeout(min(float(timeout_seconds), remaining))
            chunk = response.read1(min(16 * 1024, MAX_PROVIDER_RESPONSE_BYTES + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
        if time.monotonic() >= deadline:
            raise OutboundError("timeout")
        if len(body) > MAX_PROVIDER_RESPONSE_BYTES:
            raise OutboundError("invalid_response", status=status)
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise OutboundError("invalid_response", status=status) from None
    except OutboundError:
        raise
    except TimeoutError:
        raise OutboundError("timeout") from None
    except (OSError, ssl.SSLError, http.client.HTTPException, UnicodeError):
        raise OutboundError("unavailable") from None
    finally:
        if response is not None:
            response.close()
        connection.close()
