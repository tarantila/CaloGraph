import hashlib
import logging
import secrets
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from app.config import settings
from app.database import SessionLocal
from app.importers.yazio import parse_yazio_export
from app.models import User, YazioConnection
from app.schemas import ImportSummary
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
from app.services.yazio_guard import YazioOperationBusy, yazio_operation_slot
from app.services.yazio_transport import (
    YazioTransportAuthenticationError,
    YazioTransportDeadlineError,
    YazioTransportError,
    fetch_yazio_payload_transport,
    validate_yazio_credentials_transport,
)

logger = logging.getLogger("calograph.yazio_sync")

YazioFetcher = Callable[[str, str, date, date, bool], dict[str, Any]]
MICRONUTRIENT_SYNC_INTERVAL = timedelta(hours=24)
YAZIO_CIRCUIT_ACTION = "yazio-provider-failure"
YAZIO_CIRCUIT_KEY = "provider:yzapi.yazio.com"


class YazioSyncError(RuntimeError):
    pass


class YazioConnectionNotConfigured(YazioSyncError):
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


def _next_sync_at(reference: datetime, interval_minutes: int) -> datetime:
    max_jitter = settings.yazio_scheduler_jitter_minutes
    jitter_minutes = secrets.randbelow(max_jitter) + 1 if max_jitter else 0
    return reference + timedelta(minutes=interval_minutes + jitter_minutes)


def yazio_account_hash(email: str) -> str:
    return hashlib.sha256(email.strip().casefold().encode()).hexdigest()


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
        with yazio_operation_slot(operation_key or yazio_account_hash(email)):
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
    _require_yazio_enabled()
    _ensure_yazio_circuit_closed()
    try:
        with yazio_operation_slot(operation_key or yazio_account_hash(email)):
            result = fetch_yazio_payload_transport(
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
    if fetcher is None:
        payload = fetch_yazio_payload(
            email,
            password,
            start_day,
            end_day,
            include_micronutrients,
            operation_key=user.id,
        )
    else:
        payload = fetcher(
            email,
            password,
            start_day,
            end_day,
            include_micronutrients,
        )
    identifier = source_identifier or f"yazio:{yazio_account_hash(email)[:16]}"
    return import_yazio_payload(user, payload, identifier)


def configure_yazio_connection(
    user: User,
    email: str,
    password: str,
    *,
    sync_interval_minutes: int = 360,
    sync_days: int = 7,
) -> YazioConnection:
    _require_yazio_enabled()
    if not 60 <= sync_interval_minutes <= 10080:
        raise ValueError("Das Sync-Intervall muss zwischen 60 und 10080 Minuten liegen.")
    if not 1 <= sync_days <= 366:
        raise ValueError("Die Anzahl der Sync-Tage muss zwischen 1 und 366 liegen.")

    account_hash = yazio_account_hash(email)
    with SessionLocal() as db:
        attached_user = db.get(User, user.id)
        if attached_user is None:
            raise ValueError("CaloGraph-Benutzer nicht gefunden.")
        connection = db.scalar(
            select(YazioConnection).where(YazioConnection.user_id == attached_user.id)
        )
        if connection is None:
            connection = YazioConnection(user_id=attached_user.id)
            db.add(connection)
        connection.encrypted_email = encrypt_credential(email.strip())
        connection.encrypted_password = encrypt_credential(password)
        connection.account_hash = account_hash
        connection.source_identifier = f"yazio:{account_hash[:16]}"
        connection.sync_enabled = True
        connection.sync_interval_minutes = sync_interval_minutes
        connection.sync_days = sync_days
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
) -> ImportSummary | None:
    attempted_at = now or datetime.now(UTC)
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if connection is None or (require_enabled and not connection.sync_enabled):
            return None
        user = db.get(User, connection.user_id)
        if user is None or not user.is_active:
            return None
        connection.last_attempt_at = attempted_at
        connection.next_sync_at = _next_sync_at(
            attempted_at,
            connection.sync_interval_minutes,
        )
        db.commit()
        try:
            email = decrypt_credential(connection.encrypted_email)
            password = decrypt_credential(connection.encrypted_password)
        except CredentialEncryptionError as exc:
            _record_failure(connection_id, attempted_at, exc)
            if raise_errors:
                raise YazioSyncError(
                    "Gespeicherte YAZIO-Zugangsdaten konnten nicht entschlüsselt werden."
                ) from exc
            return None
        timezone = user.timezone
        sync_days = sync_days_override or connection.sync_days
        source_identifier = connection.source_identifier
        username = user.username
        last_micronutrient_sync_at = connection.last_micronutrient_sync_at

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
        summary = sync_yazio_user(
            user,
            email,
            password,
            start_day,
            end_day,
            source_identifier,
            fetcher=fetcher,
            include_micronutrients=include_micronutrients,
        )
    except Exception as exc:
        _record_failure(connection_id, attempted_at, exc)
        logger.warning(
            "scheduled_yazio_sync_failed connection_id=%s error_type=%s",
            connection_id,
            type(exc).__name__,
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
                connection.sync_interval_minutes,
            )
            db.commit()
    logger.info(
        "scheduled_yazio_sync_completed username=%s inserted=%s updated=%s skipped=%s",
        username,
        summary.inserted,
        summary.updated,
        summary.skipped,
    )
    return summary


def run_due_yazio_syncs(
    *,
    fetcher: YazioFetcher | None = None,
    now: datetime | None = None,
) -> tuple[int, int]:
    if not settings.yazio_enabled:
        return 0, 0
    connection_ids = due_yazio_connection_ids(now)
    succeeded = 0
    for connection_id in connection_ids:
        if run_scheduled_yazio_sync(connection_id, fetcher=fetcher, now=now) is not None:
            succeeded += 1
    return len(connection_ids), succeeded


def _record_failure(
    connection_id: UUID,
    attempted_at: datetime,
    error: Exception,
) -> None:
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if connection is None:
            return
        retry_minutes = min(connection.sync_interval_minutes, 60)
        if isinstance(error, YazioAuthenticationError):
            connection.sync_enabled = False
            connection.last_error = str(error)[:500]
            connection.next_sync_at = None
        elif isinstance(error, (CredentialEncryptionError, YazioSyncError)):
            connection.last_error = str(error)[:500]
            connection.next_sync_at = _next_sync_at(attempted_at, retry_minutes)
        else:
            connection.last_error = (
                f"Unerwarteter Fehler bei der Synchronisierung ({type(error).__name__})."
            )
            connection.next_sync_at = _next_sync_at(attempted_at, retry_minutes)
        db.commit()
