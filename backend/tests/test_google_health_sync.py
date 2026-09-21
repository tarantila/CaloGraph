from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.google_health.client import (
    ActiveEnergyBurnedDataPoint,
    GoogleHealthDataPointPage,
    NutritionLog,
    NutritionLogDataPoint,
    NutritionLogInterval,
    NutritionLogPage,
    NutritionQuantity,
    WeightDataPoint,
)
from app.google_health.constants import GOOGLE_HEALTH_SCOPES
from app.google_health.errors import (
    GoogleHealthAuthenticationError,
    GoogleHealthInvalidResponseError,
    GoogleHealthTransientError,
)
from app.models import GoogleHealthConnection, HealthSample, User
from app.nutrition.models import NutritionSourceObservation
from app.services.credential_crypto import decrypt_credential, encrypt_credential
from app.services.google_health_sync import (
    GoogleHealthDomainResult,
    GoogleHealthSyncResult,
    GoogleHealthSyncService,
)

START = date(2026, 9, 15)
END = date(2026, 9, 15)
POINT_TIME = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


def _nutrition(name: str) -> NutritionLogDataPoint:
    return NutritionLogDataPoint(
        name=name,
        nutrition_log=NutritionLog(
            interval=NutritionLogInterval(
                start_time=POINT_TIME,
                end_time=datetime(2026, 9, 15, 10, 30, tzinfo=UTC),
                start_utc_offset="0s",
                end_utc_offset="0s",
                civil_start_time=POINT_TIME,
                civil_end_time=POINT_TIME,
            ),
            energy=NutritionQuantity(Decimal("500"), "kcal"),
        ),
    )


def _activity(name: str) -> ActiveEnergyBurnedDataPoint:
    return ActiveEnergyBurnedDataPoint(
        name=name,
        start_time=POINT_TIME,
        end_time=datetime(2026, 9, 15, 10, 30, tzinfo=UTC),
        value=Decimal("100"),
        unit="kcal",
    )


def _weight(name: str) -> WeightDataPoint:
    return WeightDataPoint(
        name=name,
        start_time=POINT_TIME,
        end_time=POINT_TIME,
        value=Decimal("70"),
        unit="kilograms",
    )

class _Client:
    def __init__(
        self,
        *,
        activity_error: Exception | None = None,
        points: int = 1,
        exact_fill: bool = False,
    ) -> None:
        self.activity_error = activity_error
        self.points = points
        self.exact_fill = exact_fill
        self.calls: list[str] = []

    def get_nutrition_log_page(
        self, *, page_size, page_token=None, civil_start_time=None, civil_end_time=None
    ):
        del page_size, civil_start_time, civil_end_time
        self.calls.append(f"nutrition:{page_token}")
        name_suffix = "" if page_token is None else f"-{page_token}"
        return NutritionLogPage(
            data_points=tuple(_nutrition(f"nutrition{name_suffix}-{idx}") for idx in range(self.points)),
            next_page_token=(
                "nutrition-next"
                if page_token is None and (self.points == 1 or self.exact_fill)
                else None
            ),
            page_size=self.points,
            page_token=page_token,
            start_time=None,
            end_time=None,
            civil_start_time=START,
            civil_end_time=END,
        )

    def get_data_points_page(
        self, data_type, *, start_time=None, end_time=None, page_token=None, page_size
    ):
        del start_time, end_time, page_size
        self.calls.append(f"{data_type}:{page_token}")
        if data_type == "active-energy-burned" and self.activity_error is not None:
            raise self.activity_error
        point_factory = _activity if data_type == "active-energy-burned" else _weight
        return GoogleHealthDataPointPage(
            data_points=tuple(point_factory(f"{data_type}-{idx}") for idx in range(self.points)),
            next_page_token=(
                f"{data_type}-next" if page_token is None and self.exact_fill else None
            ),
            page_size=self.points,
            page_token=page_token,
            data_type=data_type,
            start_time=POINT_TIME,
            end_time=POINT_TIME,
        )

    def close(self) -> None:
        self.calls.append("close")


class _IteratorClient(_Client):
    def iter_data_points_pages(
        self,
        data_type,
        *,
        start_time=None,
        end_time=None,
        page_size,
        max_pages,
    ):
        del start_time, end_time, page_size, max_pages
        self.calls.append(f"{data_type}:None")
        point_factory = _activity if data_type == "active-energy-burned" else _weight
        yield GoogleHealthDataPointPage(
            data_points=(point_factory(f"{data_type}-0"),),
            next_page_token=f"{data_type}-next",
            page_size=1,
            page_token=None,
            data_type=data_type,
            start_time=POINT_TIME,
            end_time=POINT_TIME,
        )




