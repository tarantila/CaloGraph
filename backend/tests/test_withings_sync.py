from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import HealthSample, User, WithingsConnection
from app.services.user_operation_lock import UserOperationBusy
from app.withings.client import WithingsPageResult
from app.withings.errors import WithingsInvalidResponseError, WithingsProviderUnavailableError
from app.withings.parsers import (
    ActivityPage,
    MeasurePage,
    WithingsActivity,
    WithingsMeasure,
    WithingsMeasureGroup,
    parse_measure_response,
)
from app.withings.service import disconnect_withings
from app.withings.sync import WithingsSyncService


class FakeClient:
    def __init__(
        self,
        *,
        activity_error: Exception | None = None,
        measure_group: WithingsMeasureGroup | None = None,
        measure_groups: tuple[WithingsMeasureGroup, ...] | None = None,
        measure_error: Exception | None = None,
    ) -> None:
        self.activity_error = activity_error
        self.measure_groups = (
            measure_groups
            if measure_groups is not None
            else (measure_group,) if measure_group is not None else None
        )
        self.measure_error = measure_error
        self.measure_ranges: list[tuple[int, int]] = []
        self.activity_ranges: list[tuple[date, date]] = []

    def fetch_measure_pages(self, **kwargs):
        if self.measure_error is not None:
            raise self.measure_error
        startdate = kwargs["startdate"]
        enddate = kwargs["enddate"]
        self.measure_ranges.append((startdate, enddate))
        groups = self.measure_groups
        if groups is None:
            groups = (
                WithingsMeasureGroup(
                    44,
                    datetime(2026, 9, 20, 8, tzinfo=UTC),
                    "UTC",
                    (WithingsMeasure(1, "weight_kg", "kg", Decimal("80.0"), Decimal("800"), -1),),
                ),
            )
        returned = tuple(
            group
            for group in groups
            if startdate
            <= int(group.measured_at.astimezone(UTC).timestamp())
            <= enddate
        )
        return WithingsPageResult(
            measure_pages=(MeasurePage(returned, 0, False, None),),
            records_read=len(returned),
            pages_read=1,
        )

    def fetch_activity_pages(self, **kwargs):
        self.activity_ranges.append((kwargs["startdateymd"], kwargs["enddateymd"]))
        if self.activity_error:
            raise self.activity_error
        page = ActivityPage((WithingsActivity(date(2026, 9, 20), Decimal("0")),), 0, False, None)
        return WithingsPageResult(activity_pages=(page,), records_read=1, pages_read=1)

    def close(self):
        return None


def test_sync_derives_missing_provider_timezone_local_date_from_user_timezone(
    db, user, monkeypatch
) -> None:
    connection = _connection(db, user)
    connection.last_activity_error_category = "provider_error"
    db.commit()
    group = WithingsMeasureGroup(
        45,
        datetime(2026, 9, 20, 22, 30, tzinfo=UTC),
        None,
        (WithingsMeasure(1, "weight_kg", "kg", Decimal("81.0"), Decimal("810"), -1),),
    )
    fake = FakeClient(measure_group=group)
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")

    result = WithingsSyncService(
        session_factory=lambda: db, client_factory=lambda token: fake
    ).sync(
        user_id=user.id,
        requested_start=date(2026, 9, 21),
        requested_end=date(2026, 9, 21),
    )

    row = db.scalar(select(HealthSample).where(HealthSample.user_id == user.id))
    assert result.weight.status == "success"
    assert row is not None
    assert row.local_date == date(2026, 9, 21)
    assert row.timezone == "Europe/Berlin"
    assert group.timezone is None
    assert connection.last_activity_error_category is None


