import logging
import secrets
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.importers.yazio import parse_yazio_export
from app.models import User, YazioConnection
from app.nutrition.models import NutritionIngestionRun
from app.schemas import ImportSummary
from app.security_events import log_security_event, security_reference
from app.services.credential_crypto import (
    CredentialEncryptionError,
    decrypt_credential,
    encrypt_credential,
)
from app.services.import_service import _persist_import_locked
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
from app.services.yazio_nutrition_ingestion import ingest_yazio_food_diary
from app.services.yazio_projection_retry import (
    acknowledge_yazio_projection_run,
    drain_pending_yazio_projections,
    rebuild_yazio_projection_run,
)
from app.services.yazio_provider import (
    YazioProviderAuthenticationError,
    YazioProviderDeadlineError,
    YazioProviderError,
    YazioProviderInvalidResponseError,
    YazioProviderNetworkTimeoutError,
    YazioProviderRateLimitedError,
    YazioProviderUnavailableError,
)
from app.services.yazio_transport import (
    YazioTransportAuthenticationError,
    YazioTransportDeadlineError,
    YazioTransportError,
    YazioTransportInvalidResponseError,
    YazioTransportNetworkTimeoutError,
    YazioTransportRateLimitedError,
    YazioTransportUnavailableError,
    YazioTransportVersionBlockedError,
    fetch_yazio_domain_transport,
    validate_yazio_credentials_transport,
)


@dataclass(frozen=True, slots=True)
class _YazioRangeSyncResult:
    summary: ImportSummary
    ingestion_run_id: UUID
    user_id: UUID
    source_instance_id: UUID


@dataclass(frozen=True, slots=True)
class _YazioHistoryAttempt:
    start_date: date
    end_date: date
    cursor_date: date | None
    started_at: datetime
    next_cursor_date: date | None
    completed: bool


YAZIO_DAILY_SYNC_FRESHNESS = timedelta(minutes=30)
YAZIO_INTER_ACCOUNT_DELAY_SECONDS = 90
YAZIO_CIRCUIT_ACTION = "yazio-provider-failure"
YAZIO_CIRCUIT_KEY = "provider:yzapi.yazio.com"
YAZIO_VERSION_BLOCKED_MESSAGE = (
    "Der von CaloGraph verwendete YAZIO-Client wird von YAZIO nicht mehr akzeptiert. "
    "Prüfe, ob eine neuere CaloGraph-Version verfügbar ist."
)

logger = logging.getLogger(__name__)


class YazioSyncError(RuntimeError):
    def __init__(
        self,
        message: str | None = None,
        *,
        provider_context: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message or "YAZIO synchronization failed")
        self.provider_context = (
            dict(provider_context) if provider_context is not None else None
        )


class YazioConnectionNotConfigured(YazioSyncError):
    pass


class YazioSdkNotConfigured(YazioSyncError):
    pass


class YazioConnectionDisabled(YazioSyncError):
    pass


class YazioDisabled(YazioSyncError):
    pass


class YazioAuthenticationError(YazioSyncError):
    pass


class YazioVersionBlockedError(YazioSyncError):
    pass


class YazioRateLimitedError(YazioSyncError):
    def __init__(self, retry_after: int | None = None) -> None:
        self.retry_after = retry_after
        super().__init__("YAZIO-Anbieter begrenzt die Anfragen vorübergehend.")


class YazioUnavailableError(YazioSyncError):
    pass


class YazioNetworkTimeoutError(YazioSyncError):
    pass


class YazioInvalidResponseError(YazioSyncError):
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
    if isinstance(error, YazioVersionBlockedError):
        return "version_blocked"
    if isinstance(error, YazioRateLimitedError):
        return "rate_limited"
    if isinstance(error, YazioNetworkTimeoutError):
        return "network_timeout"
    if isinstance(error, YazioInvalidResponseError):
        return "invalid_response"
    if isinstance(error, YazioUnavailableError):
        return "unavailable"
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


