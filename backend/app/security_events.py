import hashlib
import hmac
import json
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from app.config import settings


@dataclass(frozen=True, slots=True)
class EventSpec:
    outcome: str
    level: int
    detail_fields: frozenset[str] = frozenset()


_COUNT_FIELDS = frozenset({"received", "inserted", "updated", "skipped", "failed"})
EVENT_SPECS: Final[dict[str, EventSpec]] = {
    "admin.mfa.reset": EventSpec("success", logging.WARNING),
    "admin.user.created": EventSpec("success", logging.INFO),
    "admin.user.deactivated": EventSpec("success", logging.WARNING),
    "admin.user.deleted": EventSpec("success", logging.WARNING),
    "admin.user.lifecycle_failed": EventSpec(
        "failure",
        logging.ERROR,
        frozenset({"action", "reason"}),
    ),
    "admin.user.lifecycle_rejected": EventSpec(
        "blocked",
        logging.WARNING,
        frozenset({"action", "reason"}),
    ),
    "admin.user.reactivated": EventSpec("success", logging.INFO),
    "auth.api_token.created": EventSpec("success", logging.INFO),
    "auth.api_token.revoked": EventSpec("success", logging.INFO),
    "auth.invitation.created": EventSpec("success", logging.INFO),
    "auth.invitation.exchanged": EventSpec("success", logging.INFO),
    "auth.invitation.rejected": EventSpec("failure", logging.WARNING),
    "auth.invitation.revoked": EventSpec("success", logging.INFO),
    "auth.login.failed": EventSpec("failure", logging.WARNING),
    "auth.login.mfa_required": EventSpec("pending", logging.INFO),
    "auth.login.succeeded": EventSpec("success", logging.INFO),
    "auth.mfa.recovery_codes_replaced": EventSpec("success", logging.WARNING),
    "auth.mfa.totp_disabled": EventSpec("success", logging.WARNING),
    "auth.mfa.totp_enabled": EventSpec("success", logging.INFO),
    "auth.mfa.totp_setup_started": EventSpec("pending", logging.INFO),
    "auth.passkey.login_failed": EventSpec("failure", logging.WARNING),
    "auth.passkey.login_succeeded": EventSpec("success", logging.INFO),
    "auth.passkey.registered": EventSpec("success", logging.INFO),
    "auth.passkey.registration_started": EventSpec("pending", logging.INFO),
    "auth.passkey.removed": EventSpec("success", logging.WARNING),
    "auth.password.change_failed": EventSpec("failure", logging.WARNING),
    "auth.password.changed": EventSpec("success", logging.WARNING),
    "auth.registration.succeeded": EventSpec("success", logging.INFO),
    "auth.session.logged_out": EventSpec("success", logging.INFO),
    "import.completed": EventSpec("success", logging.INFO, _COUNT_FIELDS | {"source_type"}),
    "import.partial_failed": EventSpec(
        "failure", logging.WARNING, _COUNT_FIELDS | {"source_type"}
    ),
    "import.rejected": EventSpec("failure", logging.WARNING, frozenset({"status_code"})),
    "import.started": EventSpec("pending", logging.INFO, frozenset({"source_type"})),
    "import.validation_completed": EventSpec(
        "success", logging.INFO, _COUNT_FIELDS | {"source_type"}
    ),
    "integration.yazio.connection_configured": EventSpec("success", logging.INFO),
    "integration.yazio.connection_disabled": EventSpec("success", logging.WARNING),
    "integration.yazio.connection_failed": EventSpec("failure", logging.WARNING),
    "integration.yazio.sync_completed": EventSpec(
        "success", logging.INFO, _COUNT_FIELDS | {"mode"}
    ),
    "integration.yazio.sync_failed": EventSpec(
        "failure", logging.WARNING, frozenset({"mode"})
    ),
    "request.failed": EventSpec("failure", logging.ERROR),
    "security.rate_limit.triggered": EventSpec(
        "blocked",
        logging.WARNING,
        frozenset({"action", "retry_after"}),
    ),
}

_REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_REFERENCE_PATTERN = re.compile(r"^[a-f0-9]{16}$")
_SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,64}$")
_request_id_context: ContextVar[str | None] = ContextVar(
    "calograph_security_request_id", default=None
)
_client_ref_context: ContextVar[str | None] = ContextVar(
    "calograph_security_client_ref", default=None
)

logger = logging.getLogger("calograph.security")
logger.setLevel(logging.INFO)
logger.propagate = False
if not any(handler.get_name() == "calograph-security-json" for handler in logger.handlers):
    handler = logging.StreamHandler(sys.stdout)
    handler.set_name("calograph-security-json")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)


def security_reference(namespace: str, value: object) -> str:
    if not _SAFE_TOKEN_PATTERN.fullmatch(namespace):
        raise ValueError("Security reference namespace is invalid")
    material = f"{namespace}:{value}".encode()
    return hmac.new(
        settings.rate_limit_secret.encode(), material, hashlib.sha256
    ).hexdigest()[:16]


@contextmanager
def security_request_context(request_id: str, client_ref: str) -> Iterator[None]:
    request_token = _request_id_context.set(request_id)
    client_token = _client_ref_context.set(client_ref)
    try:
        yield
    finally:
        _request_id_context.reset(request_token)
        _client_ref_context.reset(client_token)


def _validated_reference(value: str | None, field: str) -> str | None:
    if value is not None and not _REFERENCE_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must be a pseudonymous security reference")
    return value


def _validated_details(spec: EventSpec, details: Mapping[str, object]) -> dict[str, object]:
    unknown = set(details) - spec.detail_fields
    if unknown:
        raise ValueError(f"Security event contains unsupported detail fields: {sorted(unknown)}")
    validated: dict[str, object] = {}
    for key, value in details.items():
        if (
            isinstance(value, bool)
            or (isinstance(value, int) and value >= 0)
            or (isinstance(value, str) and _SAFE_TOKEN_PATTERN.fullmatch(value))
        ):
            validated[key] = value
        else:
            raise ValueError(f"Security event detail {key!r} is not a safe scalar")
    return validated


def log_security_event(
    event: str,
    *,
    actor_ref: str | None = None,
    client_ref: str | None = None,
    target_ref: str | None = None,
    reason: str | None = None,
    details: Mapping[str, object] | None = None,
) -> None:
    spec = EVENT_SPECS.get(event)
    if spec is None:
        raise ValueError(f"Unknown security event: {event}")
    request_id = _request_id_context.get()
    if request_id is not None and not _REQUEST_ID_PATTERN.fullmatch(request_id):
        raise ValueError("Security event request ID is invalid")
    if reason is not None and not _SAFE_TOKEN_PATTERN.fullmatch(reason):
        raise ValueError("Security event reason must be a bounded identifier")

    payload: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        ),
        "event": event,
        "outcome": spec.outcome,
    }
    optional_fields = {
        "request_id": request_id,
        "actor_ref": _validated_reference(actor_ref, "actor_ref"),
        "client_ref": _validated_reference(
            client_ref if client_ref is not None else _client_ref_context.get(),
            "client_ref",
        ),
        "target_ref": _validated_reference(target_ref, "target_ref"),
        "reason": reason,
    }
    payload.update({key: value for key, value in optional_fields.items() if value is not None})
    payload.update(_validated_details(spec, details or {}))
    logger.log(spec.level, json.dumps(payload, sort_keys=True, separators=(",", ":")))
