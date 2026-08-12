import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.importers.yazio import parse_yazio_export
from app.models import User, YazioConnection
from app.schemas import ImportSummary
from app.security_events import log_security_event, security_reference
from app.services.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)
from app.services.import_service import persist_import
from app.services.rate_limit import (
    RateLimitExceeded,
    check_rate_limit,
    clear_rate_limit,
    ensure_rate_limit_available,
)
from app.services.user_operation_lock import (
    InactiveUserOperation,
    UserOperationBusy,
    shared_user_operation,
)
from app.services.yazio_guard import YazioOperationBusy, yazio_operation_slot
from app.services.yazio_transport import (
    YazioTransportAuthenticationError,
    YazioTransportDeadlineError,
    YazioTransportError,
    fetch_yazio_payload_transport,
    validate_yazio_credentials_transport,
)

YazioFetcher = Callable[[str, str, date, date, bool], dict[str, Any]]
MICRONUTRIENT_SYNC_INTERVAL = timedelta(hours=24)
YAZIO_CIRCUIT_ACTION = "yazio-provider-failure"
YAZIO_CIRCUIT_KEY = "provider:yzapi.yazio.com"


class YazioSyncError(RuntimeError):
    pass


class YazioConnectionNotConfigured(YazioSyncError):
    pass


class YazioConnectionDisabled(YazioSyncError):
    pass


class YazioDisabled(YazioSyncError):
    pass


class YazioAuthenticationError(YazioSyncError):
    pass


class YazioOperationDeadlineExceeded(YazioSyncError):
    pass


class YazioOperationCapacityExceeded(YazioSyncError):
    pass


class YazioCircuitOpen(YazioSyncError):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after
        super().__init__("YAZIO ist nach mehreren Providerfehlern vorübergehend pausiert.")


@contextmanager
def _active_yazio_user_operation(db: Session, user_id: UUID) -> Iterator[User]:
    try:
        with shared_user_operation(db, user_id) as user:
            yield user
    except InactiveUserOperation as exc:
        raise YazioSyncError("CaloGraph-Benutzer ist nicht aktiv.") from exc
    except UserOperationBusy as exc:
        raise YazioOperationCapacityExceeded(
            "Für dieses Konto läuft gerade eine administrative Operation."
        ) from exc


def yazio_failure_reason(error: Exception) -> str:
    if isinstance(error, YazioAuthenticationError):
        return "authentication_error"
    if isinstance(error, YazioOperationDeadlineExceeded):
        return "deadline_exceeded"
    if isinstance(error, YazioOperationCapacityExceeded):
        return "capacity_exceeded"
    if isinstance(error, YazioCircuitOpen):
        return "circuit_open"
    if isinstance(error, YazioConnectionNotConfigured):
        return "connection_not_configured"
    if isinstance(error, YazioDisabled):
        return "feature_disabled"
    if isinstance(error, CredentialEncryptionError):
        return "credential_decryption_error"
    if isinstance(error, YazioSyncError):
        return "provider_error"
    return "unexpected_error"


def _next_sync_at(reference: datetime, interval_minutes: int) -> datetime:
    max_jitter = settings.yazio_scheduler_jitter_minutes
    jitter_minutes = secrets.randbelow(max_jitter) + 1 if max_jitter else 0
    return reference + timedelta(minutes=interval_minutes + jitter_minutes)


def yazio_source_identifier(user_id: UUID) -> str:
    return f"yazio:{user_id}"

YAZIO_HISTORY_DISCOVERY_START = date(2000, 1, 1)
MAX_YAZIO_RANGE_DAYS = 366
MAX_YAZIO_HISTORY_RANGE_DAYS = 366



def effective_sync_interval_minutes(connection: YazioConnection) -> int:
    return connection.sync_interval_minutes or settings.yazio_sync_interval_hours * 60


def effective_sync_days(connection: YazioConnection) -> int:
    return connection.sync_days or settings.yazio_sync_days


