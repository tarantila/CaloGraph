"""Disposable, exclusively owned Analysis demo account and canonical scalar samples."""

import getpass
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import delete, inspect, or_, select, update
from sqlalchemy.orm import Session

from app.auth.password_policy import MIN_PASSWORD_LENGTH, validate_new_password
from app.auth.security import hash_password
from app.config import settings
from app.database import Base
from app.importers.common import CanonicalSample
from app.models import (
    HealthSample,
    ImportBatch,
    ImportError,
    RawImportPayload,
    User,
    UserAnalysisPreference,
    UserOnboarding,
    UserSession,
    WebAuthnChallenge,
)
from app.nutrition.models import (
    NutritionConsumptionEvent,
    NutritionDailyProjection,
    NutritionDailyProjectionFact,
    NutritionDailyProjectionLineage,
    NutritionExternalIdentity,
    NutritionExternalIdentityLink,
    NutritionFieldObservation,
    NutritionFoodProfile,
    NutritionFoodSnapshot,
    NutritionIngestionRun,
    NutritionProjectionHead,
    NutritionSourceObservation,
)
from app.services.analysis_demo_nutrition import (
    audit_analysis_demo_nutrition,
    ensure_analysis_demo_policy,
    persist_analysis_demo_nutrition,
)
from app.services.user_operation_lock import (
    exclusive_initial_user_operation,
    exclusive_user_lifecycle_operation,
)
from app.source_priority.models import SourcePriorityPolicy, SourcePriorityRule

ANALYSIS_DEMO_USERNAME = "calograph-analysis-demo"
ANALYSIS_DEMO_PROVIDER = "analysis_demo"
ANALYSIS_DEMO_SOURCE_TYPE = "analysis_demo_offline"
_ANCHOR_PREFIX = "analysis-demo:v2:"
_CONNECTOR_VARIANT = "analysis-demo-v2"
_DEMO_TIMEZONE = "Europe/Berlin"
_DEMO_KEYS = (
    "weight_kg",
    "dietary_energy_kcal",
    "vitamin_a_ug",
    "active_energy_kcal",
)
_NAMESPACE = UUID("cd99db78-a22a-49b6-9ad3-5db3d2e9b1ea")
# Seed rows, login sessions, and usage-day records may cascade. Security audit
# events survive deletion by their SET NULL foreign keys; they are not demo-owned data.
_OWNED_USER_TABLES = frozenset(
    {
        "user_analysis_preferences",
        "user_onboarding",
        "import_batches",
        "health_samples",
        "user_sessions",
        "user_usage_days",
    }
)
_NUTRITION_OWNED_TABLES = frozenset({
    "nutrition_ingestion_runs", "nutrition_source_observations",
    "nutrition_external_identities", "nutrition_external_identity_links",
    "nutrition_food_profiles", "nutrition_food_snapshots",
    "nutrition_consumption_events", "nutrition_field_observations",
    "nutrition_daily_projections", "nutrition_daily_projection_facts",
    "nutrition_daily_projection_lineage", "nutrition_projection_heads",
    "source_priority_policies", "source_priority_rules",
})
_PRESERVED_AUDIT_COLUMNS = frozenset({"actor_user_id", "target_user_id"})


class AnalysisDemoOwnershipError(RuntimeError):
    """The account or one of its references is not provably seed-exclusive."""


class AnalysisDemoUnavailable(RuntimeError):
    """Demo-account operations are disabled outside disposable environments."""


def demo_user_id() -> UUID:
    return uuid5(_NAMESPACE, "analysis-demo:user:v2")


def demo_source_instance_id(user_id: UUID) -> UUID:
    return uuid5(_NAMESPACE, f"analysis-demo:source:v2:{user_id}")