def _with_provider_context(
    mapped: YazioSyncError,
    error: YazioProviderError,
) -> YazioSyncError:
    mapped.provider_context = (
        error.context.to_dict() if error.context is not None else None
    )
    return mapped

def _with_transport_context(
    mapped: YazioSyncError,
    error: YazioTransportError,
) -> YazioSyncError:
    mapped.provider_context = error.context
    return mapped


def _map_yazio_provider_error(error: YazioProviderError) -> YazioSyncError:
    if isinstance(error, YazioProviderAuthenticationError):
        return _with_provider_context(
            YazioAuthenticationError("YAZIO-Anmeldung fehlgeschlagen. Zugangsdaten prüfen."),
            error,
        )
    if error.kind == "version_blocked":
        return _with_provider_context(YazioVersionBlockedError(YAZIO_VERSION_BLOCKED_MESSAGE), error)
    if isinstance(error, YazioProviderRateLimitedError):
        return _with_provider_context(YazioRateLimitedError(error.retry_after), error)
    if isinstance(error, YazioProviderNetworkTimeoutError):
        return _with_provider_context(
            YazioNetworkTimeoutError("YAZIO hat nicht rechtzeitig geantwortet."), error
        )
    if isinstance(error, YazioProviderDeadlineError):
        return _with_provider_context(
            YazioOperationDeadlineExceeded("YAZIO hat nicht rechtzeitig geantwortet."), error
        )
    if isinstance(error, YazioProviderInvalidResponseError):
        return _with_provider_context(
            YazioInvalidResponseError("YAZIO hat eine ungültige Antwort geliefert."), error
        )
    if isinstance(error, YazioProviderUnavailableError):
        return _with_provider_context(
            YazioUnavailableError("YAZIO ist vorübergehend nicht erreichbar."), error
        )
    return _with_provider_context(
        YazioUnavailableError("YAZIO ist vorübergehend nicht erreichbar."), error
    )


def _provider_context_details(error: Exception) -> dict[str, object]:
    context = getattr(error, "provider_context", None)
    if not isinstance(context, Mapping):
        return {}
    details: dict[str, object] = {}
    for source_key, detail_key in (
        ("operation", "provider_operation"),
        ("endpoint_key", "provider_endpoint"),
        ("response_model", "provider_model"),
        ("error_category", "provider_error_category"),
    ):
        value = context.get(source_key)
        if isinstance(value, str) and value:
            if source_key == "response_model":
                value = value.replace("[", "").replace("]", "")
            details[detail_key] = value[:64]
    location = context.get("validation_location")
    if isinstance(location, str) and location:
        details["provider_validation_location"] = location.replace("[", "").replace("]", "")[:64]
    status = context.get("upstream_status_code")
    if isinstance(status, int) and not isinstance(status, bool) and status >= 0:
        details["provider_status"] = status
    retryable = context.get("retryable")
    if isinstance(retryable, bool):
        details["provider_retryable"] = retryable
    return details

def _next_sync_at(reference: datetime, interval_minutes: int) -> datetime:
    max_jitter = settings.yazio_scheduler_jitter_minutes
    jitter_minutes = secrets.randbelow(max_jitter) + 1 if max_jitter else 0
    return reference + timedelta(minutes=interval_minutes + jitter_minutes)


def yazio_source_identifier(user_id: UUID) -> str:
    return f"yazio:{user_id}"

YAZIO_HISTORY_DISCOVERY_START = date(2000, 1, 1)
MAX_YAZIO_RANGE_DAYS = 366



def effective_sync_interval_minutes(connection: YazioConnection) -> int:
    return connection.sync_interval_minutes or settings.yazio_sync_interval_hours * 60


def effective_sync_days(connection: YazioConnection) -> int:
    return connection.sync_days or settings.yazio_sync_days


