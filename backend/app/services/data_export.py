from __future__ import annotations

import csv
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from io import TextIOWrapper
from queue import Empty, Full, Queue
from threading import BoundedSemaphore, Event, Lock, Thread, current_thread
from typing import IO, Literal, cast
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    HealthSample,
    ImportBatch,
    NutritionTarget,
    TrackingOverride,
    TrackingQualitySettings,
    User,
    UserAchievement,
    UserProfile,
    YazioConnection,
)
from app.security_events import log_security_event, security_reference
from app.services.achievements import unlock_achievement_keys

EXPORT_FORMAT = "calograph-data-export"
EXPORT_FORMAT_VERSION = 2
SUPPORTED_EXPORT_FORMAT_VERSIONS = frozenset({1, 2})
EXPORT_STATUS_COOKIE = "calograph_export_status"
_EXPORT_CHUNK_BYTES = 64 * 1024
_EXPORT_QUEUE_CHUNKS = 16
_EXPORT_SLOTS = BoundedSemaphore(1)


def export_status_cookie(
    download_id: UUID,
    status: Literal["accepted", "busy", "unauthenticated"],
    *,
    secure: bool,
) -> str:
    secure_attribute = "; Secure" if secure else ""
    cookie_name = f"{EXPORT_STATUS_COOKIE}_{download_id.hex}"
    return (
        f"{cookie_name}={status}; Max-Age=30; Path=/; "
        f"SameSite=Lax{secure_attribute}"
    )


class ExportManifest(BaseModel):
    format: Literal["calograph-data-export"]
    format_version: Literal[1, 2]
    generated_at: datetime
    application: Literal["CaloGraph"]
    application_version: str
    files: list[str]


class ExportProfileV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    language: Literal["de", "en"]
    timezone: str
    week_starts_on: int
    preferred_weight_unit: Literal["kg", "lb"]
    raw_payload_retention_days: int
    created_at: datetime


class ExportProfile(ExportProfileV1):
    display_name: str | None = Field(default=None, max_length=120)
    gender: Literal["female", "male", "non_binary", "other", "prefer_not_to_say"] | None = None
    birth_date: date | None = None
    height_cm: Decimal | None = Field(
        default=None, gt=0, le=300, max_digits=5, decimal_places=2
    )
    diet_type: (
        Literal[
            "no_special_diet",
            "vegetarian",
            "vegan",
            "pescetarian",
            "other",
            "prefer_not_to_say",
        ]
        | None
    ) = None
    health_notes: str | None = Field(default=None, max_length=4000)
    intolerances: str | None = Field(default=None, max_length=2000)

    @field_validator("display_name", "health_notes", "intolerances", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class ExportTrackingQuality(BaseModel):
    calories_full_ratio: Decimal = Field(gt=0, le=2)
    calories_partial_ratio: Decimal = Field(gt=0, le=2)
    median_full_ratio: Decimal = Field(gt=0, le=2)
    median_partial_ratio: Decimal = Field(gt=0, le=2)
    complete_score: int = Field(ge=1, le=8)
    probably_complete_score: int = Field(ge=1, le=8)
    probably_incomplete_score: int = Field(ge=1, le=8)


class ExportSettings(BaseModel):
    tracking_quality: ExportTrackingQuality | None


class ExportTarget(BaseModel):
    valid_from: date
    valid_to: date | None
    calories_kcal: Decimal = Field(gt=0, max_digits=12, decimal_places=3)
    maintenance_kcal: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=3)
    activity_mode: Literal["off", "full"]
    activity_source_type: str | None
    protein_g: Decimal = Field(ge=0, max_digits=12, decimal_places=3)
    carbs_g: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    fat_g: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    fiber_g: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    water_ml: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=3)
    created_at: datetime


class ExportTrackingOverride(BaseModel):
    local_date: date
    status: Literal["complete", "probably_complete", "probably_incomplete", "incomplete", "no_data"]
    note: str | None = Field(default=None, max_length=500)
    created_at: datetime