def is_owned_analysis_demo_account(db: Session, user_id: UUID) -> bool:
    """Cheap read gate; the full immutable-evidence audit remains seed/remove-only."""
    if settings.environment not in {"development", "test"} or user_id != demo_user_id():
        return False
    user = db.get(User, user_id)
    if user is None or not (
        user.username == ANALYSIS_DEMO_USERNAME and user.is_active
        and not user.is_admin and user.timezone == _DEMO_TIMEZONE
    ):
        return False
    batches = tuple(db.scalars(select(ImportBatch).where(ImportBatch.user_id == user_id)))
    if len(batches) != 1:
        return False
    batch = batches[0]
    marker = batch.client_identifier or ""
    if not marker.startswith(_ANCHOR_PREFIX):
        return False
    try:
        anchor = date.fromisoformat(marker[len(_ANCHOR_PREFIX):])
    except ValueError:
        return False
    return (
        batch.id == _batch_id(user_id, anchor)
        and batch.status == "completed"
        and batch.source_type == ANALYSIS_DEMO_SOURCE_TYPE
        and batch.connector_variant == _CONNECTOR_VARIANT
    )


def _batch_id(user_id: UUID, anchor: date) -> UUID:
    return uuid5(_NAMESPACE, f"analysis-demo:batch:v2:{user_id}:{anchor.isoformat()}")


def _sample_id(user_id: UUID, external_id: str | None) -> UUID:
    if external_id is None:
        raise AnalysisDemoOwnershipError("Analyse-Demosample hat keine externe Seed-ID.")
    return uuid5(_NAMESPACE, f"analysis-demo:sample:v2:{user_id}:{external_id}")


def _require_disposable_environment() -> None:
    if settings.environment not in {"development", "test"}:
        raise AnalysisDemoUnavailable("Analysis-Demodaten sind nur in development/test erlaubt.")


def _samples(user_id: UUID, anchor: date) -> list[CanonicalSample]:
    randomizer = random.Random(20260929)
    zone = ZoneInfo(_DEMO_TIMEZONE)
    source_identifier = str(demo_source_instance_id(user_id))
    samples: list[CanonicalSample] = []
    for offset in range(89, -1, -1):
        day = anchor - timedelta(days=offset)
        at = datetime(day.year, day.month, day.day, 12, tzinfo=zone)
        weight = Decimal("74.0") + Decimal(randomizer.randint(-14, 14)) / 10
        metrics: list[tuple[str, Decimal, str]] = []
        if offset == 89 or offset % 4 == 0:
            metrics.append(("weight_kg", weight, "kg"))
        # Missing activity days have no row; zero activity is a real observation.
        if offset % 11 != 3:
            activity = Decimal(0 if offset % 13 == 0 else randomizer.randint(90, 560))
            metrics.append(("active_energy_kcal", activity, "kcal"))
        for metric, value, unit in metrics:
            samples.append(
                CanonicalSample(
                    metric_type=metric,
                    value=value,
                    unit=unit,
                    original_value=value,
                    original_unit=unit,
                    start_at=at,
                    end_at=at,
                    timezone=_DEMO_TIMEZONE,
                    source_type=ANALYSIS_DEMO_SOURCE_TYPE,
                    source_name="CaloGraph Analysis Demo",
                    source_identifier=source_identifier,
                    external_sample_id=f"analysis-demo:v2:{day.isoformat()}:{metric}",
                    local_date=day,
                )
            )
    return samples


def _same_time(stored: datetime, expected: datetime) -> bool:
    # SQLite drops tzinfo from DateTime(timezone=True); PostgreSQL retains it.
    if stored.tzinfo is None:
        return stored == expected.replace(tzinfo=None)
    return stored == expected