def enqueue_historical_yazio_sync(
    user_id: UUID,
    *,
    kind: Literal["full", "range"],
    start_day: date | None = None,
    end_day: date | None = None,
    now: datetime | None = None,
) -> YazioConnection:
    _require_yazio_enabled()
    if kind == "range" and (
        start_day is None
        or end_day is None
        or start_day > end_day
        or (end_day - start_day).days >= MAX_YAZIO_HISTORY_RANGE_DAYS
    ):
        raise ValueError(
            "Der historische Zeitraum ist ungültig oder länger als 366 Tage."
        )
    requested_at = now or datetime.now(UTC)
    with SessionLocal() as db, _active_yazio_user_operation(db, user_id):
        connection = db.scalar(
            select(YazioConnection).where(YazioConnection.user_id == user_id)
        )
        if connection is None:
            raise YazioConnectionNotConfigured(
                "Für dieses Konto ist keine YAZIO-Verbindung eingerichtet."
            )
        if not connection.sync_enabled:
            raise YazioConnectionDisabled(
                "Die automatische YAZIO-Synchronisierung ist deaktiviert."
            )
        if connection.historical_sync_state in {"pending", "running"}:
            raise YazioOperationCapacityExceeded(
                "Für dieses Konto läuft bereits ein YAZIO-Vorgang."
            )
        connection.historical_sync_kind = kind
        connection.historical_sync_state = "pending"
        connection.historical_sync_start_date = start_day
        connection.historical_sync_end_date = end_day
        connection.historical_sync_cursor_date = end_day if kind == "range" else None
        connection.historical_sync_started_at = None
        connection.historical_sync_completed_at = None
        connection.historical_sync_last_error = None
        connection.next_sync_at = requested_at
        db.commit()
        db.refresh(connection)
        return connection


def _yazio_operation_key(email: str) -> str:
    return security_reference("yazio_account", email.strip().casefold())


def _require_yazio_enabled() -> None:
    if not settings.yazio_enabled:
        raise YazioDisabled("Die YAZIO-Funktion ist auf diesem Server deaktiviert.")


def _ensure_yazio_circuit_closed() -> None:
    with SessionLocal() as db:
        try:
            ensure_rate_limit_available(
                db,
                YAZIO_CIRCUIT_ACTION,
                YAZIO_CIRCUIT_KEY,
                settings.yazio_circuit_failure_limit,
                settings.yazio_circuit_window_seconds,
            )
        except RateLimitExceeded as exc:
            raise YazioCircuitOpen(exc.retry_after) from exc


def _record_yazio_provider_failure() -> None:
    with SessionLocal() as db, suppress(RateLimitExceeded):
        check_rate_limit(
            db,
            YAZIO_CIRCUIT_ACTION,
            YAZIO_CIRCUIT_KEY,
            settings.yazio_circuit_failure_limit,
            settings.yazio_circuit_window_seconds,
        )


def _clear_yazio_provider_failures() -> None:
    with SessionLocal() as db:
        clear_rate_limit(db, YAZIO_CIRCUIT_ACTION, YAZIO_CIRCUIT_KEY)


def validate_yazio_credentials(
    email: str,
    password: str,
    *,
    operation_key: object | None = None,
) -> None:
    _require_yazio_enabled()
    _ensure_yazio_circuit_closed()
    try:
        with yazio_operation_slot(operation_key or _yazio_operation_key(email)):
            validate_yazio_credentials_transport(email, password)
    except YazioOperationBusy as exc:
        raise YazioOperationCapacityExceeded(
            "Es laufen bereits zu viele YAZIO-Vorgänge. Bitte später erneut versuchen."
        ) from exc
    except YazioTransportAuthenticationError as exc:
        raise YazioAuthenticationError(
            "YAZIO-Anmeldung fehlgeschlagen. Zugangsdaten prüfen."
        ) from exc
    except YazioTransportDeadlineError as exc:
        _record_yazio_provider_failure()
        raise YazioOperationDeadlineExceeded(
            "YAZIO hat nicht rechtzeitig geantwortet."
        ) from exc
    except YazioTransportError as exc:
        _record_yazio_provider_failure()
        raise YazioSyncError(
            "YAZIO ist vorübergehend nicht erreichbar."
        ) from exc
    _clear_yazio_provider_failures()


