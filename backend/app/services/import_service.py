import hashlib
from datetime import UTC, datetime, timedelta

import zstandard
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.importers.common import local_date_for
from app.importers.json_adapter import AdapterResult
from app.models import HealthSample, ImportBatch, ImportError, RawImportPayload, User
from app.schemas import ImportSummary


def persist_import(
    db: Session,
    user: User,
    result: AdapterResult,
    raw_payload: bytes | None,
    content_type: str,
    client_identifier: str | None,
) -> ImportSummary:
    payload_hash = hashlib.sha256(raw_payload).hexdigest() if raw_payload is not None else None
    batch = ImportBatch(
        user_id=user.id,
        source_type=result.source_type,
        client_identifier=client_identifier,
        status="processing",
        received=result.received,
        failed=len(result.errors),
        unknown_types=sorted(result.unknown_types),
        payload_hash=payload_hash,
    )
    db.add(batch)
    db.flush()

    for item_index, metric, code, detail in result.errors[:1000]:
        db.add(
            ImportError(
                batch_id=batch.id,
                item_index=item_index,
                metric_type=metric,
                error_code=code,
                safe_detail=detail[:500],
            )
        )

    inserted = updated = skipped = 0
    # SessionLocal deliberately disables autoflush. Consequently, a database
    # lookup cannot see samples that were added earlier in this same import
    # until the final commit. Keep an import-local index so duplicate records
    # in one Apple Health export are skipped before they can violate the
    # database uniqueness constraint.
    batch_fingerprints: set[str] = set()
    for sample in result.samples:
        fingerprint = sample.fingerprint(user.id)
        if fingerprint in batch_fingerprints:
            skipped += 1
            continue
        batch_fingerprints.add(fingerprint)
        existing = None
        if sample.external_sample_id:
            existing = db.scalar(
                select(HealthSample).where(
                    HealthSample.user_id == user.id,
                    HealthSample.source_type == sample.source_type,
                    HealthSample.source_identifier == sample.source_identifier,
                    HealthSample.external_sample_id == sample.external_sample_id,
                )
            )
        if existing is None:
            existing = db.scalar(
                select(HealthSample).where(
                    HealthSample.user_id == user.id, HealthSample.fingerprint == fingerprint
                )
            )
        if existing:
            changed = existing.fingerprint != fingerprint
            if not changed:
                skipped += 1
                continue
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
            existing.import_batch_id = batch.id
            updated += 1
            continue
        db.add(
            HealthSample(
                user_id=user.id,
                import_batch_id=batch.id,
                external_sample_id=sample.external_sample_id,
                fingerprint=fingerprint,
                source_type=sample.source_type,
                source_name=sample.source_name,
                source_identifier=sample.source_identifier,
                metric_type=sample.metric_type,
                value=sample.value,
                unit=sample.unit,
                original_value=sample.original_value,
                original_unit=sample.original_unit,
                start_at=sample.start_at,
                end_at=sample.end_at,
                local_date=local_date_for(sample.start_at, sample.timezone),
                timezone=sample.timezone,
            )
        )
        inserted += 1

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
    batch.inserted = inserted
    batch.updated = updated
    batch.skipped = skipped + result.unknown_count
    batch.status = "completed_with_errors" if result.errors else "completed"
    batch.finished_at = datetime.now(UTC)
    db.commit()
    return ImportSummary(
        batch_id=batch.id,
        status=batch.status,
        received=batch.received,
        inserted=inserted,
        updated=updated,
        skipped=batch.skipped,
        failed=batch.failed,
        unknown_types=batch.unknown_types,
    )


def purge_expired_raw_payloads(db: Session) -> int:
    result = db.execute(
        delete(RawImportPayload).where(RawImportPayload.expires_at <= datetime.now(UTC))
    )
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)
