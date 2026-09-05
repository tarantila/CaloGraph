import hashlib
import zipfile
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from xml.etree.ElementTree import ParseError

import zstandard
from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
from sqlalchemy import delete, select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.importers.apple_xml import ReadableByteStream, iter_apple_health_xml
from app.importers.common import CanonicalSample, local_date_for
from app.importers.errors import ImportLimitError
from app.importers.json_adapter import AdapterResult
from app.models import HealthSample, ImportBatch, ImportError, RawImportPayload, User
from app.schemas import ImportSummary
from app.security_events import log_security_event, security_reference
from app.services.user_operation_lock import shared_user_operation


@dataclass(slots=True)
class ImportCounters:
    received: int = 0
    inserted: int = 0
    updated: int = 0
    duplicate_skipped: int = 0
    unknown_count: int = 0
    failed: int = 0
    accepted: int = 0


def _summary(batch: ImportBatch) -> ImportSummary:
    return ImportSummary(
        batch_id=batch.id,
        status=batch.status,
        received=batch.received,
        inserted=batch.inserted,
        updated=batch.updated,
        skipped=batch.skipped,
        failed=batch.failed,
        unknown_types=batch.unknown_types,
    )


def _start_batch(
    db: Session,
    user: User,
    source_type: str,
    client_identifier: str | None,
    payload_hash: str | None = None,
    *,
    connector_variant: str | None = None,
    commit: bool = True,
    log_started: bool = True,
) -> ImportBatch:
    batch = ImportBatch(
        user_id=user.id,
        source_type=source_type,
        client_identifier=client_identifier,
        connector_variant=connector_variant,
        status="processing",
        payload_hash=payload_hash,
    )
    db.add(batch)
    if commit:
        db.commit()
        db.refresh(batch)
    else:
        db.flush()
    if log_started:
        log_security_event(
            "import.started",
            actor_ref=security_reference("user", user.id),
            target_ref=security_reference("import_batch", batch.id),
            details={"source_type": source_type},
        )
    return batch


def _batch_event_details(batch: ImportBatch) -> dict[str, object]:
    return {
        "source_type": batch.source_type,
        "received": batch.received,
        "inserted": batch.inserted,
        "updated": batch.updated,
        "skipped": batch.skipped,
        "failed": batch.failed,
    }


def _sample_values(
    user: User,
    batch: ImportBatch,
    sample: CanonicalSample,
    fingerprint: str,
) -> dict[str, object]:
    return {
        "user_id": user.id,
        "import_batch_id": batch.id,
        "external_sample_id": sample.external_sample_id,
        "fingerprint": fingerprint,
        "source_type": sample.source_type,
        "source_name": sample.source_name,
        "source_identifier": sample.source_identifier,
        "metric_type": sample.metric_type,
        "value": sample.value,
        "unit": sample.unit,
        "original_value": sample.original_value,
        "original_unit": sample.original_unit,
        "start_at": sample.start_at,
        "end_at": sample.end_at,
        "local_date": local_date_for(sample.start_at, sample.timezone),
        "timezone": sample.timezone,
    }


def _update_sample(
    existing: HealthSample,
    batch: ImportBatch,
    sample: CanonicalSample,
    fingerprint: str,
) -> None:
    existing.import_batch_id = batch.id
    existing.external_sample_id = sample.external_sample_id
    existing.fingerprint = fingerprint
    existing.metric_type = sample.metric_type
    existing.value = sample.value
    existing.unit = sample.unit
    existing.original_value = sample.original_value
    existing.original_unit = sample.original_unit
    existing.start_at = sample.start_at
    existing.end_at = sample.end_at
    existing.local_date = local_date_for(sample.start_at, sample.timezone)
    existing.timezone = sample.timezone
    existing.source_name = sample.source_name
    existing.source_identifier = sample.source_identifier