def fetch_yazio_payload(
    email: str,
    password: str,
    start_day: date,
    end_day: date,
    include_micronutrients: bool = True,
    *,
    operation_key: object | None = None,
) -> dict[str, Any]:
    try:
        with yazio_operation_slot(operation_key or _yazio_operation_key(email)):
            return _fetch_yazio_payload_unlocked(
                email,
                password,
                start_day,
                end_day,
                include_micronutrients,
            )
    except YazioOperationBusy as exc:
        raise YazioOperationCapacityExceeded(
            "Es laufen bereits zu viele YAZIO-Vorgänge. Bitte später erneut versuchen."
        ) from exc


def _fetch_yazio_payload_unlocked(
    email: str,
    password: str,
    start_day: date,
    end_day: date,
    include_micronutrients: bool,
) -> dict[str, Any]:
    _require_yazio_enabled()
    _ensure_yazio_circuit_closed()
    try:
        result = fetch_yazio_payload_transport(
            email,
            password,
            start_day,
            end_day,
            include_micronutrients,
        )
    except YazioTransportAuthenticationError as exc:
        raise YazioAuthenticationError(
            "YAZIO-Anmeldung fehlgeschlagen. Zugangsdaten aktualisieren."
        ) from exc
    except YazioTransportDeadlineError as exc:
        _record_yazio_provider_failure()
        raise YazioOperationDeadlineExceeded(
            "YAZIO-Abruf hat die maximale Laufzeit überschritten."
        ) from exc
    except YazioTransportError as exc:
        _record_yazio_provider_failure()
        raise YazioSyncError("YAZIO ist vorübergehend nicht erreichbar.") from exc
    _clear_yazio_provider_failures()
    return result


def import_yazio_payload(
    user: User,
    payload: dict[str, Any],
    source_identifier: str,
) -> ImportSummary:
    with SessionLocal() as db:
        attached_user = db.get(User, user.id)
        if attached_user is None or not attached_user.is_active:
            raise YazioSyncError("CaloGraph-Benutzer ist nicht aktiv.")
        result = parse_yazio_export(payload, attached_user.timezone, source_identifier)
        return persist_import(
            db,
            attached_user,
            result,
            None,
            "application/x-yazio-sync",
            "yazio-exporter",
        )


def sync_yazio_user(
    user: User,
    email: str,
    password: str,
    start_day: date,
    end_day: date,
    source_identifier: str | None = None,
    fetcher: YazioFetcher | None = None,
    *,
    include_micronutrients: bool = True,
) -> ImportSummary:
    try:
        with (
            yazio_operation_slot(user.id),
            SessionLocal() as db,
            _active_yazio_user_operation(db, user.id) as active_user,
        ):
            return _sync_yazio_user_unlocked(
                active_user,
                email,
                password,
                start_day,
                end_day,
                source_identifier,
                fetcher,
                include_micronutrients=include_micronutrients,
            )
    except YazioOperationBusy as exc:
        raise YazioOperationCapacityExceeded(
            "Es laufen bereits zu viele YAZIO-Vorgänge. Bitte später erneut versuchen."
        ) from exc


def _sync_yazio_user_unlocked(
    user: User,
    email: str,
    password: str,
    start_day: date,
    end_day: date,
    source_identifier: str | None,
    fetcher: YazioFetcher | None,
    *,
    include_micronutrients: bool,
) -> ImportSummary:
    if fetcher is None:
        payload = _fetch_yazio_payload_unlocked(
            email,
            password,
            start_day,
            end_day,
            include_micronutrients,
        )
    else:
        payload = fetcher(
            email,
            password,
            start_day,
            end_day,
            include_micronutrients,
        )
    identifier = source_identifier or yazio_source_identifier(user.id)
    summary = import_yazio_payload(user, payload, identifier)
    if (
        summary.failed > 0
        and summary.inserted == 0
        and summary.updated == 0
        and summary.skipped == 0
    ):
        raise YazioSyncError("YAZIO-Daten konnten nicht verarbeitet werden.")
    return summary


