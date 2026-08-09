import io
import json
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import security_events
from app.auth.security import create_api_token
from app.config import settings
from app.importers.common import CanonicalSample
from app.importers.json_adapter import AdapterResult
from app.models import ImportBatch, User
from app.security_events import security_reference
from app.services import import_service
from app.services.import_service import persist_apple_health_stream, persist_import

REFERENCE_PATTERN = re.compile(r"^[a-f0-9]{16}$")


def _capture_security_events(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[int, str]]:
    records: list[tuple[int, str]] = []
    monkeypatch.setattr(
        security_events.logger,
        "log",
        lambda level, message: records.append((level, message)),
    )
    return records


def _event_payloads(records: list[tuple[int, str]]) -> list[dict[str, object]]:
    return [json.loads(message) for _, message in records]


def _contract_fields(payload: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in payload.items()
        if key not in {"timestamp", "request_id", "client_ref"}
    }


def _assert_pseudonymous_references(payloads: list[dict[str, object]]) -> None:
    for payload in payloads:
        for field in ("actor_ref", "client_ref", "target_ref"):
            value = payload.get(field)
            if value is not None:
                assert isinstance(value, str)
                assert REFERENCE_PATTERN.fullmatch(value)


def _assert_sentinels_absent(
    records: list[tuple[int, str]],
    *sentinels: object,
) -> None:
    serialized = "\n".join(message for _, message in records)
    for sentinel in sentinels:
        assert str(sentinel) not in serialized


def _sample(value: Decimal = Decimal("876543.125")) -> CanonicalSample:
    return CanonicalSample(
        metric_type="dietary_energy_kcal",
        value=value,
        unit="kcal",
        original_value=value,
        original_unit="kcal",
        start_at=datetime(2026, 7, 20, 12, tzinfo=UTC),
        end_at=datetime(2026, 7, 20, 12, tzinfo=UTC),
        timezone="Europe/Berlin",
        source_type="calograph_sync_v1",
        source_name="PRIVATE_RAW_SOURCE_SENTINEL",
        source_identifier="PRIVATE_RAW_IDENTIFIER_SENTINEL",
        external_sample_id="PRIVATE_EXTERNAL_ID_SENTINEL",
    )