def _sync_yazio_connection_range_locked(
    connection_id: UUID,
    *,
    start_day: date,
    end_day: date,
) -> _YazioRangeSyncResult:
    """Commit SDK evidence and replay projections under the caller's operation locks."""
    _require_yazio_enabled()
    _require_yazio_sdk_configured()
    if start_day > end_day:
        raise ValueError("start_day must not be after end_day")
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if connection is None:
            raise YazioConnectionNotConfigured("Keine YAZIO-Verbindung konfiguriert.")
        user = db.get(User, connection.user_id)
        if user is None or not user.is_active:
            raise YazioSyncError("CaloGraph-Benutzer ist nicht aktiv.")
        user_id = user.id
        source_identifier = connection.source_identifier
        encrypted_email = connection.encrypted_email
        encrypted_password = connection.encrypted_password
    try:
        email = decrypt_credential(encrypted_email)
        password = decrypt_credential(encrypted_password)
    except CredentialEncryptionError as exc:
        raise YazioSyncError(
            "Gespeicherte YAZIO-Zugangsdaten konnten nicht entschlüsselt werden."
        ) from exc

    drain_pending_yazio_projections(
        session_factory=SessionLocal,
        user_id=user_id,
        source_instance_id=connection_id,
    )
    _ensure_yazio_circuit_closed()
    # Only transport failures affect provider classification and its circuit.
    try:
        aggregate_payload, diary = fetch_yazio_domain_transport(
            email, password, start_day, end_day,
        )
    except YazioProviderError as exc:
        if exc.kind != "authentication":
            _record_yazio_provider_failure()
        raise _map_yazio_provider_error(exc) from exc
    _clear_yazio_provider_failures()

    with SessionLocal() as db:
        try:
            connection = db.get(YazioConnection, connection_id)
            if (
                connection is None
                or connection.user_id != user_id
                or connection.source_identifier != source_identifier
                or connection.encrypted_email != encrypted_email
                or connection.encrypted_password != encrypted_password
            ):
                raise YazioSyncError("YAZIO-Verbindung ist nicht mehr verfügbar.")
            active_user = db.get(User, user_id)
            if active_user is None or not active_user.is_active:
                raise YazioSyncError("CaloGraph-Benutzer ist nicht aktiv.")
            result = parse_yazio_export(
                aggregate_payload, active_user.timezone, source_identifier,
            )
            summary = _persist_import_locked(
                db,
                active_user,
                result,
                None,
                "application/x-yazio-sync",
                "yazio-sdk",
                connector_variant="sdk-v22",
                shared_transaction=True,
            )
            if (
                summary.failed > 0
                and summary.inserted == 0
                and summary.updated == 0
                and summary.skipped == 0
            ):
                raise YazioSyncError("YAZIO-Daten konnten nicht verarbeitet werden.")
            run = ingest_yazio_food_diary(
                db,
                user_id=user_id,
                source_instance_id=connection_id,
                requested_start=start_day,
                requested_end=end_day,
                diary=diary,
            )
            run.projection_pending = True
            ingestion_run_id = run.id
            db.commit()
        except Exception:
            db.rollback()
            raise

    log_security_event(
        "import.started",
        actor_ref=security_reference("user", user_id),
        target_ref=security_reference("import_batch", summary.batch_id),
        details={"source_type": "yazio_export_v1"},
    )
    rebuild_yazio_projection_run(
        session_factory=SessionLocal,
        user_id=user_id,
        source_instance_id=connection_id,
        ingestion_run_id=ingestion_run_id,
        policy_at=datetime.now(UTC),
    )
    return _YazioRangeSyncResult(
        summary=summary,
        ingestion_run_id=ingestion_run_id,
        user_id=user_id,
        source_instance_id=connection_id,
    )