def _audit_samples(
    db: Session, user_id: UUID, batch: ImportBatch, expected: list[CanonicalSample]
) -> None:
    rows = list(db.scalars(select(HealthSample).where(HealthSample.user_id == user_id)))
    by_external_id = {sample.external_sample_id: sample for sample in rows}
    if len(rows) != len(expected) or len(by_external_id) != len(expected):
        raise AnalysisDemoOwnershipError("Analyse-Demosamples fehlen oder sind fremd.")
    for sample in expected:
        row = by_external_id.get(sample.external_sample_id)
        if row is None or not (
            row.id == _sample_id(user_id, sample.external_sample_id)
            and row.import_batch_id == batch.id
            and row.fingerprint == sample.fingerprint(user_id)
            and row.source_type == sample.source_type
            and row.source_name == sample.source_name
            and row.source_identifier == sample.source_identifier
            and row.metric_type == sample.metric_type
            and row.value == sample.value
            and row.unit == sample.unit
            and row.original_value == sample.original_value
            and row.original_unit == sample.original_unit
            and row.local_date == sample.local_date
            and row.timezone == sample.timezone
            and _same_time(row.start_at, sample.start_at)
            and _same_time(row.end_at, sample.end_at)
        ):
            raise AnalysisDemoOwnershipError("Analyse-Demosample stimmt nicht mit dem Seed überein.")


def _audit_other_references(db: Session, user_id: UUID, batch_id: UUID) -> None:
    # A future user-owned table must be reviewed explicitly before this account
    # may be deleted; database cascades alone cannot establish ownership.
    for table in Base.metadata.sorted_tables:
        # Migration 20260918_0031 removes this legacy ORM-mapped table.
        if table.name == "user_provider_preferences" and not inspect(db.connection()).has_table(
            table.name, schema=table.schema
        ):
            continue
        for column in table.columns:
            if not any(fk.target_fullname == "users.id" for fk in column.foreign_keys):
                continue
            if column.name == "user_id" and table.name in (_OWNED_USER_TABLES | _NUTRITION_OWNED_TABLES):
                continue
            if (
                table.name == "security_audit_events"
                and column.name in _PRESERVED_AUDIT_COLUMNS
            ):
                continue
            if db.execute(
                select(column).where(column == user_id).limit(1)
            ).first() is not None:
                raise AnalysisDemoOwnershipError(f"Fremde Referenz auf Demo-Konto: {table.name}.")
    for model in (RawImportPayload, ImportError):
        if db.scalar(select(model.id).where(model.batch_id == batch_id).limit(1)) is not None:
            raise AnalysisDemoOwnershipError("Fremde Import-Referenz auf Demo-Batch.")
    if db.scalar(
        select(HealthSample.id)
        .where(HealthSample.import_batch_id == batch_id, HealthSample.user_id != user_id)
        .limit(1)
    ) is not None:
        raise AnalysisDemoOwnershipError("Demo-Batch enthält Samples eines fremden Kontos.")
    if db.scalar(
        select(WebAuthnChallenge.id)
        .join(UserSession, WebAuthnChallenge.session_id == UserSession.id)
        .where(UserSession.user_id == user_id)
        .limit(1)
    ) is not None:
        raise AnalysisDemoOwnershipError("Fremde Session-Referenz auf Demo-Konto.")
    # All other references into nutrition rows (including unowned extensions)
    # must be rejected rather than silently cascaded on account removal.
    for parent in Base.metadata.sorted_tables:
        if parent.name not in _NUTRITION_OWNED_TABLES or "id" not in parent.c:
            continue
        ids = set(db.scalars(select(parent.c.id).where(parent.c.user_id == user_id)))
        if not ids:
            continue
        for child in Base.metadata.sorted_tables:
            for column in child.c:
                if not any(fk.column.table is parent and fk.column.name == "id" for fk in column.foreign_keys):
                    continue
                if child.name not in _NUTRITION_OWNED_TABLES or "user_id" not in child.c:
                    if db.execute(select(column).where(column.in_(ids)).limit(1)).first() is not None:
                        raise AnalysisDemoOwnershipError("Fremde Referenz auf Demo-Ernährung.")
                elif db.execute(select(column).where(column.in_(ids), child.c.user_id != user_id).limit(1)).first() is not None:
                    raise AnalysisDemoOwnershipError("Fremdes Konto referenziert Demo-Ernährung.")