def test_xml_import_emits_started_then_completed_with_safe_aggregates(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _capture_security_events(monkeypatch)
    filename = "PRIVATE_FILENAME_SENTINEL.xml"
    source_name = "PRIVATE_IMPORT_EMAIL_SENTINEL@example.invalid"
    health_value = "987654.125"
    xml = (
        '<?xml version="1.0"?><HealthData><Record '
        'type="HKQuantityTypeIdentifierDietaryEnergyConsumed" '
        f'sourceName="{source_name}" unit="kcal" value="{health_value}" '
        'startDate="2026-07-20 12:00:00 +0000" /></HealthData>'
    ).encode()

    summary = persist_apple_health_stream(
        db,
        user,
        io.BytesIO(xml),
        "application/xml",
        filename,
    )

    payloads = _event_payloads(records)
    actor_ref = security_reference("user", user.id)
    target_ref = security_reference("import_batch", summary.batch_id)
    assert [level for level, _ in records] == [logging.INFO, logging.INFO]
    assert [_contract_fields(payload) for payload in payloads] == [
        {
            "event": "import.started",
            "outcome": "pending",
            "actor_ref": actor_ref,
            "target_ref": target_ref,
            "source_type": "apple_health_xml",
        },
        {
            "event": "import.completed",
            "outcome": "success",
            "actor_ref": actor_ref,
            "target_ref": target_ref,
            "source_type": "apple_health_xml",
            "received": 1,
            "inserted": 1,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
        },
    ]
    _assert_pseudonymous_references(payloads)
    _assert_sentinels_absent(
        records,
        filename,
        source_name,
        health_value,
        xml.decode(),
        user.id,
        user.username,
        summary.batch_id,
    )


def test_completed_with_errors_event_contains_only_safe_counts(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _capture_security_events(monkeypatch)
    filename = "PRIVATE_ERROR_FILENAME_SENTINEL.json"
    payload_sentinel = "PRIVATE_RAW_PAYLOAD_SENTINEL"
    exception_sentinel = "PRIVATE_EXCEPTION_TEXT_SENTINEL"
    metric_sentinel = "PRIVATE_METRIC_SENTINEL"
    credential_sentinel = "PRIVATE_TOKEN_CREDENTIAL_SENTINEL"
    raw_payload = (
        f'{{"payload":"{payload_sentinel}","token":"{credential_sentinel}"}}'
    ).encode()
    result = AdapterResult(
        source_type="calograph_sync_v1",
        errors=[(0, metric_sentinel, "invalid_sample", exception_sentinel)],
        received=3,
        unknown_count=1,
    )

    summary = persist_import(
        db,
        user,
        result,
        raw_payload,
        "application/json",
        filename,
    )

    payloads = _event_payloads(records)
    actor_ref = security_reference("user", user.id)
    target_ref = security_reference("import_batch", summary.batch_id)
    assert summary.status == "completed_with_errors"
    assert [level for level, _ in records] == [logging.INFO, logging.INFO]
    assert [_contract_fields(payload) for payload in payloads] == [
        {
            "event": "import.started",
            "outcome": "pending",
            "actor_ref": actor_ref,
            "target_ref": target_ref,
            "source_type": "calograph_sync_v1",
        },
        {
            "event": "import.completed",
            "outcome": "success",
            "actor_ref": actor_ref,
            "target_ref": target_ref,
            "source_type": "calograph_sync_v1",
            "received": 3,
            "inserted": 0,
            "updated": 0,
            "skipped": 1,
            "failed": 1,
        },
    ]
    _assert_pseudonymous_references(payloads)
    _assert_sentinels_absent(
        records,
        filename,
        payload_sentinel,
        exception_sentinel,
        metric_sentinel,
        credential_sentinel,
        raw_payload.decode(),
        user.id,
        user.username,
        summary.batch_id,
    )


def test_partial_failure_emits_one_fixed_reason_without_exception_text(
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = _capture_security_events(monkeypatch)
    monkeypatch.setattr(settings, "import_batch_size", 1)
    original_persist = import_service._persist_sample_batch
    persist_calls = 0
    exception_sentinel = "PRIVATE_DATABASE_EXCEPTION_SENTINEL"

    def fail_second_batch(*args, **kwargs):
        nonlocal persist_calls
        persist_calls += 1
        if persist_calls == 2:
            raise SQLAlchemyError(exception_sentinel)
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(import_service, "_persist_sample_batch", fail_second_batch)
    filename = "PRIVATE_PARTIAL_FILENAME_SENTINEL.xml"
    source_name = "PRIVATE_PARTIAL_SOURCE_SENTINEL"
    xml = (
        '<?xml version="1.0"?><HealthData>'
        '<Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" '
        f'sourceName="{source_name}" unit="kcal" value="765432.125" '
        'startDate="2026-07-20 12:00:00 +0000" />'
        '<Record type="HKQuantityTypeIdentifierDietaryEnergyConsumed" '
        f'sourceName="{source_name}" unit="kcal" value="654321.125" '
        'startDate="2026-07-21 12:00:00 +0000" />'
        "</HealthData>"
    ).encode()

    with pytest.raises(SQLAlchemyError, match=exception_sentinel):
        persist_apple_health_stream(
            db,
            user,
            io.BytesIO(xml),
            "application/xml",
            filename,
        )

    batch = db.scalar(select(ImportBatch))
    assert batch is not None
    payloads = _event_payloads(records)
    actor_ref = security_reference("user", user.id)
    target_ref = security_reference("import_batch", batch.id)
    assert [level for level, _ in records] == [logging.INFO, logging.WARNING]
    assert [_contract_fields(payload) for payload in payloads] == [
        {
            "event": "import.started",
            "outcome": "pending",
            "actor_ref": actor_ref,
            "target_ref": target_ref,
            "source_type": "apple_health_xml",
        },
        {
            "event": "import.partial_failed",
            "outcome": "failure",
            "actor_ref": actor_ref,
            "target_ref": target_ref,
            "reason": "database_error",
            "source_type": "apple_health_xml",
            "received": 2,
            "inserted": 1,
            "updated": 0,
            "skipped": 0,
            "failed": 1,
        },
    ]
    _assert_pseudonymous_references(payloads)
    _assert_sentinels_absent(
        records,
        filename,
        source_name,
        exception_sentinel,
        "765432.125",
        "654321.125",
        xml.decode(),
        user.id,
        user.username,
        batch.id,
    )


def test_validation_endpoint_emits_one_safe_aggregate_event(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_label = "PRIVATE_TOKEN_LABEL_SENTINEL"
    _, raw_token = create_api_token(db, user, token_label)
    records = _capture_security_events(monkeypatch)
    source_sentinel = "PRIVATE_VALIDATION_SOURCE_SENTINEL"
    unknown_metric = "PRIVATE_UNKNOWN_METRIC_SENTINEL"
    payload = {
        "data": {
            "metrics": [
                {
                    "name": "dietary_protein",
                    "units": "g",
                    "data": [
                        {
                            "qty": 765432.125,
                            "date": "2026-07-20 12:00:00 +0000",
                            "source": source_sentinel,
                        }
                    ],
                },
                {
                    "name": unknown_metric,
                    "units": "private-unit",
                    "data": [
                        {"qty": 111.125, "date": "2026-07-20 12:00:00 +0000"},
                        {"qty": 222.125, "date": "2026-07-21 12:00:00 +0000"},
                    ],
                },
            ]
        }
    }

    response = client.post(
        "/api/v1/import/apple-health/validate",
        headers={
            "Authorization": f"Bearer {raw_token}",
            "Content-Type": "application/json",
        },
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "valid"
    payloads = _event_payloads(records)
    assert [level for level, _ in records] == [logging.INFO]
    assert [_contract_fields(event) for event in payloads] == [
        {
            "event": "import.validation_completed",
            "outcome": "success",
            "actor_ref": security_reference("user", user.id),
            "source_type": "health_auto_export_v2",
            "received": 3,
            "inserted": 0,
            "updated": 0,
            "skipped": 2,
            "failed": 0,
        }
    ]
    _assert_pseudonymous_references(payloads)
    _assert_sentinels_absent(
        records,
        token_label,
        raw_token,
        source_sentinel,
        unknown_metric,
        "765432.125",
        "111.125",
        "222.125",
        json.dumps(payload),
        user.id,
        user.username,
    )


def test_http_import_rejections_emit_fixed_events_without_request_data(
    client: TestClient,
    db: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_label = "PRIVATE_REJECTION_TOKEN_LABEL_SENTINEL"
    _, raw_token = create_api_token(db, user, token_label)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200
    csrf_token = login.json()["csrf_token"]
    records = _capture_security_events(monkeypatch)
    client_identifier = "PRIVATE_CLIENT_IDENTIFIER_SENTINEL" + ("x" * 190)
    api_payload = "PRIVATE_REJECTED_API_PAYLOAD_SENTINEL"
    filename = "PRIVATE_REJECTED_FILENAME_SENTINEL.txt"
    file_payload = b"PRIVATE_REJECTED_FILE_PAYLOAD_SENTINEL"

    invalid_identifier = client.post(
        "/api/v1/import/apple-health",
        headers={
            "Authorization": f"Bearer {raw_token}",
            "Content-Type": "application/json",
            "X-Client-Identifier": client_identifier,
        },
        content=api_payload,
    )
    invalid_file_type = client.post(
        "/api/v1/import/apple-health/file",
        headers={"X-CSRF-Token": csrf_token},
        files={"file": (filename, file_payload, "text/plain")},
    )

    assert invalid_identifier.status_code == 422
    assert invalid_file_type.status_code == 415
    payloads = _event_payloads(records)
    assert [level for level, _ in records] == [logging.WARNING, logging.WARNING]
    assert [_contract_fields(payload) for payload in payloads] == [
        {
            "event": "import.rejected",
            "outcome": "failure",
            "reason": "http_422",
            "status_code": 422,
        },
        {
            "event": "import.rejected",
            "outcome": "failure",
            "reason": "http_415",
            "status_code": 415,
        },
    ]
    assert all("client_ref" in payload for payload in payloads)
    _assert_pseudonymous_references(payloads)
    _assert_sentinels_absent(
        records,
        token_label,
        raw_token,
        csrf_token,
        client_identifier,
        api_payload,
        filename,
        file_payload.decode(),
        user.id,
        user.username,
    )