class ExportHealthSample(BaseModel):
    external_sample_id: str | None
    source_type: str
    source_name: str | None
    source_identifier: str
    metric_type: str
    value: Decimal
    unit: str
    original_value: Decimal
    original_unit: str
    start_at: datetime
    end_at: datetime
    local_date: date
    timezone: str
    created_at: datetime
    updated_at: datetime


class ExportImportBatch(BaseModel):
    source_type: str
    client_identifier: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    received: int
    inserted: int
    updated: int
    skipped: int
    failed: int
    unknown_types: list[str]


class ExportYazioConnection(BaseModel):
    configured: bool
    source_identifier: str | None = None
    sync_enabled: bool | None = None
    sync_interval_minutes: int | None = None
    sync_days: int | None = None
    historical_sync_state: str | None = None
    historical_sync_start_date: date | None = None
    historical_sync_end_date: date | None = None
    historical_sync_cursor_date: date | None = None
    historical_sync_started_at: datetime | None = None
    historical_sync_completed_at: datetime | None = None
    historical_sync_last_error: str | None = None
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_micronutrient_sync_at: datetime | None = None
    next_sync_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ExportAchievement(BaseModel):
    achievement_key: str
    unlocked_at: datetime


class ExportBusy(Exception):
    pass


class ExportCancelled(Exception):
    pass


@dataclass(frozen=True)
class ExportFailure:
    cause: Exception


class StreamingZipWriter:
    def __init__(self, chunks: Queue[bytes | ExportFailure | None], cancelled: Event) -> None:
        self._chunks = chunks
        self._cancelled = cancelled
        self._position = 0

    def write(self, data: bytes) -> int:
        for start in range(0, len(data), _EXPORT_CHUNK_BYTES):
            chunk = data[start : start + _EXPORT_CHUNK_BYTES]
            while True:
                if self._cancelled.is_set():
                    raise ExportCancelled
                try:
                    self._chunks.put(chunk, timeout=0.1)
                    self._position += len(chunk)
                    break
                except Full:
                    continue
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        return None

    def seekable(self) -> Literal[False]:
        return False


class ExportStream(Iterator[bytes]):
    def __init__(
        self,
        user_id: UUID,
        *,
        request_id: str | None,
        client_ref: str | None,
        export_writer: Callable[[ZipFile, Session, User], None] | None = None,
        event_name: str = "data.exported",
        achievement_keys: tuple[str, ...] = (),
    ) -> None:
        self._user_id = user_id
        self._request_id = request_id
        self._client_ref = client_ref
        self._export_writer = export_writer
        self._event_name = event_name
        self._achievement_keys = achievement_keys
        self._chunks: Queue[bytes | ExportFailure | None] | None = None
        self._cancelled = Event()
        self._producer: Thread | None = None
        self._state_lock = Lock()
        self._closing = False
        self._close_done = Event()
        self._slot_released = False

    def __iter__(self) -> ExportStream:
        return self

    def __next__(self) -> bytes:
        try:
            self._start()
        except BaseException:
            self.close()
            raise
        chunks = self._chunks
        producer = self._producer
        assert chunks is not None
        assert producer is not None
        while True:
            try:
                item = chunks.get(timeout=0.1)
            except Empty:
                if not producer.is_alive():
                    self.close()
                    raise StopIteration from None
                continue
            if item is None:
                raise StopIteration
            if isinstance(item, ExportFailure):
                raise item.cause
            return item

    def _start(self) -> None:
        with self._state_lock:
            if self._closing:
                raise StopIteration
            if self._producer is not None:
                return
            self._chunks = Queue(maxsize=_EXPORT_QUEUE_CHUNKS)
            self._producer = Thread(
                target=self._run_producer,
                name="calograph-data-export",
                daemon=True,
            )
            self._producer.start()

    def _publish(self, value: ExportFailure | None) -> None:
        chunks = self._chunks
        assert chunks is not None
        while not self._cancelled.is_set():
            try:
                chunks.put(value, timeout=0.1)
                return
            except Full:
                continue

    def _produce(self) -> None:
        chunks = self._chunks
        assert chunks is not None
        with SessionLocal() as db:
            user = db.get(User, self._user_id)
            if user is None:
                raise RuntimeError("Export user no longer exists")
            writer = cast(IO[bytes], StreamingZipWriter(chunks, self._cancelled))
            export_writer = self._export_writer or _write_export
            with ZipFile(
                writer,
                "w",
                compression=ZIP_DEFLATED,
                compresslevel=6,
            ) as zip_file:
                export_writer(zip_file, db, user)
            if self._achievement_keys:
                unlock_achievement_keys(db, user.id, self._achievement_keys)
            log_security_event(
                self._event_name,
                actor_ref=security_reference("user", user.id),
                request_id=self._request_id,
                client_ref=self._client_ref,
            )

    def _run_producer(self) -> None:
        try:
            self._produce()
        except ExportCancelled:
            pass
        except Exception as exc:
            self._publish(ExportFailure(exc))
        finally:
            self._publish(None)

    def close(self) -> None:
        with self._state_lock:
            if self._closing:
                close_done = self._close_done
                owner = False
            else:
                self._closing = True
                self._cancelled.set()
                close_done = self._close_done
                owner = True
                producer = self._producer
        if not owner:
            close_done.wait()
            return
        try:
            if producer is not None and producer is not current_thread():
                producer.join()
        finally:
            with self._state_lock:
                if not self._slot_released:
                    self._slot_released = True
                    _EXPORT_SLOTS.release()
            close_done.set()