def _audit_foreign_sources(db: Session, user_id: UUID) -> None:
    source_id = str(demo_source_instance_id(user_id))
    if db.scalar(
        select(HealthSample.id)
        .where(
            HealthSample.user_id != user_id,
            or_(
                HealthSample.source_identifier == source_id,
                HealthSample.source_type == ANALYSIS_DEMO_SOURCE_TYPE,
                HealthSample.external_sample_id.startswith(_ANCHOR_PREFIX),
            ),
        )
        .limit(1)
    ) is not None or db.scalar(
        select(ImportBatch.id)
        .where(
            ImportBatch.user_id != user_id,
            or_(
                ImportBatch.source_type == ANALYSIS_DEMO_SOURCE_TYPE,
                ImportBatch.client_identifier.startswith(_ANCHOR_PREFIX),
            ),
        )
        .limit(1)
    ) is not None:
        raise AnalysisDemoOwnershipError("Demo-Quelle gehört auch einem fremden Konto.")
    source_uuid = demo_source_instance_id(user_id)
    for model in (
        NutritionIngestionRun, NutritionSourceObservation, NutritionExternalIdentity,
        NutritionFoodProfile, NutritionConsumptionEvent,
    ):
        if db.scalar(select(model.id).where(
            model.user_id != user_id,
            or_(model.source_instance_id == source_uuid, model.provider_key == ANALYSIS_DEMO_PROVIDER),
        ).limit(1)) is not None:
            raise AnalysisDemoOwnershipError("Demo-Ernährungsquelle gehört auch einem fremden Konto.")
    if db.scalar(select(NutritionDailyProjectionLineage.id).where(
        NutritionDailyProjectionLineage.user_id != user_id,
        NutritionDailyProjectionLineage.provider_key == ANALYSIS_DEMO_PROVIDER,
    ).limit(1)) is not None:
        raise AnalysisDemoOwnershipError("Fremde Demo-Ernährungsreferenz.")


def _audit_account(db: Session, user: User) -> ImportBatch:
    user_id = demo_user_id()
    if (
        user.id != user_id
        or user.username != ANALYSIS_DEMO_USERNAME
        or user.is_admin
        or not user.is_active
        or user.timezone != _DEMO_TIMEZONE
    ):
        raise AnalysisDemoOwnershipError("Das reservierte Analyse-Demokonto ist nicht seed-exklusiv.")
    onboarding = db.get(UserOnboarding, user_id)
    if onboarding is None or onboarding.current_step != "completed" or onboarding.completed_at is None:
        raise AnalysisDemoOwnershipError("Demo-Onboarding wurde verändert.")
    preferences = list(
        db.scalars(select(UserAnalysisPreference).where(UserAnalysisPreference.user_id == user_id))
    )
    if sorted((row.analysis_key, row.position) for row in preferences) != sorted(
        (key, position) for position, key in enumerate(_DEMO_KEYS)
    ):
        raise AnalysisDemoOwnershipError("Demo-Analyseauswahl wurde verändert.")
    batches = list(db.scalars(select(ImportBatch).where(ImportBatch.user_id == user_id)))
    if len(batches) != 1:
        raise AnalysisDemoOwnershipError("Demo-Importanker fehlt oder ist nicht exklusiv.")
    batch = batches[0]
    marker = batch.client_identifier
    if not marker or not marker.startswith(_ANCHOR_PREFIX):
        raise AnalysisDemoOwnershipError("Demo-Importanker fehlt.")
    try:
        anchor = date.fromisoformat(marker[len(_ANCHOR_PREFIX) :])
    except ValueError as exc:
        raise AnalysisDemoOwnershipError("Ungültiger Demo-Importanker.") from exc
    expected = _samples(user_id, anchor)
    expected_count = len(expected)
    if not (
        marker == f"{_ANCHOR_PREFIX}{anchor.isoformat()}"
        and batch.id == _batch_id(user_id, anchor)
        and batch.source_type == ANALYSIS_DEMO_SOURCE_TYPE
        and batch.connector_variant == _CONNECTOR_VARIANT
        and batch.status == "completed"
        and batch.finished_at is not None
        and batch.started_at == batch.finished_at
        and batch.received == expected_count
        and batch.inserted == expected_count
        and batch.updated == 0
        and batch.skipped == 0
        and batch.failed == 0
        and batch.unknown_types == []
        and batch.payload_hash is None
        and batch.error_message is None
    ):
        raise AnalysisDemoOwnershipError("Demo-Importanker wurde verändert.")
    _audit_foreign_sources(db, user_id)
    _audit_other_references(db, user_id, batch.id)
    _audit_samples(db, user_id, batch, expected)
    audit_analysis_demo_nutrition(
        db, user_id=user_id, start=anchor - timedelta(days=89), end=anchor,
    )
    return batch


