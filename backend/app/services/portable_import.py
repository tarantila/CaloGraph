from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import PurePosixPath
from typing import BinaryIO
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.activity import ACTIVITY_MODES, ACTIVITY_SOURCE_TYPES
from app.config import settings
from app.importers.common import METRIC_MAP, CanonicalSample
from app.models import (
    HealthSample,
    ImportBatch,
    NutritionTarget,
    TrackingOverride,
    TrackingQualitySettings,
    User,
    UserAchievement,
    UserProfile,
)
from app.services.achievements import unlock_achievement_keys
from app.services.data_export import (
    EXPORT_FORMAT,
    SUPPORTED_EXPORT_FORMAT_VERSIONS,
    ExportAchievement,
    ExportHealthSample,
    ExportManifest,
    ExportProfile,
    ExportProfileV1,
    ExportSettings,
    ExportTarget,
    ExportTrackingOverride,
)

EXPECTED_FILES = frozenset(
    {
        "manifest.json",
        "profile.json",
        "settings.json",
        "targets.json",
        "tracking_overrides.json",
        "health_samples.jsonl",
        "import_batches.jsonl",
        "yazio.json",
        "achievements.json",
    }
)
_CANONICAL_METRIC_UNITS = frozenset(METRIC_MAP.values())
_SUPPORTED_PORTABLE_SOURCES = ACTIVITY_SOURCE_TYPES | {"calograph_sync_v1", "synthetic_demo"}
_MAX_COMPRESSION_RATIO = 200
_MAX_HEALTH_SAMPLES_BYTES = 128 * 1024 * 1024


class PortableImportError(ValueError):
    pass