def _validate_historical_range(
    start_day: date | None,
    end_day: date | None,
    *,
    local_today: date,
) -> tuple[date, date]:
    if start_day is None or end_day is None:
        raise ValueError(
            "Für den historischen YAZIO-Import müssen Von- und Bis-Datum angegeben werden."
        )
    if start_day > end_day:
        raise ValueError("Das Von-Datum darf nicht nach dem Bis-Datum liegen.")
    if start_day < YAZIO_HISTORY_DISCOVERY_START:
        raise ValueError(
            "Der historische YAZIO-Import kann frühestens am 2000-01-01 beginnen."
        )
    if end_day > local_today:
        raise ValueError("Das Bis-Datum darf nicht in der Zukunft liegen.")
    return start_day, end_day


def enqueue_historical_yazio_sync(
    user_id: UUID,
    *,
    start_day: date,
    end_day: date,
    now: datetime | None = None,
) -> YazioConnection:
    _require_yazio_enabled()
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
        user = db.get(User, connection.user_id)
        if user is None:
            raise YazioConnectionNotConfigured(
                "Für dieses Konto ist keine YAZIO-Verbindung eingerichtet."
            )
        start_day, end_day = _validate_historical_range(
            start_day,
            end_day,
            local_today=requested_at.astimezone(ZoneInfo(user.timezone)).date(),
        )
        if connection.historical_sync_state in {"pending", "running"}:
            raise YazioOperationCapacityExceeded(
                "Für dieses Konto läuft bereits ein YAZIO-Vorgang."
            )
        connection.historical_sync_state = "pending"
        connection.historical_sync_start_date = start_day
        connection.historical_sync_end_date = end_day
        connection.historical_sync_cursor_date = end_day
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


def _require_yazio_sdk_configured() -> None:
    if not settings.yazio_sdk_client_secret.strip():
        raise YazioSdkNotConfigured("Der YAZIO-SDK ist nicht konfiguriert.")

def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def queue_daily_yazio_sync_if_due(
    db: Session,
    user: User,
    *,
    now: datetime | None = None,
) -> bool:
    if not settings.yazio_enabled or not settings.yazio_scheduler_enabled or not user.is_active:
        return False

    connection = db.scalar(
        select(YazioConnection)
        .where(YazioConnection.user_id == user.id)
        .with_for_update()
    )
    if connection is None or not connection.sync_enabled:
        return False
    if connection.historical_sync_state in {"pending", "running"}:
        return False

    current_time = _utc_datetime(now or datetime.now(UTC))
    local_today = current_time.astimezone(ZoneInfo(user.timezone)).date()
    if connection.last_daily_sync_trigger_date == local_today:
        return False

    connection.last_daily_sync_trigger_date = local_today
    last_success_at = (
        _utc_datetime(connection.last_success_at)
        if connection.last_success_at is not None
        else None
    )
    if (
        last_success_at is not None
        and current_time - last_success_at < YAZIO_DAILY_SYNC_FRESHNESS
    ):
        db.commit()
        return True

    if connection.next_sync_at is None or _utc_datetime(connection.next_sync_at) > current_time:
        connection.next_sync_at = current_time
    db.commit()
    return True


def queue_daily_yazio_sync_best_effort(
    user_id: UUID,
    *,
    now: datetime | None = None,
) -> None:
    try:
        with SessionLocal() as db:
            try:
                user = db.get(User, user_id)
                if user is None:
                    return
                queue_daily_yazio_sync_if_due(db, user, now=now)
            except Exception:
                db.rollback()
                raise
    except Exception as exc:
        logger.warning(
            "yazio_daily_trigger_failed user_ref=%s error_type=%s",
            security_reference("user", user_id),
            type(exc).__name__,
        )