class _MixedFailureClient(_Client):
    def get_nutrition_log_page(
        self, *, page_size, page_token=None, civil_start_time=None, civil_end_time=None
    ):
        del page_size, page_token, civil_start_time, civil_end_time
        raise GoogleHealthTransientError()

    def get_data_points_page(
        self, data_type, *, start_time=None, end_time=None, page_token=None, page_size
    ):
        if data_type == "active-energy-burned":
            raise GoogleHealthAuthenticationError()
        return super().get_data_points_page(
            data_type,
            start_time=start_time,
            end_time=end_time,
            page_token=page_token,
            page_size=page_size,
        )
def _service(monkeypatch, client: _Client, *, max_points: int = 10):
    service = GoogleHealthSyncService(
        session_factory=lambda: None,  # type: ignore[arg-type]
        client_factory=lambda _: client,
        credentials_factory=lambda _token, _client_id, _client_secret: object(),
        decrypt_refresh_token=lambda _: "refresh",
        max_pages=2,
        max_points=max_points,
    )
    monkeypatch.setattr(
        service,
        "_snapshot",
        lambda _user_id: (uuid4(), "UTC", b"encrypted", "client-id", "client-secret"),
    )
    persisted_domains: set[str] = set()

    def persist_once(*, domain: str, points: tuple[object, ...]) -> int:
        if domain in persisted_domains:
            return 0
        persisted_domains.add(domain)
        return len(points)

    monkeypatch.setattr(
        service,
        "_persist_nutrition",
        lambda **kwargs: persist_once(domain="nutrition", points=kwargs["points"]),
    )
    monkeypatch.setattr(
        service,
        "_persist_scalar",
        lambda **kwargs: persist_once(
            domain="activity" if kwargs["activity"] else "weight",
            points=kwargs["points"],
        ),
    )
    monkeypatch.setattr(service, "_update_retry_status", lambda **_: None)
    return service


def test_sync_returns_safe_results_for_all_three_domains(monkeypatch) -> None:
    client = _Client()
    result = _service(monkeypatch, client).sync(user_id=uuid4(), requested_start=START, requested_end=END)

    assert result.status == "success"
    assert result.nutrition.status == "success"
    assert result.activity_energy.status == "success"
    assert result.weight.status == "success"
    assert result.nutrition.fetched_count == result.nutrition.persisted_count == 2
    assert result.activity_energy.fetched_count == result.activity_energy.persisted_count == 1
    assert result.weight.fetched_count == result.weight.persisted_count == 1
    assert "nutrition-0" not in str(result)
    assert "100" not in str(result)


def test_activity_failure_keeps_nutrition_and_weight_success(monkeypatch) -> None:
    client = _Client(activity_error=GoogleHealthTransientError())
    result = _service(monkeypatch, client).sync(
        user_id=uuid4(), requested_start=START, requested_end=END
    )

    assert result.status == "partial_failure"
    assert result.nutrition.persisted_count == 2
    assert result.weight.persisted_count == 1
    assert result.activity_energy.status == "failed"
    assert result.activity_energy.error_code == "transient_error"


def test_invalid_response_has_bounded_domain_diagnostic(monkeypatch) -> None:
    error = GoogleHealthInvalidResponseError(
        "invalid",
        upstream_status_code=200,
        parser_stage="scalar_data_point",
        structural_reason_code="missing_required_field",
    )
    client = _Client(activity_error=error)
    result = _service(monkeypatch, client).sync(
        user_id=uuid4(), requested_start=START, requested_end=END
    )

    diagnostic = result.activity_energy.diagnostic
    assert diagnostic is not None
    assert diagnostic.domain == "activity_energy"
    assert diagnostic.operation == "activity_read"
    assert diagnostic.endpoint_key == "active_energy_burned_data_points"
    assert diagnostic.parser_stage == "scalar_data_point"
    assert diagnostic.structural_reason_code == "missing_required_field"
    assert diagnostic.error_category == "invalid_response"
    assert diagnostic.upstream_status_code == 200
    assert diagnostic.retryable is False
    assert diagnostic.reauth_required is False


def test_each_domain_has_independent_finite_point_budget(monkeypatch) -> None:
    client = _Client(points=3)
    result = _service(monkeypatch, client, max_points=2).sync(
        user_id=uuid4(), requested_start=START, requested_end=END
    )

    assert result.status == "success"
    assert result.nutrition.status == "truncated"
    assert result.activity_energy.status == "truncated"
    assert result.weight.status == "truncated"
    assert result.nutrition.fetched_count == 2
    assert result.activity_energy.fetched_count == 2
    assert result.weight.fetched_count == 2
    assert len(client.calls) == 4  # one page per domain plus close