def _persist_sample_batch(
    db: Session,
    user: User,
    batch: ImportBatch,
    samples: list[CanonicalSample],
) -> tuple[int, int, int]:
    prepared: list[tuple[CanonicalSample, str]] = []
    local_fingerprints: set[str] = set()
    local_external_ids: set[tuple[str, str, str]] = set()
    skipped = 0

    for sample in samples:
        fingerprint = sample.fingerprint(user.id)
        external_key = (
            sample.source_type,
            sample.source_identifier,
            sample.external_sample_id,
        ) if sample.external_sample_id else None
        if fingerprint in local_fingerprints or (
            external_key is not None and external_key in local_external_ids
        ):
            skipped += 1
            continue
        local_fingerprints.add(fingerprint)
        if external_key is not None:
            local_external_ids.add(external_key)
        prepared.append((sample, fingerprint))

    if not prepared:
        return 0, 0, skipped

    fingerprints = [fingerprint for _, fingerprint in prepared]
    existing_by_fingerprint = {
        item.fingerprint: item
        for item in db.scalars(
            select(HealthSample).where(
                HealthSample.user_id == user.id,
                HealthSample.fingerprint.in_(fingerprints),
            )
        )
    }
    external_keys = [
        (sample.source_type, sample.source_identifier, sample.external_sample_id)
        for sample, _ in prepared
        if sample.external_sample_id is not None
    ]
    existing_by_external: dict[tuple[str, str, str], HealthSample] = {}
    if external_keys:
        existing_by_external = {
            (
                item.source_type,
                item.source_identifier,
                item.external_sample_id or "",
            ): item
            for item in db.scalars(
                select(HealthSample).where(
                    HealthSample.user_id == user.id,
                    tuple_(
                        HealthSample.source_type,
                        HealthSample.source_identifier,
                        HealthSample.external_sample_id,
                    ).in_(external_keys),
                )
            )
        }

    inserted = updated = 0
    for sample, fingerprint in prepared:
        external_key = (
            sample.source_type,
            sample.source_identifier,
            sample.external_sample_id,
        ) if sample.external_sample_id else None
        existing = (
            existing_by_external.get(external_key)
            if external_key is not None
            else None
        )
        if existing is None:
            existing = existing_by_fingerprint.get(fingerprint)
        if existing is not None:
            if existing.fingerprint == fingerprint:
                skipped += 1
                continue
            _update_sample(existing, batch, sample, fingerprint)
            updated += 1
            continue
        db.add(HealthSample(**_sample_values(user, batch, sample, fingerprint)))
        inserted += 1

    db.flush()
    return inserted, updated, skipped


def _add_import_errors(
    db: Session,
    batch: ImportBatch,
    errors: list[tuple[int | None, str | None, str, str]],
) -> None:
    for item_index, metric, code, detail in errors:
        db.add(
            ImportError(
                batch_id=batch.id,
                item_index=item_index,
                metric_type=metric[:128] if metric else None,
                error_code=code[:64],
                safe_detail=detail[:500],
            )
        )


def _checkpoint(
    db: Session,
    batch: ImportBatch,
    counters: ImportCounters,
    unknown_types: set[str],
    errors: list[tuple[int | None, str | None, str, str]],
    *,
    commit: bool = True,
) -> None:
    _add_import_errors(db, batch, errors)
    errors.clear()
    batch.received = counters.received
    batch.inserted = counters.inserted
    batch.updated = counters.updated
    batch.skipped = counters.duplicate_skipped + counters.unknown_count
    batch.failed = counters.failed
    batch.unknown_types = sorted(unknown_types)
    if commit:
        db.commit()


def _partial_failure_detail(exc: Exception) -> str:
    if isinstance(exc, ImportLimitError):
        return str(exc)
    if isinstance(exc, DefusedXmlException):
        return "XML enthält nicht erlaubte DTD- oder Entity-Inhalte"
    if isinstance(exc, (ParseError, zipfile.BadZipFile)):
        return "XML- oder ZIP-Datei ist beschädigt oder unvollständig"
    if isinstance(exc, OSError):
        return "Importdatei konnte nicht vollständig gelesen werden"
    return "Import wurde während der Verarbeitung abgebrochen"