def _sleep_between_yazio_connections() -> None:
    time.sleep(YAZIO_INTER_ACCOUNT_DELAY_SECONDS)


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
    _require_yazio_sdk_configured()
    _ensure_yazio_circuit_closed()
    try:
        with yazio_operation_slot(operation_key or _yazio_operation_key(email)):
            validate_yazio_credentials_transport(email, password)
    except YazioOperationBusy as exc:
        raise YazioOperationCapacityExceeded(
            "Es laufen bereits zu viele YAZIO-Vorgänge. Bitte später erneut versuchen."
        ) from exc
    except YazioTransportAuthenticationError as exc:
        raise _with_transport_context(
            YazioAuthenticationError(
                "YAZIO-Anmeldung fehlgeschlagen. Zugangsdaten prüfen."
            ),
            exc,
        ) from exc
    except YazioTransportVersionBlockedError as exc:
        _record_yazio_provider_failure()
        raise _with_transport_context(
            YazioVersionBlockedError(YAZIO_VERSION_BLOCKED_MESSAGE),
            exc,
        ) from exc
    except YazioTransportRateLimitedError as exc:
        _record_yazio_provider_failure()
        raise _with_transport_context(
            YazioRateLimitedError(exc.retry_after),
            exc,
        ) from exc
    except YazioTransportNetworkTimeoutError as exc:
        _record_yazio_provider_failure()
        raise _with_transport_context(
            YazioNetworkTimeoutError("YAZIO hat nicht rechtzeitig geantwortet."),
            exc,
        ) from exc
    except YazioTransportInvalidResponseError as exc:
        _record_yazio_provider_failure()
        raise _with_transport_context(
            YazioInvalidResponseError("YAZIO hat eine ungültige Antwort geliefert."),
            exc,
        ) from exc
    except YazioTransportUnavailableError as exc:
        _record_yazio_provider_failure()
        raise _with_transport_context(
            YazioUnavailableError("YAZIO ist vorübergehend nicht erreichbar."),
            exc,
        ) from exc
    except YazioTransportDeadlineError as exc:
        _record_yazio_provider_failure()
        raise _with_transport_context(
            YazioOperationDeadlineExceeded("YAZIO hat nicht rechtzeitig geantwortet."),
            exc,
        ) from exc
    except YazioTransportError as exc:
        _record_yazio_provider_failure()
        raise _with_transport_context(
            YazioUnavailableError("YAZIO ist vorübergehend nicht erreichbar."),
            exc,
        ) from exc
    _clear_yazio_provider_failures()


def configure_yazio_connection(
    user: User,
    email: str,
    password: str,
    *,
    sync_interval_minutes: int | None = None,
    sync_days: int | None = None,
    start_day: date | None = None,
    end_day: date | None = None,
    now: datetime | None = None,
) -> YazioConnection:
    _require_yazio_enabled()
    if sync_interval_minutes is not None and not 60 <= sync_interval_minutes <= 10080:
        raise ValueError("Das Sync-Intervall muss zwischen 60 und 10080 Minuten liegen.")
    if sync_days is not None and not 1 <= sync_days <= 366:
        raise ValueError("Die Anzahl der Sync-Tage muss zwischen 1 und 366 liegen.")
    if (start_day is None) != (end_day is None):
        raise ValueError(
            "Für den historischen YAZIO-Import müssen Von- und Bis-Datum "
            "zusammen angegeben werden."
        )

    configured_at = now or datetime.now(UTC)
    with SessionLocal() as db, _active_yazio_user_operation(db, user.id) as attached_user:
        connection = db.scalar(
            select(YazioConnection).where(YazioConnection.user_id == attached_user.id)
        )
        if connection is None:
            start_day, end_day = _validate_historical_range(
                start_day,
                end_day,
                local_today=configured_at.astimezone(
                    ZoneInfo(attached_user.timezone)
                ).date(),
            )
            connection = YazioConnection(user_id=attached_user.id)
            db.add(connection)
            queue_historical_sync = True
        else:
            queue_historical_sync = start_day is not None
            if queue_historical_sync:
                start_day, end_day = _validate_historical_range(
                    start_day,
                    end_day,
                    local_today=configured_at.astimezone(
                        ZoneInfo(attached_user.timezone)
                    ).date(),
                )
        connection.encrypted_email = encrypt_credential(email.strip())
        connection.encrypted_password = encrypt_credential(password)
        connection.source_identifier = yazio_source_identifier(attached_user.id)
        connection.sync_enabled = True
        if sync_interval_minutes is not None:
            connection.sync_interval_minutes = sync_interval_minutes
        if sync_days is not None:
            connection.sync_days = sync_days
        if queue_historical_sync:
            connection.historical_sync_state = "pending"
            connection.historical_sync_start_date = start_day
            connection.historical_sync_end_date = end_day
            connection.historical_sync_cursor_date = end_day
            connection.historical_sync_started_at = None
            connection.historical_sync_completed_at = None
            connection.historical_sync_last_error = None
            connection.next_sync_at = configured_at
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
    now: datetime | None = None,
) -> ImportSummary | None:
    if not settings.yazio_enabled or not settings.yazio_scheduler_enabled:
        return None
    return _run_yazio_connection_sync(
        connection_id, now=now, raise_errors=False, mode="scheduled",
    )