def test_exact_fill_direct_nutrition_stops_without_overread(monkeypatch) -> None:
    client = _Client(points=1, exact_fill=True)
    service = _service(monkeypatch, client, max_points=1)

    points, truncated = service._pages(
        client, data_type="nutrition-log", start=START, end=END, timezone="UTC"
    )

    assert len(points) == 1
    assert truncated is True
    assert client.calls == ["nutrition:None"]


def test_exact_fill_iterator_activity_stops_without_overread(monkeypatch) -> None:
    client = _IteratorClient()
    service = _service(monkeypatch, client, max_points=1)

    points, truncated = service._pages(
        client, data_type="active-energy-burned", start=START, end=END, timezone="UTC"
    )

    assert len(points) == 1
    assert truncated is True
    assert client.calls == ["active-energy-burned:None"]


def test_exact_fill_iterator_weight_stops_without_overread(monkeypatch) -> None:
    client = _IteratorClient()
    service = _service(monkeypatch, client, max_points=1)

    points, truncated = service._pages(
        client, data_type="weight", start=START, end=END, timezone="UTC"
    )

    assert len(points) == 1
    assert truncated is True
    assert client.calls == ["weight:None"]


def test_missing_scope_short_circuits_before_credentials_or_provider_io(monkeypatch) -> None:
    calls: list[str] = []
    service = GoogleHealthSyncService(
        session_factory=lambda: None,  # type: ignore[arg-type]
        client_factory=lambda _: calls.append("client") or None,  # type: ignore[return-value]
        credentials_factory=lambda _token, _client_id, _client_secret: calls.append("credentials") or object(),
        decrypt_refresh_token=lambda _: calls.append("decrypt") or "refresh",
    )
    monkeypatch.setattr(service, "_snapshot", lambda _user_id: "scope_missing")
    monkeypatch.setattr(service, "_update_retry_status", lambda **_: None)

    result = service.sync(user_id=uuid4(), requested_start=START, requested_end=END)

    assert result.status == "reauth_required"
    assert all(domain.status == "reauth_required" for domain in (
        result.nutrition, result.activity_energy, result.weight
    ))
    assert all(domain.error_code == "scope_missing" for domain in (
        result.nutrition, result.activity_energy, result.weight
    ))
    assert calls == []

def _real_connection(db: Session, user: User, scopes: list[str]) -> GoogleHealthConnection:
    connection = GoogleHealthConnection(
        user_id=user.id,
        client_id="client-id",
        encrypted_client_secret=encrypt_credential("client-secret"),
        encrypted_refresh_token=encrypt_credential("refresh-token"),
        granted_scopes=scopes,
        state="active",
    )
    db.add(connection)
    db.commit()
    db.refresh(connection)
    return connection


def test_real_scope_preflight_reauth_preserves_token_and_skips_provider_io(
    db: Session, user: User, monkeypatch
) -> None:
    connection = _real_connection(db, user, list(GOOGLE_HEALTH_SCOPES)[:-1])
    calls: list[str] = []
    service = GoogleHealthSyncService(
        session_factory=SessionLocal,
        decrypt_refresh_token=lambda _: calls.append("decrypt") or "refresh",
        credentials_factory=lambda _token, _client_id, _client_secret: calls.append("credentials") or object(),
        client_factory=lambda _: calls.append("client") or None,  # type: ignore[return-value]
    )

    result = service.sync(user_id=user.id, requested_start=START, requested_end=END)

    assert result.status == "reauth_required"
    assert all(domain.error_code == "scope_missing" for domain in (
        result.nutrition, result.activity_energy, result.weight
    ))
    assert calls == []
    db.expire_all()
    refreshed = db.get(GoogleHealthConnection, connection.id)
    assert refreshed is not None
    assert refreshed.state == "reauth_required"
    assert refreshed.last_error == "scope_missing"
    assert decrypt_credential(refreshed.encrypted_refresh_token) == "refresh-token"


