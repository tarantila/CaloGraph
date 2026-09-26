from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.activity import ACTIVE_ENERGY_METRIC, ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS
from app.config import settings
from app.models import HealthSample, ImportBatch, User, WithingsConnection
from app.provider_preferences import (
    ACTIVITY_ENERGY_DATA_AREA,
    NUTRITION_DATA_AREA,
    SUPPORTED_PROVIDER_KEYS,
    WEIGHT_DATA_AREA,
)
from app.services.credential_crypto import encrypt_credential
from app.services.provider_preferences import provider_availability
from app.weight import WEIGHT_METRIC, WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS
from app.withings.constants import WITHINGS_REQUIRED_SCOPES
from app.withings.service import delete_withings_credentials, disconnect_withings

PASSWORD = "correct-horse-battery-staple"


def _configured(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True) -> None:
    monkeypatch.setattr(settings, "withings_enabled", enabled)


def _connection(
    db: Session,
    user: User,
    *,
    state: str = "active",
    scopes: list[str] | None = None,
    client_id: str | None = "synthetic-client-id",
    encrypted_client_secret: bytes | None = None,
) -> WithingsConnection:
    connection = WithingsConnection(
        user_id=user.id,
        client_id=client_id,
        encrypted_client_secret=encrypted_client_secret or encrypt_credential("synthetic-secret"),
        withings_user_id="synthetic-user",
        encrypted_access_token=encrypt_credential("synthetic-access-token"),
        encrypted_refresh_token=encrypt_credential("synthetic-refresh-token"),
        granted_scopes=list(WITHINGS_REQUIRED_SCOPES) if scopes is None else scopes,
        state=state,
    )
    db.add(connection)
    db.flush()
    return connection


def _sample(
    db: Session,
    user: User,
    *,
    metric: str,
    source_type: str,
    source_identifier: str,
    value: str,
) -> HealthSample:
    timestamp = datetime(2026, 1, 2, 12, tzinfo=UTC)
    batch = ImportBatch(user_id=user.id, source_type=source_type, status="completed")
    db.add(batch)
    db.flush()
    sample = HealthSample(
        user_id=user.id,
        import_batch_id=batch.id,
        external_sample_id=f"sample-{user.id}-{metric}-{source_type}-{value}",
        fingerprint=f"fingerprint-{user.id}-{metric}-{source_type}-{value}",
        source_type=source_type,
        source_name="Withings",
        source_identifier=source_identifier,
        metric_type=metric,
        value=Decimal(value),
        unit="kg" if metric == WEIGHT_METRIC else "kcal",
        original_value=Decimal(value),
        original_unit="native",
        start_at=timestamp,
        end_at=timestamp,
        local_date=date(2026, 1, 2),
        timezone="UTC",
    )
    db.add(sample)
    db.flush()
    return sample


def _withings_status(db: Session, user: User, area: str):
    return next(
        item
        for item in provider_availability(db, user_id=user.id, data_area=area)
        if item.provider_key == "withings"
    )


def test_withings_is_registered_for_scalar_areas_but_never_nutrition() -> None:
    assert "withings" in SUPPORTED_PROVIDER_KEYS[WEIGHT_DATA_AREA]
    assert "withings" in SUPPORTED_PROVIDER_KEYS[ACTIVITY_ENERGY_DATA_AREA]
    assert "withings" not in SUPPORTED_PROVIDER_KEYS[NUTRITION_DATA_AREA]
    assert WEIGHT_PROVIDER_SOURCE_TYPE_GROUPS["withings"] == ("withings_measure_v1",)
    assert ACTIVITY_PROVIDER_SOURCE_TYPE_GROUPS["withings"] == ("withings_activity_v2",)


def test_withings_weight_availability_requires_owned_positive_evidence(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured(monkeypatch)
    assert _withings_status(db, user, WEIGHT_DATA_AREA).status == "not_configured"

    connection = _connection(db, user)
    _sample(
        db,
        user,
        metric=WEIGHT_METRIC,
        source_type="withings_measure_v1",
        source_identifier=str(connection.id),
        value="0",
    )
    assert _withings_status(db, user, WEIGHT_DATA_AREA).status == "no_data"

    _sample(
        db,
        user,
        metric=WEIGHT_METRIC,
        source_type="withings_measure_v1",
        source_identifier=str(connection.id),
        value="81.2",
    )
    assert _withings_status(db, user, WEIGHT_DATA_AREA).status == "available"


def test_withings_activity_accepts_owned_zero_and_isolates_other_users(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured(monkeypatch)
    connection = _connection(db, user)
    other = User(username="withings-activity-other", password_hash="synthetic")
    db.add(other)
    db.flush()
    _sample(
        db,
        other,
        metric=ACTIVE_ENERGY_METRIC,
        source_type="withings_activity_v2",
        source_identifier=str(connection.id),
        value="0",
    )
    assert _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA).status == "no_data"

    _sample(
        db,
        user,
        metric=ACTIVE_ENERGY_METRIC,
        source_type="withings_activity_v2",
        source_identifier=str(connection.id),
        value="0",
    )
    assert _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA).status == "available"