def _partial_failure_reason(exc: Exception) -> str:
    if isinstance(exc, ImportLimitError):
        return "record_limit"
    if isinstance(exc, DefusedXmlException):
        return "unsafe_xml"
    if isinstance(exc, ParseError):
        return "invalid_xml"
    if isinstance(exc, zipfile.BadZipFile):
        return "invalid_zip"
    if isinstance(exc, OSError):
        return "io_error"
    if isinstance(exc, SQLAlchemyError):
        return "database_error"
    return "unexpected_error"


def _finish_partial(
    db: Session,
    batch_id: object,
    counters: ImportCounters,
    unknown_types: set[str],
    errors: list[tuple[int | None, str | None, str, str]],
    exc: Exception,
) -> ImportSummary:
    db.rollback()
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise RuntimeError("Import batch disappeared while recording a partial failure") from exc
    detail = _partial_failure_detail(exc)
    if counters.failed < settings.max_import_errors:
        errors.append((counters.received or None, None, "import_aborted", detail))
    counters.failed += 1
    _checkpoint(db, batch, counters, unknown_types, errors)
    batch.status = "partial_failed"
    batch.error_message = detail
    batch.finished_at = datetime.now(UTC)
    db.commit()
    log_security_event(
        "import.partial_failed",
        actor_ref=security_reference("user", batch.user_id),
        target_ref=security_reference("import_batch", batch.id),
        reason=_partial_failure_reason(exc),
        details=_batch_event_details(batch),
    )
    return _summary(batch)

def _discard_failed_atomic_import(db: Session) -> None:
    db.rollback()


def persist_apple_health_stream(
    db: Session,
    user: User,
    stream: ReadableByteStream,
    content_type: str,
    client_identifier: str | None,
    *,
    atomic: bool = False,
) -> ImportSummary:
    with shared_user_operation(db, user.id) as active_user:
        return _persist_apple_health_stream_locked(
            db,
            active_user,
            stream,
            content_type,
            client_identifier,
            atomic=atomic,
        )


def _persist_apple_health_stream_locked(
    db: Session,
    user: User,
    stream: ReadableByteStream,
    content_type: str,
    client_identifier: str | None,
    *,
    atomic: bool,
) -> ImportSummary:
    del content_type
    batch = _start_batch(
        db,
        user,
        "apple_health_xml",
        client_identifier,
        commit=not atomic,
        log_started=not atomic,
    )
    counters = ImportCounters()
    samples: list[CanonicalSample] = []
    pending_errors: list[tuple[int | None, str | None, str, str]] = []
    unknown_types: set[str] = set()

    try:
        for record in iter_apple_health_xml(stream, user.timezone):
            counters.received += 1
            if counters.received > settings.max_import_records:
                raise ImportLimitError("Import enthält zu viele XML-Datensätze")
            if record.sample is not None:
                counters.accepted += 1
                if counters.accepted > settings.max_import_samples:
                    raise ImportLimitError(
                        "Import enthält zu viele unterstützte Ernährungswerte"
                    )
                samples.append(record.sample)
            elif record.error is not None:
                counters.failed += 1
                if (
                    counters.failed <= settings.max_import_errors
                    and len(pending_errors) < settings.max_import_errors
                ):
                    pending_errors.append(record.error)
            else:
                counters.unknown_count += 1
                if (
                    record.unknown_type
                    and len(unknown_types) < settings.max_import_unknown_types
                ):
                    unknown_types.add(record.unknown_type[:128])

            if len(samples) >= settings.import_batch_size:
                inserted, updated, skipped = _persist_sample_batch(
                    db, user, batch, samples
                )
                counters.inserted += inserted
                counters.updated += updated
                counters.duplicate_skipped += skipped
                samples.clear()
                if not atomic:
                    _checkpoint(db, batch, counters, unknown_types, pending_errors)

        if samples:
            inserted, updated, skipped = _persist_sample_batch(
                db, user, batch, samples
            )
            counters.inserted += inserted
            counters.updated += updated
            counters.duplicate_skipped += skipped
        _checkpoint(db, batch, counters, unknown_types, pending_errors, commit=not atomic)
    except (DefusedXmlException, OSError, ParseError, zipfile.BadZipFile, zlib.error) as exc:
        if atomic:
            _discard_failed_atomic_import(db)
            raise
        return _finish_partial(
            db,
            batch.id,
            counters,
            unknown_types,
            pending_errors,
            exc,
        )
    except ImportLimitError as exc:
        if atomic:
            _discard_failed_atomic_import(db)
            raise
        return _finish_partial(
            db,
            batch.id,
            counters,
            unknown_types,
            pending_errors,
            exc,
        )
    except SQLAlchemyError as exc:
        if atomic:
            _discard_failed_atomic_import(db)
            raise
        _finish_partial(
            db,
            batch.id,
            counters,
            unknown_types,
            pending_errors,
            exc,
        )
        raise

    if atomic:
        log_security_event(
            "import.started",
            actor_ref=security_reference("user", batch.user_id),
            target_ref=security_reference("import_batch", batch.id),
            details={"source_type": batch.source_type},
        )

    batch.status = "completed_with_errors" if counters.failed else "completed"
    batch.finished_at = datetime.now(UTC)
    db.commit()
    log_security_event(
        "import.completed",
        actor_ref=security_reference("user", batch.user_id),
        target_ref=security_reference("import_batch", batch.id),
        details=_batch_event_details(batch),
    )
    return _summary(batch)