def configure_yazio_connection(
    user: User,
    email: str,
    password: str,
    *,
    sync_interval_minutes: int | None = None,
    sync_days: int | None = None,
) -> YazioConnection:
    _require_yazio_enabled()
    if sync_interval_minutes is not None and not 60 <= sync_interval_minutes <= 10080:
        raise ValueError("Das Sync-Intervall muss zwischen 60 und 10080 Minuten liegen.")
    if sync_days is not None and not 1 <= sync_days <= 366:
        raise ValueError("Die Anzahl der Sync-Tage muss zwischen 1 und 366 liegen.")

    with SessionLocal() as db, _active_yazio_user_operation(db, user.id) as attached_user:
        connection = db.scalar(
            select(YazioConnection).where(YazioConnection.user_id == attached_user.id)
        )
        is_new = connection is None
        if connection is None:
            connection = YazioConnection(user_id=attached_user.id)
            db.add(connection)
        connection.encrypted_email = encrypt_credential(email.strip())
        connection.encrypted_password = encrypt_credential(password)
        connection.source_identifier = yazio_source_identifier(attached_user.id)
        connection.sync_enabled = True
        if sync_interval_minutes is not None:
            connection.sync_interval_minutes = sync_interval_minutes
        if sync_days is not None:
            connection.sync_days = sync_days
        if is_new:
            connection.initial_sync_state = "pending"
            connection.historical_sync_kind = "initial"
            connection.historical_sync_state = "pending"
            connection.historical_sync_start_date = None
            connection.historical_sync_end_date = None
            connection.historical_sync_cursor_date = None
            connection.historical_sync_started_at = None
            connection.historical_sync_completed_at = None
            connection.historical_sync_last_error = None
            connection.next_sync_at = datetime.now(UTC)
        elif connection.initial_sync_state in {"pending", "running", "failed"}:
            connection.historical_sync_state = "pending"
            connection.historical_sync_last_error = None
            connection.next_sync_at = datetime.now(UTC)
        connection.last_error = None
        db.commit()
        db.refresh(connection)
        return connection


def due_yazio_connection_ids(
    now: datetime | None = None,
    *,
    limit: int = 20,
) -> list[UUID]:
    now = now or datetime.now(UTC)
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(YazioConnection.id)
                .join(User, User.id == YazioConnection.user_id)
                .where(
                    YazioConnection.sync_enabled.is_(True),
                    User.is_active.is_(True),
                    or_(
                        YazioConnection.next_sync_at.is_(None),
                        YazioConnection.next_sync_at <= now,
                    ),
                )
                .order_by(
                    YazioConnection.next_sync_at.is_not(None),
                    YazioConnection.next_sync_at,
                )
                .limit(limit)
            )
        )


def run_scheduled_yazio_sync(
    connection_id: UUID,
    *,
    fetcher: YazioFetcher | None = None,
    now: datetime | None = None,
) -> ImportSummary | None:
    if not settings.yazio_enabled:
        return None
    return _run_yazio_connection_sync(
        connection_id,
        fetcher=fetcher,
        now=now,
        require_enabled=True,
        raise_errors=False,
        mode="scheduled",
    )