def _find_account(db: Session) -> User | None:
    user_id = demo_user_id()
    by_name = db.scalar(select(User).where(User.username == ANALYSIS_DEMO_USERNAME))
    by_id = db.get(User, user_id)
    if (by_name is not None and by_name.id != user_id) or (
        by_id is not None and by_id.username != ANALYSIS_DEMO_USERNAME
    ):
        raise AnalysisDemoOwnershipError("Reservierter Demobenutzername oder Benutzer-ID ist belegt.")
    return by_id


def seed_analysis_demo(db: Session, *, password: str | None, today: date) -> UUID:
    """Create once; audit existing evidence and resume only unfinished projections."""
    _require_disposable_environment()
    try:
        with exclusive_initial_user_operation(db):
            user = _find_account(db)
            if user is not None:
                with exclusive_user_lifecycle_operation(db, user.id):
                    batch = _audit_account(db, user)
                    run = db.scalar(select(NutritionIngestionRun).where(
                        NutritionIngestionRun.user_id == user.id,
                    ))
                    if run is not None and run.status == "running":
                        marker = batch.client_identifier
                        if marker is None:
                            raise AnalysisDemoOwnershipError("Demo-Importanker fehlt.")
                        # End the audit transaction before clean projection sessions
                        # (and SQLite's BEGIN IMMEDIATE) try to write.
                        db.commit()
                        anchor = date.fromisoformat(marker[len(_ANCHOR_PREFIX):])
                        persist_analysis_demo_nutrition(
                            db, user_id=user.id, source_instance_id=demo_source_instance_id(user.id),
                            start=anchor - timedelta(days=89), end=anchor,
                        )
                    else:
                        db.commit()
                    return user.id
            user_id = demo_user_id()
            if db.scalar(select(User.id).where(User.id != user_id).limit(1)) is None:
                raise AnalysisDemoOwnershipError("Zuerst ein echtes Benutzerkonto anlegen.")
            # Refuse orphaned or cross-account seed identities before prompting.
            _audit_foreign_sources(db, user_id)
            with exclusive_user_lifecycle_operation(db, user_id):
                new_password = password if password is not None else getpass.getpass(
                    f"Analyse-Demo-Passwort (mindestens {MIN_PASSWORD_LENGTH} Zeichen): "
                )
                validate_new_password(new_password, ANALYSIS_DEMO_USERNAME)
                user = User(
                    id=user_id,
                    username=ANALYSIS_DEMO_USERNAME,
                    password_hash=hash_password(new_password),
                    timezone=_DEMO_TIMEZONE,
                    is_admin=False,
                )
                samples = _samples(user_id, today)
                now = datetime.now(UTC)
                batch = ImportBatch(
                    id=_batch_id(user_id, today),
                    user_id=user_id,
                    source_type=ANALYSIS_DEMO_SOURCE_TYPE,
                    client_identifier=f"{_ANCHOR_PREFIX}{today.isoformat()}",
                    connector_variant=_CONNECTOR_VARIANT,
                    status="completed",
                    started_at=now,
                    finished_at=now,
                    received=len(samples),
                    inserted=len(samples),
                    updated=0,
                    skipped=0,
                    failed=0,
                )
                db.add(user)
                db.flush()
                db.add(UserOnboarding(user_id=user_id, current_step="completed", completed_at=now))
                db.add_all(
                    UserAnalysisPreference(user_id=user_id, analysis_key=key, position=position)
                    for position, key in enumerate(_DEMO_KEYS)
                )
                db.add(batch)
                db.flush()
                db.add_all(
                    HealthSample(
                        id=_sample_id(user_id, sample.external_sample_id),
                        user_id=user_id,
                        import_batch_id=batch.id,
                        external_sample_id=sample.external_sample_id,
                        fingerprint=sample.fingerprint(user_id),
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
                    )
                    for sample in samples
                )
                ensure_analysis_demo_policy(db, user_id=user_id, start=today - timedelta(days=89))
                persist_analysis_demo_nutrition(
                    db, user_id=user_id, source_instance_id=demo_source_instance_id(user_id),
                    start=today - timedelta(days=89), end=today,
                )
                return user_id
    except Exception:
        db.rollback()
        raise