def persist_import(
    db: Session,
    user: User,
    result: AdapterResult,
    raw_payload: bytes | None,
    content_type: str,
    client_identifier: str | None,
    *,
    connector_variant: str | None = None,
) -> ImportSummary:
    with shared_user_operation(db, user.id) as active_user:
        return _persist_import_locked(
            db,
            active_user,
            result,
            raw_payload,
            content_type,
            client_identifier,
            connector_variant=connector_variant,
        )


def _persist_import_locked(
    db: Session,
    user: User,
    result: AdapterResult,
    raw_payload: bytes | None,
    content_type: str,
    client_identifier: str | None,
    *,
    connector_variant: str | None = None,
) -> ImportSummary:
    payload_hash = hashlib.sha256(raw_payload).hexdigest() if raw_payload is not None else None
    batch = _start_batch(
        db,
        user,
        result.source_type,
        client_identifier,
        payload_hash,
        connector_variant=connector_variant,
    )
    counters = ImportCounters(
        received=result.received,
        unknown_count=result.unknown_count,
        failed=result.failed_count,
        accepted=len(result.samples),
    )
    pending_errors = list(result.errors[: settings.max_import_errors])
    unknown_types = set(list(sorted(result.unknown_types))[: settings.max_import_unknown_types])

    try:
        for offset in range(0, len(result.samples), settings.import_batch_size):
            inserted, updated, skipped = _persist_sample_batch(
                db,
                user,
                batch,
                result.samples[offset : offset + settings.import_batch_size],
            )
            counters.inserted += inserted
            counters.updated += updated
            counters.duplicate_skipped += skipped
            _checkpoint(db, batch, counters, unknown_types, pending_errors)

        if not result.samples:
            _checkpoint(db, batch, counters, unknown_types, pending_errors)

        retention_days = user.raw_payload_retention_days
        if raw_payload is not None and retention_days > 0:
            db.add(
                RawImportPayload(
                    batch_id=batch.id,
                    content_type=content_type,
                    compressed_payload=zstandard.ZstdCompressor(level=6).compress(raw_payload),
                    expires_at=datetime.now(UTC) + timedelta(days=retention_days),
                )
            )
        batch.status = "completed_with_errors" if counters.failed else "completed"
        batch.finished_at = datetime.now(UTC)
        db.commit()
    except SQLAlchemyError as exc:
        _finish_partial(
            db,
            batch.id,
            counters,
            unknown_types,
            pending_errors,
            exc,
        )
        raise
    log_security_event(
        "import.completed",
        actor_ref=security_reference("user", batch.user_id),
        target_ref=security_reference("import_batch", batch.id),
        details=_batch_event_details(batch),
    )
    return _summary(batch)


def purge_expired_raw_payloads(db: Session) -> int:
    result = db.execute(
        delete(RawImportPayload).where(RawImportPayload.expires_at <= datetime.now(UTC))
    )
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)