def run_manual_yazio_sync(
    user_id: UUID,
    *,
    sync_days: int | None = None,
    fetcher: YazioFetcher | None = None,
    now: datetime | None = None,
) -> ImportSummary:
    _require_yazio_enabled()
    if sync_days is not None and not 1 <= sync_days <= 366:
        raise ValueError("Die Anzahl der Sync-Tage muss zwischen 1 und 366 liegen.")
    with SessionLocal() as db:
        connection_id = db.scalar(
            select(YazioConnection.id).where(YazioConnection.user_id == user_id)
        )
    if connection_id is None:
        raise YazioConnectionNotConfigured(
            "Für dieses Konto ist keine YAZIO-Verbindung eingerichtet."
        )

    summary = _run_yazio_connection_sync(
        connection_id,
        fetcher=fetcher,
        now=now,
        require_enabled=True,
        sync_days_override=sync_days,
        raise_errors=True,
        mode="manual",
    )
    if summary is None:
        raise YazioSyncError(
            "Die YAZIO-Synchronisierung ist fehlgeschlagen. Bitte den Datenstatus prüfen."
        )
    return summary


def _run_yazio_connection_sync(
    connection_id: UUID,
    *,
    fetcher: YazioFetcher | None,
    now: datetime | None,
    require_enabled: bool,
    sync_days_override: int | None = None,
    raise_errors: bool = False,
    mode: Literal["manual", "scheduled"],
) -> ImportSummary | None:
    attempted_at = now or datetime.now(UTC)
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if connection is None or (require_enabled and not connection.sync_enabled):
            return None
        user_id = connection.user_id
    try:
        with (
            yazio_operation_slot(user_id),
            SessionLocal() as db,
            _active_yazio_user_operation(db, user_id),
        ):
            return _run_yazio_connection_sync_locked(
                connection_id,
                fetcher=fetcher,
                attempted_at=attempted_at,
                require_enabled=require_enabled,
                sync_days_override=sync_days_override,
                raise_errors=raise_errors,
                mode=mode,
            )
    except (YazioOperationBusy, YazioOperationCapacityExceeded) as exc:
        log_security_event(
            "integration.yazio.sync_failed",
            actor_ref=security_reference("user", user_id),
            target_ref=security_reference("yazio_connection", connection_id),
            reason="capacity_exceeded",
            details={"mode": mode},
        )
        if raise_errors:
            if isinstance(exc, YazioOperationCapacityExceeded):
                raise
            raise YazioOperationCapacityExceeded(
                "Für dieses Konto läuft bereits ein YAZIO-Vorgang."
            ) from exc
        return None