def test_sync_fetch_window_covers_provider_day_and_filters_neighbor_day(
    db, user, monkeypatch
) -> None:
    user.timezone = "UTC"
    _connection(db, user)
    provider_zone = ZoneInfo("Pacific/Kiritimati")
    neighbor = WithingsMeasureGroup(
        46,
        datetime(2026, 9, 19, 0, 30, tzinfo=provider_zone),
        "Pacific/Kiritimati",
        (WithingsMeasure(1, "weight_kg", "kg", Decimal("790"), Decimal("7900"), -1),),
    )
    requested_day = WithingsMeasureGroup(
        47,
        datetime(2026, 9, 20, 0, 30, tzinfo=provider_zone),
        "Pacific/Kiritimati",
        (WithingsMeasure(1, "weight_kg", "kg", Decimal("810"), Decimal("8100"), -1),),
    )
    fake = FakeClient(measure_groups=(neighbor, requested_day))
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")

    result = WithingsSyncService(
        session_factory=lambda: db, client_factory=lambda token: fake
    ).sync(
        user_id=user.id,
        requested_start=date(2026, 9, 20),
        requested_end=date(2026, 9, 20),
    )

    requested_timestamp = int(requested_day.measured_at.astimezone(UTC).timestamp())
    assert fake.measure_ranges[0][0] <= requested_timestamp <= fake.measure_ranges[0][1]
    assert result.weight.status == "success"
    rows = list(
        db.scalars(
            select(HealthSample).where(
                HealthSample.user_id == user.id,
                HealthSample.metric_type == "weight_kg",
            )
        )
    )
    assert len(rows) == 1
    assert rows[0].external_sample_id == "47:1"
    assert rows[0].local_date == date(2026, 9, 20)


def test_sync_uses_parser_local_date_for_z_timezone(db, user, monkeypatch) -> None:
    user.timezone = "Pacific/Honolulu"
    _connection(db, user)
    measured_at = datetime(2026, 9, 20, 0, 30, tzinfo=UTC)
    group = parse_measure_response(
        {
            "status": 0,
            "body": {
                "measuregrps": [
                    {
                        "grpid": 48,
                        "date": int(measured_at.timestamp()),
                        "timezone": "Z",
                        "measures": [{"type": 1, "value": 810, "unit": -1}],
                    }
                ]
            },
        }
    ).groups[0]
    assert group.local_date == date(2026, 9, 20)
    fake = FakeClient(measure_group=group)
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")

    result = WithingsSyncService(
        session_factory=lambda: db, client_factory=lambda token: fake
    ).sync(
        user_id=user.id,
        requested_start=date(2026, 9, 20),
        requested_end=date(2026, 9, 20),
    )

    row = db.scalar(
        select(HealthSample).where(
            HealthSample.user_id == user.id,
            HealthSample.metric_type == "weight_kg",
        )
    )
    assert result.weight.status == "success"
    assert row is not None
    assert row.local_date == date(2026, 9, 20)
    assert row.timezone == "UTC"


def test_sync_logs_only_bounded_parser_diagnostic(db, user, monkeypatch, caplog) -> None:
    _connection(db, user)
    with pytest.raises(WithingsInvalidResponseError) as caught:
        parse_measure_response({"status": 0, "body": {"measuregrps": "sensitive-payload"}})
    fake = FakeClient(measure_error=caught.value)
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")

    with caplog.at_level(logging.WARNING, logger="app.withings.sync"):
        result = WithingsSyncService(
            session_factory=lambda: db, client_factory=lambda token: fake
        ).sync(
            user_id=user.id,
            requested_start=date(2026, 9, 20),
            requested_end=date(2026, 9, 20),
        )

    assert result.weight.status == "failed"
    assert result.weight.error_code == "invalid_response"
    assert "field_path=body.measuregrps" in caplog.text
    assert "observed_json_type=string" in caplog.text
    assert "sensitive-payload" not in caplog.text


def _connection(db, user):
    connection = WithingsConnection(
        user_id=user.id,
        state="active",
        encrypted_access_token=b"test-token",
        granted_scopes=["user.metrics", "user.activity"],
    )
    db.add(connection)
    db.commit()
    return connection


def test_sync_preserves_fetched_activity_count_and_logs_safe_persistence_failure(
    db, user, monkeypatch, caplog
) -> None:
    connection = _connection(db, user)
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")

    def fail_persist(*args, **kwargs):
        del args, kwargs
        raise TypeError("private sample value")

    monkeypatch.setattr("app.withings.sync._persist_sample_batch", fail_persist)
    fake = FakeClient()

    with caplog.at_level(logging.WARNING, logger="app.withings.sync"):
        result = WithingsSyncService(
            session_factory=lambda: db, client_factory=lambda token: fake
        ).sync(
            user_id=user.id,
            requested_start=date(2026, 9, 20),
            requested_end=date(2026, 9, 20),
        )

    assert result.activity_energy.status == "failed"
    assert result.activity_energy.fetched_count == 1
    assert result.activity_energy.persisted_count == 0
    assert result.activity_energy.error_code == "persistence_error"
    assert connection.last_activity_error_category == "persistence_error"
    assert "stage=sample_persistence" in caplog.text
    assert "exception_type=TypeError" in caplog.text
    assert "private sample value" not in caplog.text