def test_reauth_failure_precedes_earlier_transient_failure_in_bookkeeping(
    db: Session, user: User, monkeypatch
) -> None:
    connection = _real_connection(db, user, list(GOOGLE_HEALTH_SCOPES))
    client = _MixedFailureClient()
    service = _service(monkeypatch, client)
    service._session_factory = SessionLocal
    monkeypatch.setattr(
        service,
        "_snapshot",
        lambda _user_id: (
            connection.id,
            user.timezone,
            connection.encrypted_refresh_token,
            "client-id",
            "client-secret",
        ),
    )
    monkeypatch.setattr(service, "_persist_scalar", GoogleHealthSyncService._persist_scalar.__get__(service))

    result = service.sync(user_id=user.id, requested_start=START, requested_end=END)

    assert result.status == "partial_failure"
    assert result.nutrition.error_code == "transient_error"
    assert result.activity_energy.status == "reauth_required"
    assert result.activity_energy.error_code == "reauth_required"
    assert result.weight.status == "reauth_required"
    db.expire_all()
    refreshed = db.get(GoogleHealthConnection, connection.id)
    assert refreshed is not None
    assert refreshed.state == "reauth_required"
    assert refreshed.last_error == "reauth_required"


def test_real_domain_commits_and_connection_failure_bookkeeping(
    db: Session, user: User, monkeypatch
) -> None:
    connection = _real_connection(db, user, list(GOOGLE_HEALTH_SCOPES))
    client = _Client(activity_error=GoogleHealthTransientError())
    service = _service(monkeypatch, client)
    service._session_factory = SessionLocal
    monkeypatch.setattr(
        service,
        "_snapshot",
        lambda _user_id: (
            connection.id,
            user.timezone,
            connection.encrypted_refresh_token,
            "client-id",
            "client-secret",
        ),
    )
    # Exercise real per-domain scalar/nutrition transactions while keeping
    # projection setup out of this failure-isolation assertion.
    monkeypatch.setattr(
        "app.services.google_health_sync.bootstrap_nutrition_priority",
        lambda **_: None,
    )
    monkeypatch.setattr(
        "app.services.google_health_sync.rebuild_affected_nutrition_days",
        lambda **_: None,
    )
    monkeypatch.setattr(service, "_persist_nutrition", GoogleHealthSyncService._persist_nutrition.__get__(service))
    monkeypatch.setattr(service, "_persist_scalar", GoogleHealthSyncService._persist_scalar.__get__(service))

    result = service.sync(user_id=user.id, requested_start=START, requested_end=END)

    assert result.status == "partial_failure"
    assert result.weight.fetched_count == 1
    assert result.weight.persisted_count == 1
    assert result.activity_energy.error_code == "transient_error"
    nutrition_rows = (
        db.scalar(
            select(func.count())
            .select_from(NutritionSourceObservation)
            .where(NutritionSourceObservation.user_id == user.id)
        )
        or 0
    )
    weight_rows = (
        db.scalar(
            select(func.count())
            .select_from(HealthSample)
            .where(HealthSample.user_id == user.id)
        )
        or 0
    )
    assert nutrition_rows == result.nutrition.persisted_count == 2
    assert weight_rows == result.weight.persisted_count == 1
    db.expire_all()
    refreshed = db.get(GoogleHealthConnection, connection.id)
    assert refreshed is not None
    assert refreshed.last_error == "transient_error"


def test_post_commit_projection_failure_reports_committed_nutrition_rows(
    db: Session, user: User, monkeypatch
) -> None:
    connection = _real_connection(db, user, list(GOOGLE_HEALTH_SCOPES))
    client = _Client()
    service = _service(monkeypatch, client)
    service._session_factory = SessionLocal
    monkeypatch.setattr(
        service,
        "_snapshot",
        lambda _user_id: (
            connection.id,
            user.timezone,
            connection.encrypted_refresh_token,
            "client-id",
            "client-secret",
        ),
    )
    monkeypatch.setattr(service, "_persist_nutrition", GoogleHealthSyncService._persist_nutrition.__get__(service))
    monkeypatch.setattr(service, "_persist_scalar", GoogleHealthSyncService._persist_scalar.__get__(service))

    def fail_bootstrap(**_: object) -> None:
        raise RuntimeError("synthetic projection failure")

    monkeypatch.setattr("app.services.google_health_sync.bootstrap_nutrition_priority", fail_bootstrap)

    result = service.sync(user_id=user.id, requested_start=START, requested_end=END)

    assert result.nutrition.status == "failed"
    assert result.nutrition.error_code == "persistence_error"
    persisted_rows = db.scalar(select(func.count()).select_from(NutritionSourceObservation)) or 0
    assert result.nutrition.persisted_count == persisted_rows