def test_withings_credentials_are_scoped_to_requested_user(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured(monkeypatch)
    other = User(username="withings-credentials-other", password_hash="synthetic")
    db.add(other)
    db.flush()
    _connection(db, other)

    assert _withings_status(db, user, WEIGHT_DATA_AREA).status == "not_configured"
    assert _withings_status(db, other, WEIGHT_DATA_AREA).status == "no_data"


@pytest.mark.parametrize("value", ["0", "321.5"])
def test_activity_error_does_not_hide_persisted_withings_evidence(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _configured(monkeypatch)
    connection = _connection(db, user)
    _sample(
        db,
        user,
        metric=ACTIVE_ENERGY_METRIC,
        source_type="withings_activity_v2",
        source_identifier=str(connection.id),
        value=value,
    )
    connection.last_activity_error_category = "provider_error"

    assert _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA).status == "available"


def test_activity_error_is_reported_only_when_owned_connection_has_no_evidence(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured(monkeypatch)
    connection = _connection(db, user)
    connection.last_activity_error_category = "provider_error"

    status = _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA)
    assert status.status == "error"
    assert status.available is False


def test_activity_error_status_is_serialized_by_availability_api(
    client: TestClient, db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured(monkeypatch)
    connection = _connection(db, user)
    connection.last_activity_error_category = "invalid_response"
    db.commit()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": PASSWORD},
    )
    assert login.status_code == 200

    response = client.get("/api/v1/settings/provider-availability/activity_energy")

    assert response.status_code == 200
    withings = next(
        item for item in response.json()["providers"] if item["provider_key"] == "withings"
    )
    assert withings == {"provider_key": "withings", "available": False, "status": "error"}


@pytest.mark.parametrize(
    ("state", "scopes", "missing_access_token", "missing_refresh_token", "expected"),
    [
        ("reauth_required", None, False, False, "reauth_required"),
        ("active", ["user.metrics"], False, False, "reauth_required"),
        ("active", ["user.activity"], False, False, "reauth_required"),
        ("active", None, True, False, "reauth_required"),
        ("active", None, False, True, "reauth_required"),
        ("not_connected", None, False, False, "not_configured"),
    ],
)
def test_withings_connection_state_and_scopes_fail_closed(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    scopes: list[str] | None,
    missing_access_token: bool,
    missing_refresh_token: bool,
    expected: str,
) -> None:
    _configured(monkeypatch)
    connection = _connection(db, user, state=state, scopes=scopes)
    if missing_access_token:
        connection.encrypted_access_token = None
    if missing_refresh_token:
        connection.encrypted_refresh_token = None
    _sample(
        db,
        user,
        metric=ACTIVE_ENERGY_METRIC,
        source_type="withings_activity_v2",
        source_identifier=str(connection.id),
        value="100",
    )

    assert _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA).status == expected


@pytest.mark.parametrize(
    ("token_field", "ciphertext"),
    [
        ("encrypted_access_token", b"invalid-access-token-sentinel"),
        ("encrypted_refresh_token", b"invalid-refresh-token-sentinel"),
    ],
)
def test_corrupt_withings_tokens_fail_closed_without_ciphertext_logging(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    token_field: str,
    ciphertext: bytes,
) -> None:
    _configured(monkeypatch)
    connection = _connection(db, user)
    _sample(
        db,
        user,
        metric=ACTIVE_ENERGY_METRIC,
        source_type="withings_activity_v2",
        source_identifier=str(connection.id),
        value="100",
    )
    setattr(connection, token_field, ciphertext)
    db.flush()

    status = _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA)

    assert status.status == "reauth_required"
    assert ciphertext.decode() not in caplog.text


@pytest.mark.parametrize("credential_field", ["client_id", "encrypted_client_secret"])
def test_missing_withings_credentials_fail_closed_with_evidence(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    credential_field: str,
) -> None:
    _configured(monkeypatch)
    connection = _connection(db, user)
    _sample(
        db,
        user,
        metric=ACTIVE_ENERGY_METRIC,
        source_type="withings_activity_v2",
        source_identifier=str(connection.id),
        value="100",
    )
    setattr(connection, credential_field, None)

    assert _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA).status == "not_configured"


def test_disabled_withings_fails_closed_despite_persisted_evidence(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = _connection(db, user)
    _sample(
        db,
        user,
        metric=ACTIVE_ENERGY_METRIC,
        source_type="withings_activity_v2",
        source_identifier=str(connection.id),
        value="100",
    )
    _configured(monkeypatch, enabled=False)

    assert _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA).status == "disabled"


@pytest.mark.parametrize("disconnect", [False, True])
def test_withings_disconnect_keeps_sample_but_makes_it_unavailable(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch, disconnect: bool
) -> None:
    _configured(monkeypatch)
    connection = _connection(db, user)
    sample = _sample(
        db,
        user,
        metric=ACTIVE_ENERGY_METRIC,
        source_type="withings_activity_v2",
        source_identifier=str(connection.id),
        value="100",
    )
    db.flush()

    if disconnect:
        disconnect_withings(db, user, lock=False)
    else:
        delete_withings_credentials(db, user, lock=False)

    assert db.get(HealthSample, sample.id) is not None
    assert _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA).status == "not_configured"


def test_corrupt_withings_secret_fails_closed_without_secret_logging(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _configured(monkeypatch)
    connection = _connection(db, user)
    sample = _sample(
        db,
        user,
        metric=ACTIVE_ENERGY_METRIC,
        source_type="withings_activity_v2",
        source_identifier=str(connection.id),
        value="100",
    )
    connection.encrypted_client_secret = b"invalid-ciphertext"
    db.flush()

    assert _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA).status == "not_configured"
    assert db.get(HealthSample, sample.id) is not None
    assert "invalid-ciphertext" not in caplog.text


def test_withings_activity_wrong_connection_identifier_is_not_evidence(
    db: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    _configured(monkeypatch)
    _connection(db, user)
    _sample(
        db,
        user,
        metric=ACTIVE_ENERGY_METRIC,
        source_type="withings_activity_v2",
        source_identifier="another-connection",
        value="100",
    )

    assert _withings_status(db, user, ACTIVITY_ENERGY_DATA_AREA).status == "no_data"