def test_sync_logs_database_exception_without_driver_message(db, user, monkeypatch, caplog) -> None:
    _connection(db, user)
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")

    class DriverDiagnostics:
        constraint_name = "uq_sample_user_fingerprint"
        table_name = "health_samples"
        column_name = "fingerprint"
        sqlstate = "23505"

    class DriverException(Exception):
        diag = DriverDiagnostics()
        sqlstate = "23505"

    def fail_persist(*args, **kwargs):
        del args, kwargs
        raise IntegrityError("insert", {}, DriverException("private driver message"))

    monkeypatch.setattr("app.withings.sync._persist_sample_batch", fail_persist)
    with caplog.at_level(logging.WARNING, logger="app.withings.sync"):
        result = WithingsSyncService(
            session_factory=lambda: db, client_factory=lambda token: FakeClient()
        ).sync(
            user_id=user.id,
            requested_start=date(2026, 9, 20),
            requested_end=date(2026, 9, 20),
        )

    assert result.activity_energy.status == "failed"
    assert result.activity_energy.fetched_count == 1
    assert "exception_type=IntegrityError" in caplog.text
    assert "safe_message=database_operation_failed" in caplog.text
    assert "constraint_name=uq_sample_user_fingerprint" in caplog.text
    assert "table_name=health_samples" in caplog.text
    assert "column_name=fingerprint" in caplog.text
    assert "sqlstate=23505" in caplog.text
    assert "private driver message" not in caplog.text

def test_sync_persists_high_precision_activity_with_direct_scale_projections(
    db, user, monkeypatch
) -> None:
    user_id = user.id
    _connection(db, user)
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")
    raw_calories = Decimal("1.2345674999996")

    class HighPrecisionClient(FakeClient):
        def fetch_activity_pages(self, **kwargs):
            self.activity_ranges.append((kwargs["startdateymd"], kwargs["enddateymd"]))
            page = ActivityPage(
                (WithingsActivity(date(2026, 9, 20), raw_calories),),
                0,
                False,
                None,
            )
            return WithingsPageResult(activity_pages=(page,), records_read=1, pages_read=1)

    result = WithingsSyncService(
        session_factory=lambda: db, client_factory=lambda token: HighPrecisionClient()
    ).sync(
        user_id=user_id,
        requested_start=date(2026, 9, 20),
        requested_end=date(2026, 9, 20),
    )

    sample = db.scalar(
        select(HealthSample).where(
            HealthSample.user_id == user_id,
            HealthSample.source_type == "withings_activity_v2",
        )
    )
    assert result.activity_energy.status == "success"
    assert result.activity_energy.fetched_count == 1
    assert result.activity_energy.persisted_count == 1
    assert sample is not None
    assert sample.metric_type == "active_energy_kcal"
    assert sample.unit == "kcal"
    assert sample.original_value == Decimal("1.234567500000")
    assert sample.original_value.as_tuple().exponent == -12
    assert sample.value == Decimal("1.234567")
    assert sample.value.as_tuple().exponent == -6

def test_sync_uses_calendar_day_range_and_isolates_activity_failure(
    db, user, monkeypatch
) -> None:
    connection = _connection(db, user)
    fake = FakeClient(activity_error=WithingsProviderUnavailableError(upstream_status_code=501))
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")

    result = WithingsSyncService(
        session_factory=lambda: db, client_factory=lambda token: fake
    ).sync(
        user_id=user.id,
        requested_start=date(2026, 9, 20),
        requested_end=date(2026, 9, 20),
    )

    assert result.status == "partial_failure"
    assert result.weight.status == "success"
    assert result.activity_energy.status == "failed"
    assert result.activity_energy.error_code == "provider_error"

    assert connection.last_activity_error_category == "provider_error"
    assert result.activity_energy.requested_start == date(2026, 9, 20)

    assert fake.activity_ranges == [(date(2026, 9, 20), date(2026, 9, 20))]
    rows = list(db.scalars(select(HealthSample).where(HealthSample.user_id == user.id)))
    assert len(rows) == 1
    assert rows[0].source_type == "withings_measure_v1"
    assert "provider details" not in str(result)
    assert connection.id