def _application_version() -> str:
    try:
        return version("calograph-backend")
    except PackageNotFoundError:
        return "unknown"


def _write_json(zip_file: ZipFile, filename: str, value: BaseModel) -> None:
    payload = value.model_dump(mode="json")
    zip_file.writestr(filename, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _write_json_array(
    zip_file: ZipFile,
    filename: str,
    records: Iterator[BaseModel],
) -> None:
    with TextIOWrapper(zip_file.open(filename, "w"), encoding="utf-8", newline="\n") as output:
        output.write("[")
        first = True
        for record in records:
            if not first:
                output.write(",")
            output.write(
                json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            )
            first = False
        output.write("]")

def _write_jsonl(zip_file: ZipFile, filename: str, records: Iterator[BaseModel]) -> None:
    with TextIOWrapper(zip_file.open(filename, "w"), encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(
                json.dumps(record.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
            )
            output.write("\n")


def _health_samples(db: Session, user_id: UUID) -> Iterator[ExportHealthSample]:
    statement = (
        select(HealthSample)
        .where(HealthSample.user_id == user_id)
        .order_by(HealthSample.start_at, HealthSample.id)
        .execution_options(stream_results=True)
    )
    for sample in db.scalars(statement).yield_per(500):
        yield ExportHealthSample(
            external_sample_id=sample.external_sample_id,
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


def _import_batches(db: Session, user_id: UUID) -> Iterator[ExportImportBatch]:
    statement = (
        select(ImportBatch)
        .where(ImportBatch.user_id == user_id)
        .order_by(ImportBatch.started_at, ImportBatch.id)
        .execution_options(stream_results=True)
    )
    for batch in db.scalars(statement).yield_per(500):
        yield ExportImportBatch(
            source_type=batch.source_type,
            client_identifier=batch.client_identifier,
            status=batch.status,
            started_at=batch.started_at,
            finished_at=batch.finished_at,
            received=batch.received,
            inserted=batch.inserted,
            updated=batch.updated,
            skipped=batch.skipped,
            failed=batch.failed,
            unknown_types=batch.unknown_types,
        )


def _targets(db: Session, user_id: UUID) -> Iterator[ExportTarget]:
    statement = (
        select(NutritionTarget)
        .where(NutritionTarget.user_id == user_id)
        .order_by(NutritionTarget.valid_from, NutritionTarget.id)
        .execution_options(stream_results=True)
    )
    for target in db.scalars(statement).yield_per(500):
        yield ExportTarget(
            valid_from=target.valid_from,
            valid_to=target.valid_to,
            calories_kcal=target.calories_kcal,
            maintenance_kcal=target.maintenance_kcal,
            activity_mode=target.activity_mode,
            activity_source_type=target.activity_source_type,
            protein_g=target.protein_g,
            carbs_g=target.carbs_g,
            fat_g=target.fat_g,
            fiber_g=target.fiber_g,
            water_ml=target.water_ml,
            created_at=target.created_at,
        )


def _tracking_overrides(db: Session, user_id: UUID) -> Iterator[ExportTrackingOverride]:
    statement = (
        select(TrackingOverride)
        .where(TrackingOverride.user_id == user_id)
        .order_by(TrackingOverride.local_date, TrackingOverride.id)
        .execution_options(stream_results=True)
    )
    for item in db.scalars(statement).yield_per(500):
        yield ExportTrackingOverride(
            local_date=item.local_date,
            status=item.status,
            note=item.note,
            created_at=item.created_at,
        )


def _achievements(db: Session, user_id: UUID) -> Iterator[ExportAchievement]:
    statement = (
        select(UserAchievement)
        .where(UserAchievement.user_id == user_id)
        .order_by(UserAchievement.achievement_key, UserAchievement.id)
        .execution_options(stream_results=True)
    )
    for item in db.scalars(statement).yield_per(500):
        yield ExportAchievement(achievement_key=item.achievement_key, unlocked_at=item.unlocked_at)


def _write_export(zip_file: ZipFile, db: Session, user: User) -> None:
    files = [
        "manifest.json",
        "profile.json",
        "settings.json",
        "targets.json",
        "tracking_overrides.json",
        "health_samples.jsonl",
        "import_batches.jsonl",
        "yazio.json",
        "achievements.json",
    ]
    _write_json(
        zip_file,
        "manifest.json",
        ExportManifest(
            format=EXPORT_FORMAT,
            format_version=EXPORT_FORMAT_VERSION,
            generated_at=datetime.now(UTC),
            application="CaloGraph",
            application_version=_application_version(),
            files=files,
        ),
    )
    personal_profile = db.get(UserProfile, user.id)
    _write_json(
        zip_file,
        "profile.json",
        ExportProfile(
            username=user.username,
            language=user.language,
            timezone=user.timezone,
            week_starts_on=user.week_starts_on,
            preferred_weight_unit=user.preferred_weight_unit,
            raw_payload_retention_days=user.raw_payload_retention_days,
            created_at=user.created_at,
            display_name=personal_profile.display_name if personal_profile else None,
            gender=personal_profile.gender if personal_profile else None,
            birth_date=personal_profile.birth_date if personal_profile else None,
            height_cm=personal_profile.height_cm if personal_profile else None,
            diet_type=personal_profile.diet_type if personal_profile else None,
            health_notes=personal_profile.health_notes if personal_profile else None,
            intolerances=personal_profile.intolerances if personal_profile else None,
        ),
    )
    quality = db.get(TrackingQualitySettings, user.id)
    _write_json(
        zip_file,
        "settings.json",
        ExportSettings(
            tracking_quality=None
            if quality is None
            else ExportTrackingQuality(
                calories_full_ratio=quality.calories_full_ratio,
                calories_partial_ratio=quality.calories_partial_ratio,
                median_full_ratio=quality.median_full_ratio,
                median_partial_ratio=quality.median_partial_ratio,
                complete_score=quality.complete_score,
                probably_complete_score=quality.probably_complete_score,
                probably_incomplete_score=quality.probably_incomplete_score,
            ),
        ),
    )
    _write_json_array(zip_file, "targets.json", _targets(db, user.id))
    _write_json_array(
        zip_file,
        "tracking_overrides.json",
        _tracking_overrides(db, user.id),
    )
    _write_jsonl(zip_file, "health_samples.jsonl", _health_samples(db, user.id))
    _write_jsonl(zip_file, "import_batches.jsonl", _import_batches(db, user.id))
    connection = db.scalar(select(YazioConnection).where(YazioConnection.user_id == user.id))
    _write_json(
        zip_file,
        "yazio.json",
        ExportYazioConnection(configured=connection is not None)
        if connection is None
        else ExportYazioConnection(
            configured=True,
            source_identifier=connection.source_identifier,
            sync_enabled=connection.sync_enabled,
            sync_interval_minutes=connection.sync_interval_minutes,
            sync_days=connection.sync_days,
            historical_sync_state=connection.historical_sync_state,
            historical_sync_start_date=connection.historical_sync_start_date,
            historical_sync_end_date=connection.historical_sync_end_date,
            historical_sync_cursor_date=connection.historical_sync_cursor_date,
            historical_sync_started_at=connection.historical_sync_started_at,
            historical_sync_completed_at=connection.historical_sync_completed_at,
            historical_sync_last_error=connection.historical_sync_last_error,
            last_attempt_at=connection.last_attempt_at,
            last_success_at=connection.last_success_at,
            last_micronutrient_sync_at=connection.last_micronutrient_sync_at,
            next_sync_at=connection.next_sync_at,
            last_error=connection.last_error,
            created_at=connection.created_at,
            updated_at=connection.updated_at,
        ),
    )
    _write_json_array(zip_file, "achievements.json", _achievements(db, user.id))

def _csv_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def _write_csv(
    zip_file: ZipFile,
    filename: str,
    headers: list[str],
    rows: Iterator[tuple[object, ...]],
) -> None:
    with TextIOWrapper(zip_file.open(filename, "w"), encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(headers)
        for row in rows:
            writer.writerow([_csv_cell(value) for value in row])


def _write_csv_export(zip_file: ZipFile, db: Session, user: User) -> None:
    _write_csv(
        zip_file,
        "profile.csv",
        ["username", "language", "timezone", "week_starts_on"],
        iter([(user.username, user.language, user.timezone, user.week_starts_on)]),
    )
    _write_csv(
        zip_file,
        "targets.csv",
        ["valid_from", "valid_to", "calories_kcal", "maintenance_kcal", "activity_mode", "protein_g"],
        (
            (
                item.valid_from.isoformat(),
                item.valid_to.isoformat() if item.valid_to else "",
                item.calories_kcal,
                item.maintenance_kcal or "",
                item.activity_mode,
                item.protein_g,
            )
            for item in _targets(db, user.id)
        ),
    )
    _write_csv(
        zip_file,
        "tracking-overrides.csv",
        ["local_date", "status", "note", "created_at"],
        (
            (item.local_date.isoformat(), item.status, item.note or "", item.created_at.isoformat())
            for item in _tracking_overrides(db, user.id)
        ),
    )
    _write_csv(
        zip_file,
        "samples.csv",
        ["external_sample_id", "source_type", "metric_type", "value", "unit", "start_at", "end_at", "local_date", "timezone"],
        (
            (
                item.external_sample_id or "",
                item.source_type,
                item.metric_type,
                item.value,
                item.unit,
                item.start_at.isoformat(),
                item.end_at.isoformat(),
                item.local_date.isoformat(),
                item.timezone,
            )
            for item in _health_samples(db, user.id)
        ),
    )


def open_user_csv_export(
    user_id: UUID,
    *,
    request_id: str | None = None,
    client_ref: str | None = None,
) -> ExportStream:
    if not _EXPORT_SLOTS.acquire(blocking=False):
        raise ExportBusy
    try:
        return ExportStream(
            user_id,
            request_id=request_id,
            client_ref=client_ref,
            export_writer=_write_csv_export,
            event_name="data.exported",
            achievement_keys=("spreadsheet_ready",),
        )
    except BaseException:
        _EXPORT_SLOTS.release()
        raise


def open_user_export(
    user_id: UUID,
    *,
    request_id: str | None = None,
    client_ref: str | None = None,
) -> ExportStream:
    if not _EXPORT_SLOTS.acquire(blocking=False):
        raise ExportBusy
    try:
        return ExportStream(
            user_id,
            request_id=request_id,
            client_ref=client_ref,
            achievement_keys=("ordered_takeout",),
        )
    except BaseException:
        _EXPORT_SLOTS.release()
        raise