def run_manual_yazio_sync(
    user_id: UUID,
    *,
    sync_days: int | None = None,
    start_day: date | None = None,
    end_day: date | None = None,
    now: datetime | None = None,
) -> ImportSummary:
    _require_yazio_enabled()
    if sync_days is not None and not 1 <= sync_days <= 366:
        raise ValueError("Die Anzahl der Sync-Tage muss zwischen 1 und 366 liegen.")
    if start_day is not None or end_day is not None:
        if start_day is None or end_day is None:
            raise ValueError("Von- und Bis-Datum müssen gemeinsam angegeben werden.")
        if sync_days is not None:
            raise ValueError("Ein Datumsbereich und Sync-Tage schließen sich aus.")
        if start_day > end_day or (end_day - start_day).days >= 366:
            raise ValueError("Der Sync-Zeitraum muss zwischen 1 und 366 Tagen liegen.")
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
        now=now,
        sync_days_override=sync_days,
        start_day=start_day,
        end_day=end_day,
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
    now: datetime | None,
    sync_days_override: int | None = None,
    start_day: date | None = None,
    end_day: date | None = None,
    raise_errors: bool,
    mode: Literal["manual", "scheduled"],
) -> ImportSummary | None:
    attempted_at = _utc_datetime(now or datetime.now(UTC))
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if connection is None:
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
                attempted_at=attempted_at,
                sync_days_override=sync_days_override,
                start_day=start_day,
                end_day=end_day,
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


def _history_attempt_matches(
    connection: YazioConnection, history: _YazioHistoryAttempt,
) -> bool:
    return (
        connection.historical_sync_state == "running"
        and connection.historical_sync_start_date == history.start_date
        and connection.historical_sync_end_date == history.end_date
        and connection.historical_sync_cursor_date == history.cursor_date
        and connection.historical_sync_started_at is not None
        and _utc_datetime(connection.historical_sync_started_at) == history.started_at
    )


def _finalize_yazio_connection_sync(
    connection_id: UUID,
    *,
    result: _YazioRangeSyncResult,
    completed_at: datetime,
    history: _YazioHistoryAttempt | None = None,
) -> None:
    with SessionLocal() as db:
        try:
            connection = db.scalar(
                select(YazioConnection).where(
                    YazioConnection.id == connection_id,
                    YazioConnection.user_id == result.user_id,
                ).with_for_update()
            )
            if connection is None or result.source_instance_id != connection_id:
                raise YazioSyncError("YAZIO-Verbindung ist nicht mehr verfügbar.")
            if history is not None and not _history_attempt_matches(connection, history):
                raise YazioSyncError("Der historische YAZIO-Auftrag wurde ersetzt.")
            acknowledge_yazio_projection_run(
                db,
                user_id=result.user_id,
                source_instance_id=result.source_instance_id,
                ingestion_run_id=result.ingestion_run_id,
            )
            connection.last_success_at = completed_at
            connection.last_error = None
            connection.next_sync_at = _next_sync_at(
                completed_at, effective_sync_interval_minutes(connection),
            )
            if history is not None:
                connection.historical_sync_cursor_date = history.next_cursor_date
                connection.historical_sync_last_error = None
                connection.historical_sync_state = "completed" if history.completed else "pending"
                connection.historical_sync_completed_at = completed_at if history.completed else None
                if not history.completed:
                    connection.next_sync_at = completed_at
            db.commit()
        except Exception:
            db.rollback()
            raise