def test_sync_rejects_cross_user_connection_without_provider_call(db, user, monkeypatch) -> None:
    fake = FakeClient()
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")
    other = User(username=f"other-{uuid4()}", password_hash="hash", timezone="UTC")
    db.add(other)
    db.flush()
    _connection(db, other)

    result = WithingsSyncService(
        session_factory=lambda: db, client_factory=lambda token: fake
    ).sync(
        user_id=user.id,
        requested_start=date(2026, 9, 20),
        requested_end=date(2026, 9, 20),
    )

    assert result.status == "failed"
    assert result.weight.error_code == "not_connected"
    assert result.activity_energy.error_code == "not_connected"
    assert fake.activity_ranges == []


def test_sync_serializes_disconnect_for_full_operation_lifetime(db, user, monkeypatch) -> None:
    _connection(db, user)
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")
    disconnect_attempt: dict[str, bool] = {}

    class DisconnectingClient(FakeClient):
        def fetch_measure_pages(self, **kwargs):
            try:
                disconnect_withings(db, user)
            except UserOperationBusy:
                disconnect_attempt["busy"] = True
            else:
                disconnect_attempt["busy"] = False
            return super().fetch_measure_pages(**kwargs)

    result = WithingsSyncService(
        session_factory=lambda: db,
        client_factory=lambda token: DisconnectingClient(),
    ).sync(
        user_id=user.id,
        requested_start=date(2026, 9, 20),
        requested_end=date(2026, 9, 20),
    )

    assert disconnect_attempt == {"busy": True}
    assert result.weight.status == "success"

def test_sync_reuses_caller_owned_exclusive_lock_without_reacquiring(
    db, user, monkeypatch
) -> None:
    from app.services.user_operation_lock import exclusive_user_lifecycle_operation

    _connection(db, user)
    monkeypatch.setattr("app.withings.sync.decrypt_token", lambda value: "access-token")
    fake = FakeClient()
    service = WithingsSyncService(
        session_factory=lambda: db,
        client_factory=lambda token: fake,
    )

    with exclusive_user_lifecycle_operation(db, user.id):
        result = service.sync(
            user_id=user.id,
            requested_start=date(2026, 9, 20),
            requested_end=date(2026, 9, 20),
            db=db,
            _operation_locked=True,
        )

    assert result.status == "success"
    assert fake.activity_ranges == [(date(2026, 9, 20), date(2026, 9, 20))]

def test_expired_token_refresh_reuses_caller_owned_exclusive_lock(
    db, user, monkeypatch
) -> None:
    from app.config import settings
    from app.services.credential_crypto import encrypt_credential
    from app.services.user_operation_lock import exclusive_user_lifecycle_operation
    from app.withings.credentials import encrypt_token

    monkeypatch.setattr(settings, "withings_enabled", True)
    connection = _connection(db, user)
    connection.client_id = "synthetic-client"
    connection.encrypted_client_secret = encrypt_credential("synthetic-secret")
    connection.encrypted_access_token = encrypt_token("expired-access")
    connection.encrypted_refresh_token = encrypt_token("old-refresh")
    connection.access_token_expires_at = datetime(2020, 1, 1, tzinfo=UTC)
    db.commit()

    class FakeRefreshAdapter:
        calls = 0

        def refresh(self, *, refresh_token: str, client_id: str, client_secret: str):
            del refresh_token, client_id, client_secret
            self.calls += 1
            return {
                "access_token": "rotated-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
            }

    adapter = FakeRefreshAdapter()
    monkeypatch.setattr("app.withings.token_service._WithingsOAuthAdapter", lambda: adapter)
    fake = FakeClient()
    access_tokens: list[str] = []
    service = WithingsSyncService(
        session_factory=lambda: db,
        client_factory=lambda token: (access_tokens.append(token) or fake),
    )

    with exclusive_user_lifecycle_operation(db, user.id):
        result = service.sync(
            user_id=user.id,
            requested_start=date(2026, 9, 20),
            requested_end=date(2026, 9, 20),
            db=db,
            _operation_locked=True,
        )

    assert result.status == "success"
    assert adapter.calls == 1
    assert access_tokens == ["rotated-access"]
    assert fake.activity_ranges == [(date(2026, 9, 20), date(2026, 9, 20))]