def remove_analysis_demo(db: Session) -> bool:
    """Delete only an audited, exclusively seed-owned account; otherwise refuse."""
    _require_disposable_environment()
    try:
        with exclusive_user_lifecycle_operation(db, demo_user_id()):
            user = _find_account(db)
            if user is None:
                return False
            batch = _audit_account(db, user)
            # Heads RESTRICT their current projections; dependent evidence is
            # removed only after the full seed-exclusive ownership audit.
            db.execute(delete(NutritionProjectionHead).where(NutritionProjectionHead.user_id == user.id))
            db.execute(delete(NutritionDailyProjectionLineage).where(NutritionDailyProjectionLineage.user_id == user.id))
            db.execute(delete(NutritionDailyProjectionFact).where(NutritionDailyProjectionFact.user_id == user.id))
            db.execute(delete(NutritionDailyProjection).where(NutritionDailyProjection.user_id == user.id))
            db.execute(delete(NutritionExternalIdentityLink).where(NutritionExternalIdentityLink.user_id == user.id))
            db.execute(delete(NutritionConsumptionEvent).where(NutritionConsumptionEvent.user_id == user.id))
            db.execute(update(NutritionFoodProfile).where(NutritionFoodProfile.user_id == user.id).values(current_snapshot_id=None))
            db.execute(delete(NutritionFoodSnapshot).where(NutritionFoodSnapshot.user_id == user.id))
            db.execute(delete(NutritionFoodProfile).where(NutritionFoodProfile.user_id == user.id))
            db.execute(delete(NutritionFieldObservation).where(NutritionFieldObservation.user_id == user.id))
            db.execute(delete(NutritionExternalIdentity).where(NutritionExternalIdentity.user_id == user.id))
            db.execute(delete(NutritionSourceObservation).where(NutritionSourceObservation.user_id == user.id))
            db.execute(delete(NutritionIngestionRun).where(NutritionIngestionRun.user_id == user.id))
            db.execute(delete(SourcePriorityRule).where(SourcePriorityRule.user_id == user.id))
            db.execute(delete(SourcePriorityPolicy).where(SourcePriorityPolicy.user_id == user.id))
            db.execute(delete(HealthSample).where(HealthSample.user_id == user.id))
            db.execute(delete(ImportBatch).where(ImportBatch.id == batch.id))
            db.execute(delete(User).where(User.id == user.id))
            db.commit()
            return True
    except Exception:
        db.rollback()
        raise