def test_no_data_completion_records_success_without_sensitive_values(
    db: Session, user: User, monkeypatch
) -> None:
    connection = _real_connection(db, user, list(GOOGLE_HEALTH_SCOPES))
    client = _Client(points=0)
    service = _service(monkeypatch, client)
    service._session_factory = SessionLocal
    monkeypatch.setattr(
        service,
        "_snapshot",
        lambda _user_id: (
            connection.id,
            user.timezone,
            connection.encrypted_refresh_token,
            "client-id",
            "client-secret",
        ),
    )
    monkeypatch.setattr(
        "app.services.google_health_sync.bootstrap_nutrition_priority",
        lambda **_: None,
    )
    monkeypatch.setattr(
        "app.services.google_health_sync.rebuild_affected_nutrition_days",
        lambda **_: None,
    )
    monkeypatch.setattr(service, "_persist_nutrition", GoogleHealthSyncService._persist_nutrition.__get__(service))
    monkeypatch.setattr(service, "_persist_scalar", GoogleHealthSyncService._persist_scalar.__get__(service))

    result = service.sync(user_id=user.id, requested_start=START, requested_end=END)

    assert result.status == "no_data"
    db.expire_all()
    refreshed = db.get(GoogleHealthConnection, connection.id)
    assert refreshed is not None
    assert refreshed.state == "active"
    assert refreshed.last_error is None
    assert refreshed.last_success_at is not None


def _synthetic_sync_result(status: str, error: str | None = None) -> GoogleHealthSyncResult:
    item = GoogleHealthDomainResult(
        status="failed" if error else status,
        fetched_count=0,
        persisted_count=0,
        requested_start=START,
        requested_end=END,
        error_code=error,
    )
    return GoogleHealthSyncResult(
        status=status,
        nutrition=item,
        activity_energy=item,
        weight=item,
    )


def test_sync_retries_transient_errors_with_exact_backoff_and_attempts(monkeypatch) -> None:
    service = GoogleHealthSyncService(session_factory=lambda: None)  # type: ignore[arg-type]
    outcomes = iter(
        [
            _synthetic_sync_result("failed", "transient_error"),
            _synthetic_sync_result("success"),
        ]
    )
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setattr(service, "_sync_once", lambda **_: calls.append(1) or next(outcomes))
    monkeypatch.setattr(service, "_update_retry_status", lambda **kwargs: None)
    service._sleep = sleeps.append

    result = service.sync(user_id=uuid4(), requested_start=START, requested_end=END)

    assert result.status == "success"
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_sync_exhausts_three_real_attempts_without_intermediate_result(monkeypatch) -> None:
    service = GoogleHealthSyncService(session_factory=lambda: None)  # type: ignore[arg-type]
    calls: list[int] = []
    sleeps: list[float] = []
    statuses: list[dict[str, object]] = []
    monkeypatch.setattr(
        service,
        "_sync_once",
        lambda **_: calls.append(1) or _synthetic_sync_result("failed", "provider_error"),
    )
    monkeypatch.setattr(service, "_update_retry_status", lambda **kwargs: statuses.append(kwargs))
    service._sleep = sleeps.append

    result = service.sync(user_id=uuid4(), requested_start=START, requested_end=END)

    assert result.status == "failed"
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]
    assert statuses[-1]["attempt"] == 3
    assert statuses[-1]["state"] == "failed"


def test_sync_does_not_retry_non_retryable_error(monkeypatch) -> None:
    service = GoogleHealthSyncService(session_factory=lambda: None)  # type: ignore[arg-type]
    calls: list[int] = []
    sleeps: list[float] = []
    monkeypatch.setattr(
        service,
        "_sync_once",
        lambda **_: calls.append(1) or _synthetic_sync_result("failed", "invalid_response"),
    )
    monkeypatch.setattr(service, "_update_retry_status", lambda **kwargs: None)
    service._sleep = sleeps.append

    result = service.sync(user_id=uuid4(), requested_start=START, requested_end=END)

    assert result.status == "failed"
    assert len(calls) == 1
    assert sleeps == []


def test_sync_success_records_first_attempt_as_one_of_three(monkeypatch) -> None:
    service = GoogleHealthSyncService(session_factory=lambda: None)  # type: ignore[arg-type]
    statuses: list[dict[str, object]] = []
    monkeypatch.setattr(service, "_sync_once", lambda **_: _synthetic_sync_result("success"))
    monkeypatch.setattr(service, "_update_retry_status", lambda **kwargs: statuses.append(kwargs))

    result = service.sync(user_id=uuid4(), requested_start=START, requested_end=END)

    assert result.status == "success"
    assert len(statuses) == 1
    assert statuses[0]["attempt"] == 1
    assert statuses[0]["state"] == "completed"
    assert statuses[0]["success"] is True