def _yazio_result_is_acknowledged(result: _YazioRangeSyncResult) -> bool:
    # A commit exception can mean that COMMIT succeeded but its reply was lost.
    # Never overwrite the atomically committed status/cursor in that case.
    with SessionLocal() as db:
        pending = db.scalar(
            select(NutritionIngestionRun.projection_pending).where(
                NutritionIngestionRun.id == result.ingestion_run_id,
                NutritionIngestionRun.user_id == result.user_id,
                NutritionIngestionRun.source_instance_id == result.source_instance_id,
                NutritionIngestionRun.provider_key == "yazio",
                NutritionIngestionRun.connector_variant == "sdk-v22",
            )
        )
        return pending is False


def _is_yazio_fetch_failure(error: Exception) -> bool:
    return isinstance(error.__cause__, (YazioProviderError, CredentialEncryptionError)) or isinstance(
        error, (YazioCircuitOpen, YazioSdkNotConfigured),
    )


def _run_yazio_connection_sync_locked(
    connection_id: UUID,
    *,
    attempted_at: datetime,
    sync_days_override: int | None,
    raise_errors: bool,
    mode: Literal["manual", "scheduled"],
    start_day: date | None = None,
    end_day: date | None = None,
) -> ImportSummary | None:
    history = None
    result = None
    phase = "attempt"
    history_start: date | None = None
    history_end: date | None = None
    history_started_at: datetime | None = None
    completed = False
    with SessionLocal() as db:
        connection = db.get(YazioConnection, connection_id)
        if connection is None or not connection.sync_enabled:
            return None
        user = db.get(User, connection.user_id)
        if user is None or not user.is_active:
            raise YazioSyncError("CaloGraph-Benutzer ist nicht aktiv.")
        user_id = user.id
        historical_pending = (
            mode == "scheduled"
            and connection.historical_sync_state in {"pending", "running", "failed"}
        )
    try:
        with SessionLocal() as db:
            connection = db.get(YazioConnection, connection_id)
            if connection is None:
                raise YazioConnectionNotConfigured("Keine YAZIO-Verbindung konfiguriert.")
            connection.last_attempt_at = attempted_at
            if historical_pending:
                history_start = connection.historical_sync_start_date
                history_end = connection.historical_sync_end_date
                if history_start is None or history_end is None:
                    raise YazioSyncError("Der historische YAZIO-Auftrag ist ungültig.")
                end_day = connection.historical_sync_cursor_date or history_end
                start_day = max(
                    history_start, end_day - timedelta(days=MAX_YAZIO_RANGE_DAYS - 1),
                )
                completed = start_day == history_start
                connection.historical_sync_state = "running"
                history_started_at = connection.historical_sync_started_at or attempted_at
                connection.historical_sync_started_at = history_started_at
                connection.historical_sync_last_error = None
            else:
                connection.next_sync_at = _next_sync_at(
                    attempted_at, effective_sync_interval_minutes(connection),
                )
                if start_day is None or end_day is None:
                    end_day = attempted_at.astimezone(ZoneInfo(user.timezone)).date()
                    start_day = end_day - timedelta(
                        days=(sync_days_override or effective_sync_days(connection)) - 1,
                    )
            db.commit()
            if historical_pending:
                if (
                    history_start is None
                    or history_end is None
                    or history_started_at is None
                ):
                    raise YazioSyncError("Der historische YAZIO-Auftrag ist ungültig.")
                history = _YazioHistoryAttempt(
                    start_date=history_start,
                    end_date=history_end,
                    cursor_date=connection.historical_sync_cursor_date,
                    started_at=_utc_datetime(history_started_at),
                    next_cursor_date=None if completed else start_day - timedelta(days=1),
                    completed=completed,
                )
        phase = "range"
        result = _sync_yazio_connection_range_locked(
            connection_id, start_day=start_day, end_day=end_day,
        )
        phase = "finalize"
        _finalize_yazio_connection_sync(
            connection_id, result=result, completed_at=datetime.now(UTC), history=history,
        )
    except Exception as exc:
        if result is None or not _yazio_result_is_acknowledged(result):
            _record_failure(
                connection_id, attempted_at, exc,
                historical=historical_pending, expected_history=history,
            )
        credential_error = isinstance(exc.__cause__, CredentialEncryptionError)
        provider_failure = phase == "range" and _is_yazio_fetch_failure(exc)
        log_security_event(
            "integration.yazio.sync_failed",
            actor_ref=security_reference("user", user_id),
            target_ref=security_reference("yazio_connection", connection_id),
            reason=(
                "credential_decryption_error" if credential_error
                else yazio_failure_reason(exc) if provider_failure
                else "unexpected_error"
            ),
            details={"mode": "range" if historical_pending else mode, **_provider_context_details(exc)},
        )
        if not raise_errors and provider_failure:
            return None
        raise
    summary = result.summary
    log_security_event(
        "import.completed",
        actor_ref=security_reference("user", user_id),
        target_ref=security_reference("import_batch", summary.batch_id),
        details={
            "source_type": "yazio_export_v1",
            "received": summary.received,
            "inserted": summary.inserted,
            "updated": summary.updated,
            "skipped": summary.skipped,
            "failed": summary.failed,
        },
    )
    log_security_event(
        "integration.yazio.sync_completed",
        actor_ref=security_reference("user", user_id),
        target_ref=security_reference("yazio_connection", connection_id),
        details={
            "mode": "range" if historical_pending else mode,
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
    now: datetime | None = None,
    after_connection: Callable[[], None] | None = None,
    between_connections: Callable[[], None] | None = None,
) -> tuple[int, int]:
    if not settings.yazio_enabled or not settings.yazio_scheduler_enabled:
        return 0, 0
    connection_ids = due_yazio_connection_ids(now)
    succeeded = 0
    pace = between_connections or _sleep_between_yazio_connections
    for index, connection_id in enumerate(connection_ids):
        if index:
            pace()
        try:
            if run_scheduled_yazio_sync(connection_id, now=now) is not None:
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
    expected_history: _YazioHistoryAttempt | None = None,
) -> None:
    with SessionLocal() as db:
        try:
            connection = db.scalar(
                select(YazioConnection).where(
                    YazioConnection.id == connection_id,
                ).with_for_update()
            )
            if connection is None:
                return
            if historical and (
                expected_history is None
                or not _history_attempt_matches(connection, expected_history)
            ):
                return
            retry_minutes = min(effective_sync_interval_minutes(connection), 60)
            if isinstance(error, CredentialEncryptionError) or isinstance(
                error.__cause__, CredentialEncryptionError,
            ):
                safe_error = "Gespeicherte YAZIO-Zugangsdaten konnten nicht entschlüsselt werden."
            elif isinstance(error, YazioSyncError):
                safe_error = str(error)[:500]
            else:
                safe_error = "Unerwarteter Fehler bei der Synchronisierung."
            connection.last_attempt_at = attempted_at
            if historical:
                connection.historical_sync_state = "failed"
                connection.historical_sync_last_error = safe_error
            connection.last_error = safe_error
            if isinstance(error, YazioAuthenticationError):
                connection.sync_enabled = False
                connection.next_sync_at = None
            else:
                connection.next_sync_at = _next_sync_at(attempted_at, retry_minutes)
            db.commit()
        except Exception:
            db.rollback()
            raise