def _utc_datetime(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

def _archive_payload_hash(file: BinaryIO) -> str:
    digest = hashlib.sha256()
    file.seek(0)
    while chunk := file.read(1024 * 1024):
        digest.update(chunk)
    file.seek(0)
    return digest.hexdigest()


def _open_archive(file: BinaryIO) -> zipfile.ZipFile:
    try:
        if not zipfile.is_zipfile(file):
            raise PortableImportError("Ungültige CaloGraph-Sicherung")
        file.seek(0)
        archive = zipfile.ZipFile(file)
    except (OSError, zipfile.BadZipFile) as exc:
        raise PortableImportError("Ungültige CaloGraph-Sicherung") from exc
    entries = archive.infolist()
    if len(entries) != len(EXPECTED_FILES):
        raise PortableImportError("Die Sicherung enthält nicht die erwartete Dateistruktur")
    if sum(entry.file_size for entry in entries) > settings.max_zip_uncompressed_bytes:
        raise PortableImportError("Die Sicherung ist zu groß")
    names: set[str] = set()
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if path.is_absolute() or ".." in path.parts or entry.filename in names:
            archive.close()
            raise PortableImportError("Unsicherer Pfad in Sicherung")
        names.add(entry.filename)
        if entry.compress_size and entry.file_size / entry.compress_size > _MAX_COMPRESSION_RATIO:
            archive.close()
            raise PortableImportError("Die Sicherung überschreitet sichere Entpackgrenzen")
    if names != EXPECTED_FILES:
        archive.close()
        raise PortableImportError("Die Sicherung enthält unbekannte oder fehlende Dateien")
    return archive


def _read_json(archive: zipfile.ZipFile, name: str) -> object:
    try:
        if archive.getinfo(name).file_size > settings.max_json_payload_bytes:
            raise PortableImportError(f"Datei in Sicherung ist zu groß: {name}")
        with archive.open(name) as stream:
            return json.load(stream, parse_float=Decimal)
    except PortableImportError:
        raise
    except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PortableImportError(f"Ungültige Datei in Sicherung: {name}") from exc


def _read_jsonl(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, object]]:
    try:
        with archive.open(name) as stream:
            max_line_bytes = settings.max_json_payload_bytes + 1
            while raw_line := stream.readline(max_line_bytes):
                if len(raw_line) >= max_line_bytes:
                    raise PortableImportError(f"JSONL-Zeile in Sicherung ist zu groß: {name}")
                if not raw_line.strip():
                    continue
                value = json.loads(raw_line, parse_float=Decimal)
                if not isinstance(value, dict):
                    raise PortableImportError(f"Ungültige JSONL-Datei: {name}")
                yield value
    except PortableImportError:
        raise
    except (OSError, KeyError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PortableImportError(f"Ungültige Datei in Sicherung: {name}") from exc

def _validated_records(
    archive: zipfile.ZipFile,
) -> tuple[
    ExportManifest,
    ExportProfile,
    ExportSettings,
    list[ExportTarget],
    list[ExportTrackingOverride],
    list[ExportAchievement],
    list[ExportHealthSample],
]:
    try:
        manifest = ExportManifest.model_validate(_read_json(archive, "manifest.json"))
        if (
            manifest.format != EXPORT_FORMAT
            or manifest.format_version not in SUPPORTED_EXPORT_FORMAT_VERSIONS
        ):
            raise PortableImportError("Unbekanntes CaloGraph-Exportformat oder Version")
        profile_payload = _read_json(archive, "profile.json")
        if manifest.format_version == 1:
            profile_v1 = ExportProfileV1.model_validate(profile_payload)
            profile = ExportProfile(**profile_v1.model_dump(), display_name=None)
        else:
            profile = ExportProfile.model_validate(profile_payload)
        manifest.generated_at = _utc_datetime(manifest.generated_at)
        profile.created_at = _utc_datetime(profile.created_at)
        if (
            profile.language not in {"de", "en"}
            or profile.week_starts_on not in range(7)
            or profile.preferred_weight_unit not in {"kg", "lb"}
            or not 0 <= profile.raw_payload_retention_days <= 3650
        ):
            raise PortableImportError("Profilwerte in Sicherung sind ungültig")
        try:
            profile_timezone = ZoneInfo(profile.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise PortableImportError("Zeitzone in Sicherung ist ungültig") from exc
        if (
            profile.birth_date is not None
            and profile.birth_date > datetime.now(profile_timezone).date()
        ):
            raise PortableImportError("Geburtsdatum in Sicherung ist ungültig")
        portable_settings = ExportSettings.model_validate(_read_json(archive, "settings.json"))
        targets_raw = _read_json(archive, "targets.json")
        overrides_raw = _read_json(archive, "tracking_overrides.json")
        achievements_raw = _read_json(archive, "achievements.json")
        if (
            not isinstance(targets_raw, list)
            or not isinstance(overrides_raw, list)
            or not isinstance(achievements_raw, list)
        ):
            raise PortableImportError("JSON-Arrays in Sicherung sind ungültig")
        if len(targets_raw) + len(overrides_raw) + len(achievements_raw) > settings.max_import_records:
            raise PortableImportError("Die Sicherung enthält zu viele Datensätze")
        targets: list[ExportTarget] = []
        for value in targets_raw:
            target = ExportTarget.model_validate(value)
            target.created_at = _utc_datetime(target.created_at)
            if (
                target.activity_mode not in ACTIVITY_MODES
                or (
                    target.activity_mode == "off"
                    and target.activity_source_type is not None
                )
                or (
                    target.activity_mode == "full"
                    and (
                        target.activity_source_type is None
                        or target.activity_source_type not in ACTIVITY_SOURCE_TYPES
                    )
                )
            ):
                raise PortableImportError("Aktivitätskonfiguration in Sicherung ist ungültig")
            targets.append(target)
        overrides = [ExportTrackingOverride.model_validate(value) for value in overrides_raw]
        for override in overrides:
            override.created_at = _utc_datetime(override.created_at)
        achievements = [ExportAchievement.model_validate(value) for value in achievements_raw]
        for achievement in achievements:
            achievement.unlocked_at = _utc_datetime(achievement.unlocked_at)
        if archive.getinfo("health_samples.jsonl").file_size > _MAX_HEALTH_SAMPLES_BYTES:
            raise PortableImportError("Gesundheitssamples in Sicherung sind zu groß")
        samples: list[ExportHealthSample] = []
        for value in _read_jsonl(archive, "health_samples.jsonl"):
            if len(samples) >= settings.max_import_samples:
                raise PortableImportError("Die Sicherung enthält zu viele Samples")
            sample = ExportHealthSample.model_validate(value)
            if (
                sample.source_type not in _SUPPORTED_PORTABLE_SOURCES
                or (sample.metric_type, sample.unit) not in _CANONICAL_METRIC_UNITS
            ):
                raise PortableImportError("Sample enthält nicht unterstützte kanonische Felder")
            sample.start_at = _utc_datetime(sample.start_at)
            sample.end_at = _utc_datetime(sample.end_at)
            sample.created_at = _utc_datetime(sample.created_at)
            sample.updated_at = _utc_datetime(sample.updated_at)
            CanonicalSample(
                metric_type=sample.metric_type,
                value=sample.value,
                unit=sample.unit,
                original_value=sample.original_value,
                original_unit=sample.original_unit,
                start_at=sample.start_at,
                end_at=sample.end_at,
                timezone=sample.timezone,
                source_type=sample.source_type,
                source_name=sample.source_name,
                source_identifier=sample.source_identifier,
                external_sample_id=sample.external_sample_id,
            )
            if sample.end_at < sample.start_at:
                raise PortableImportError("Sample-Zeitbereich in Sicherung ist ungültig")
            try:
                sample_timezone = ZoneInfo(sample.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise PortableImportError("Sample-Zeitzone in Sicherung ist ungültig") from exc
            if sample.local_date != sample.start_at.astimezone(sample_timezone).date():
                raise PortableImportError("Sample-Datum und Zeitzone in Sicherung passen nicht zusammen")
            samples.append(sample)
    except ValidationError as exc:
        raise PortableImportError("Sicherung enthält ungültige Felder") from exc
    return (
        manifest,
        profile,
        portable_settings,
        targets,
        overrides,
        achievements,
        samples,
    )


def validate_portable_import(file: BinaryIO) -> dict[str, int | str]:
    archive = _open_archive(file)
    try:
        _, profile, _, targets, overrides, achievements, samples = _validated_records(archive)
        return {
            "status": "valid",
            "username": profile.username,
            "targets": len(targets),
            "tracking_overrides": len(overrides),
            "achievements": len(achievements),
            "health_samples": len(samples),
        }
    finally:
        archive.close()


def _sample_fingerprint(user_id: object, sample: ExportHealthSample) -> str:
    payload = {
        "user": str(user_id),
        "external_sample_id": sample.external_sample_id,
        "source_type": sample.source_type,
        "source_identifier": sample.source_identifier,
        "metric_type": sample.metric_type,
        "value": str(sample.value),
        "unit": sample.unit,
        "start_at": sample.start_at.astimezone(UTC).isoformat(),
        "end_at": sample.end_at.astimezone(UTC).isoformat(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def apply_portable_import(file: BinaryIO, user: User, db: Session) -> dict[str, int | str]:
    payload_hash = _archive_payload_hash(file)
    archive = _open_archive(file)
    try:
        manifest, profile, portable_settings, targets, overrides, achievements, samples = (
            _validated_records(archive)
        )
        previous_success = db.scalar(
            select(ImportBatch.id)
            .where(
                ImportBatch.user_id == user.id,
                ImportBatch.source_type == "calograph-data-export",
                ImportBatch.payload_hash == payload_hash,
                ImportBatch.status.in_(("completed", "completed_with_errors")),
            )
            .limit(1)
        ) is not None
        import_batch = ImportBatch(
            user_id=user.id,
            source_type="calograph-data-export",
            status="processing",
            payload_hash=payload_hash,
        )
        db.add(import_batch)
        db.flush()
        inserted_samples = 0
        skipped_samples = 0
        for sample in samples:
            fingerprint = _sample_fingerprint(user.id, sample)
            if sample.external_sample_id is None:
                existing = db.scalar(
                    select(HealthSample).where(
                        HealthSample.user_id == user.id,
                        HealthSample.fingerprint == fingerprint,
                    )
                )
            else:
                existing = db.scalar(
                    select(HealthSample).where(
                        HealthSample.user_id == user.id,
                        HealthSample.source_type == sample.source_type,
                        HealthSample.source_identifier == sample.source_identifier,
                        HealthSample.external_sample_id == sample.external_sample_id,
                    )
                )
            if existing is not None:
                existing.fingerprint = fingerprint
                existing.import_batch_id = import_batch.id
                existing.source_name = sample.source_name
                existing.metric_type = sample.metric_type
                existing.value = sample.value
                existing.unit = sample.unit
                existing.original_value = sample.original_value
                existing.original_unit = sample.original_unit
                existing.start_at = sample.start_at
                existing.end_at = sample.end_at
                existing.local_date = sample.local_date
                existing.timezone = sample.timezone
                existing.updated_at = sample.updated_at
                skipped_samples += 1
                continue
            db.add(
                HealthSample(
                    user_id=user.id,
                    import_batch_id=import_batch.id,
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
                    local_date=sample.local_date,
                    timezone=sample.timezone,
                    created_at=sample.created_at,
                    updated_at=sample.updated_at,
                )
            )
            inserted_samples += 1
        for target in targets:
            existing_target = db.scalar(
                select(NutritionTarget).where(
                    NutritionTarget.user_id == user.id,
                    NutritionTarget.valid_from == target.valid_from,
                )
            )
            values = {
                "valid_to": target.valid_to,
                "calories_kcal": target.calories_kcal,
                "maintenance_kcal": target.maintenance_kcal,
                "activity_mode": target.activity_mode,
                "activity_source_type": target.activity_source_type,
                "protein_g": target.protein_g,
                "carbs_g": target.carbs_g,
                "fat_g": target.fat_g,
                "fiber_g": target.fiber_g,
                "water_ml": target.water_ml,
            }
            if existing_target is None:
                db.add(
                    NutritionTarget(
                        user_id=user.id,
                        valid_from=target.valid_from,
                        created_at=target.created_at,
                        **values,
                    )
                )
            else:
                for key, value in values.items():
                    setattr(existing_target, key, value)
        all_targets = list(
            db.scalars(
                select(NutritionTarget)
                .where(NutritionTarget.user_id == user.id)
                .order_by(NutritionTarget.valid_from)
            )
        )
        for current_target, next_target in pairwise(all_targets):
            if current_target.valid_to is None or current_target.valid_to > next_target.valid_from:
                current_target.valid_to = next_target.valid_from
        for override in overrides:
            existing_override = db.scalar(
                select(TrackingOverride).where(
                    TrackingOverride.user_id == user.id,
                    TrackingOverride.local_date == override.local_date,
                )
            )
            if existing_override is None:
                db.add(
                    TrackingOverride(
                        user_id=user.id,
                        local_date=override.local_date,
                        status=override.status,
                        note=override.note,
                        created_at=override.created_at,
                    )
                )
            else:
                existing_override.status = override.status
                existing_override.note = override.note
        for achievement in achievements:
            existing_achievement = db.scalar(
                select(UserAchievement.id).where(
                    UserAchievement.user_id == user.id,
                    UserAchievement.achievement_key == achievement.achievement_key,
                )
            )
            if existing_achievement is None:
                db.add(
                    UserAchievement(
                        user_id=user.id,
                        achievement_key=achievement.achievement_key,
                        unlocked_at=achievement.unlocked_at,
                    )
                )
        if portable_settings.tracking_quality is not None:
            quality = portable_settings.tracking_quality

            current_quality = db.get(TrackingQualitySettings, user.id)
            quality_values = {
                "calories_full_ratio": quality.calories_full_ratio,
                "calories_partial_ratio": quality.calories_partial_ratio,
                "median_full_ratio": quality.median_full_ratio,
                "median_partial_ratio": quality.median_partial_ratio,
                "complete_score": quality.complete_score,
                "probably_complete_score": quality.probably_complete_score,
                "probably_incomplete_score": quality.probably_incomplete_score,
            }
            if current_quality is None:
                db.add(TrackingQualitySettings(user_id=user.id, **quality_values))
            else:
                for quality_key, quality_value in quality_values.items():
                    setattr(current_quality, quality_key, quality_value)
        if manifest.format_version == 2:
            personal_values = {
                "display_name": profile.display_name,
                "gender": profile.gender,
                "birth_date": profile.birth_date,
                "height_cm": profile.height_cm,
                "diet_type": profile.diet_type,
                "health_notes": profile.health_notes,
                "intolerances": profile.intolerances,
            }
            personal_profile = db.get(UserProfile, user.id)
            if personal_profile is None:
                if any(value is not None for value in personal_values.values()):
                    db.add(UserProfile(user_id=user.id, **personal_values))
            else:
                for field, value in personal_values.items():
                    setattr(personal_profile, field, value)
        user.language = profile.language
        user.timezone = profile.timezone
        user.week_starts_on = profile.week_starts_on
        user.preferred_weight_unit = profile.preferred_weight_unit
        user.raw_payload_retention_days = profile.raw_payload_retention_days
        import_batch.status = "completed"
        import_batch.received = len(samples)
        import_batch.inserted = inserted_samples
        import_batch.skipped = skipped_samples
        import_batch.finished_at = datetime.now(UTC)
        db.commit()
        unlock_achievement_keys(db, user.id, ("welcome_back",) + (("deja_vu",) if previous_success else ()))
        return {
            "status": "completed",
            "inserted": inserted_samples,
            "skipped": skipped_samples,
            "targets": len(targets),
            "tracking_overrides": len(overrides),
            "achievements": len(achievements),
        }
    except (DataError, IntegrityError, StatementError) as exc:
        db.rollback()
        raise PortableImportError("Sicherung enthält nicht speicherbare Werte") from exc
    except Exception:
        db.rollback()
        raise
    finally:
        archive.close()