def _run_historical_yazio_sync_locked(
    connection_id: UUID,
    *,
    fetcher: YazioFetcher | None,
    attempted_at: datetime,
) -> ImportSummary | None:
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if (
            connection is None
            or connection.historical_sync_state not in {"pending", "running", "failed"}
        ):
            return None
        user = db.get(User, connection.user_id)
        if user is None or not user.is_active:
            return None
        kind = connection.historical_sync_kind
        if kind not in {"initial", "full", "range"}:
            return None
        local_today = attempted_at.astimezone(ZoneInfo(user.timezone)).date()
        history_start: date | None
        history_end: date | None
        if kind in {"initial", "full"}:
            history_start = YAZIO_HISTORY_DISCOVERY_START
            history_end = connection.historical_sync_end_date or local_today
        else:
            history_start = connection.historical_sync_start_date
            history_end = connection.historical_sync_end_date
        if history_start is None or history_end is None:
            raise YazioSyncError("Der historische YAZIO-Auftrag ist ungültig.")
        end_day = connection.historical_sync_cursor_date or history_end
        if (end_day - history_start).days < MAX_YAZIO_RANGE_DAYS:
            start_day = history_start
        else:
            start_day = end_day - timedelta(days=MAX_YAZIO_RANGE_DAYS - 1)
        completed = start_day == history_start
        next_cursor = None if completed else start_day - timedelta(days=1)
        connection.historical_sync_start_date = history_start
        connection.historical_sync_end_date = history_end
        connection.historical_sync_state = "running"
        connection.historical_sync_started_at = (
            connection.historical_sync_started_at or attempted_at
        )
        if kind == "initial":
            connection.initial_sync_state = "running"
        connection.last_attempt_at = attempted_at
        connection.historical_sync_last_error = None
        db.commit()
        db.refresh(user)
        encrypted_email = connection.encrypted_email
        encrypted_password = connection.encrypted_password
        source_identifier = connection.source_identifier
        user_id = user.id
    try:
        email = decrypt_credential(encrypted_email)
        password = decrypt_credential(encrypted_password)
        with SessionLocal() as db:
            active_user = db.get(User, user_id)
            if active_user is None or not active_user.is_active:
                raise YazioSyncError("CaloGraph-Benutzer ist nicht aktiv.")
            summary = _sync_yazio_user_unlocked(
                active_user,
                email,
                password,
                start_day,
                end_day,
                source_identifier,
                fetcher,
                include_micronutrients=True,
            )
    except Exception as exc:
        _record_failure(connection_id, attempted_at, exc, historical=True)
        log_security_event(
            "integration.yazio.sync_failed",
            actor_ref=security_reference("user", user_id),
            target_ref=security_reference("yazio_connection", connection_id),
            reason=yazio_failure_reason(exc),
            details={"mode": kind},
        )
        return None
    completed_at = datetime.now(UTC)
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if connection is not None:
            connection.last_success_at = completed_at
            connection.last_micronutrient_sync_at = completed_at
            connection.last_error = None
            connection.historical_sync_cursor_date = next_cursor
            if completed:
                connection.historical_sync_state = "completed"
                connection.historical_sync_completed_at = completed_at
                if kind == "initial":
                    connection.initial_sync_state = "completed"
                connection.next_sync_at = _next_sync_at(
                    completed_at,
                    effective_sync_interval_minutes(connection),
                )
            else:
                connection.historical_sync_state = "pending"
                connection.next_sync_at = completed_at
            db.commit()
    log_security_event(
        "integration.yazio.sync_completed",
        actor_ref=security_reference("user", user_id),
        target_ref=security_reference("yazio_connection", connection_id),
        details={
            "mode": kind,
            "received": summary.received,
            "inserted": summary.inserted,
            "updated": summary.updated,
            "skipped": summary.skipped,
            "failed": summary.failed,
        },
    )
    return summary


def _run_yazio_connection_sync_locked(
    connection_id: UUID,
    *,
    fetcher: YazioFetcher | None,
    attempted_at: datetime,
    require_enabled: bool,
    sync_days_override: int | None,
    raise_errors: bool,
    mode: Literal["manual", "scheduled"],
) -> ImportSummary | None:
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if connection is None or (require_enabled and not connection.sync_enabled):
            return None
        user = db.get(User, connection.user_id)
        if user is None or not user.is_active:
            return None
        historical_pending = (
            mode == "scheduled"
            and connection.historical_sync_state in {"pending", "running", "failed"}
        )
        if not historical_pending:
            connection.last_attempt_at = attempted_at
            connection.next_sync_at = _next_sync_at(
                attempted_at,
                effective_sync_interval_minutes(connection),
            )
            db.commit()
            encrypted_email = connection.encrypted_email
            encrypted_password = connection.encrypted_password
            source_identifier = connection.source_identifier
            timezone = user.timezone
            sync_days = sync_days_override or effective_sync_days(connection)
            user_id = user.id
            last_micronutrient_sync_at = connection.last_micronutrient_sync_at
    if historical_pending:
        return _run_historical_yazio_sync_locked(
            connection_id,
            fetcher=fetcher,
            attempted_at=attempted_at,
        )
    try:
        email = decrypt_credential(encrypted_email)
        password = decrypt_credential(encrypted_password)
    except CredentialEncryptionError as exc:
        _record_failure(connection_id, attempted_at, exc)
        log_security_event(
            "integration.yazio.sync_failed",
            actor_ref=security_reference("user", user_id),
            target_ref=security_reference("yazio_connection", connection_id),
            reason="credential_decryption_error",
            details={"mode": mode},
        )
        if raise_errors:
            raise YazioSyncError(
                "Gespeicherte YAZIO-Zugangsdaten konnten nicht entschlüsselt werden."
            ) from exc
        return None
    if (
        last_micronutrient_sync_at is not None
        and last_micronutrient_sync_at.tzinfo is None
    ):
        last_micronutrient_sync_at = last_micronutrient_sync_at.replace(tzinfo=UTC)
    include_micronutrients = (
        not require_enabled
        or last_micronutrient_sync_at is None
        or attempted_at - last_micronutrient_sync_at
        >= MICRONUTRIENT_SYNC_INTERVAL
    )
    end_day = attempted_at.astimezone(ZoneInfo(timezone)).date()
    start_day = end_day - timedelta(days=sync_days - 1)
    try:
        summary = _sync_yazio_user_unlocked(
            user,
            email,
            password,
            start_day,
            end_day,
            source_identifier,
            fetcher,
            include_micronutrients=include_micronutrients,
        )
    except Exception as exc:
        _record_failure(connection_id, attempted_at, exc)
        log_security_event(
            "integration.yazio.sync_failed",
            actor_ref=security_reference("user", user_id),
            target_ref=security_reference("yazio_connection", connection_id),
            reason=yazio_failure_reason(exc),
            details={"mode": mode},
        )
        if raise_errors:
            if isinstance(exc, YazioSyncError):
                raise
            raise YazioSyncError(
                "Die YAZIO-Synchronisierung ist unerwartet fehlgeschlagen."
            ) from exc
        return None
    completed_at = datetime.now(UTC)
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if connection is not None:
            connection.last_success_at = completed_at
            if include_micronutrients:
                connection.last_micronutrient_sync_at = completed_at
            connection.last_error = None
            connection.next_sync_at = _next_sync_at(
                completed_at,
                effective_sync_interval_minutes(connection),
            )
            db.commit()
    log_security_event(
        "integration.yazio.sync_completed",
        actor_ref=security_reference("user", user_id),
        target_ref=security_reference("yazio_connection", connection_id),
        details={
            "mode": mode,
            "received": summary.received,
            "inserted": summary.inserted,
            "updated": summary.updated,
            "skipped": summary.skipped,
            "failed": summary.failed,
        },
    )
    return summary


def run_due_yazio_syncs(
    *,
    fetcher: YazioFetcher | None = None,
    now: datetime | None = None,
    after_connection: Callable[[], None] | None = None,
) -> tuple[int, int]:
    if not settings.yazio_enabled:
        return 0, 0
    connection_ids = due_yazio_connection_ids(now)
    succeeded = 0
    for connection_id in connection_ids:
        try:
            if run_scheduled_yazio_sync(connection_id, fetcher=fetcher, now=now) is not None:
                succeeded += 1
        finally:
            if after_connection is not None:
                after_connection()
    return len(connection_ids), succeeded


def _record_failure(
    connection_id: UUID,
    attempted_at: datetime,
    error: Exception,
    *,
    historical: bool = False,
) -> None:
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if connection is None:
            return
        retry_minutes = min(effective_sync_interval_minutes(connection), 60)
        if isinstance(error, CredentialEncryptionError):
            safe_error = "Gespeicherte YAZIO-Zugangsdaten konnten nicht entschlüsselt werden."
        elif isinstance(error, YazioSyncError):
            safe_error = str(error)[:500]
        else:
            safe_error = "Unerwarteter Fehler bei der Synchronisierung."
        if historical:
            connection.historical_sync_state = "failed"
            connection.historical_sync_last_error = safe_error
            if connection.historical_sync_kind == "initial":
                connection.initial_sync_state = "failed"
        if isinstance(error, YazioAuthenticationError):
            connection.sync_enabled = False
            connection.last_error = safe_error
            connection.next_sync_at = None
        else:
            connection.last_error = safe_error
            connection.next_sync_at = _next_sync_at(attempted_at, retry_minutes)
        db.commit()
