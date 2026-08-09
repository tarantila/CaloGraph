import io
import json
import logging
import re
import stat
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import security_events
from app.config import settings
from app.main import app
from app.models import HealthSample, ImportBatch, ImportError, User

CONTENT_SENTINEL = "PRIVATE_ENCRYPTED_ZIP_PAYLOAD_SENTINEL"
FILENAME_SENTINEL = "PRIVATE_ENCRYPTED_ZIP_FILENAME_SENTINEL.zip"
METADATA_SENTINEL = b"PRIVATE_ENCRYPTED_ZIP_METADATA_SENTINEL"
PASSWORD_SENTINEL = "correct-horse-battery-staple"
UNSUPPORTED_CONTENT_SENTINEL = "PRIVATE_UNSUPPORTED_COMPRESSION_PAYLOAD_SENTINEL"
UNSUPPORTED_FILENAME_SENTINEL = "PRIVATE_UNSUPPORTED_COMPRESSION_FILENAME_SENTINEL.zip"
UNSUPPORTED_METADATA_SENTINEL = b"PRIVATE_UNSUPPORTED_COMPRESSION_METADATA_SENTINEL"
CRC_CONTENT_SENTINEL = "PRIVATE_CRC_ZIP_PAYLOAD_SENTINEL"
CRC_FILENAME_SENTINEL = "PRIVATE_CRC_ZIP_FILENAME_SENTINEL.zip"
CRC_METADATA_SENTINEL = b"PRIVATE_CRC_ZIP_METADATA_SENTINEL"
REFERENCE_PATTERN = re.compile(r"^[a-f0-9]{16}$")
REQUEST_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
EMPTY_HEALTH_XML = b"<?xml version='1.0'?><HealthData></HealthData>"


def _zip_with_encrypted_entry_flag() -> bytes:
    buffer = io.BytesIO()
    entry = zipfile.ZipInfo("export.xml")
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.comment = METADATA_SENTINEL
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.comment = METADATA_SENTINEL
        archive.writestr(
            entry,
            f"<HealthData><!--{CONTENT_SENTINEL}--></HealthData>".encode(),
        )

    payload = bytearray(buffer.getvalue())
    local_header = payload.index(b"PK\x03\x04")
    central_directory = payload.index(b"PK\x01\x02")
    for flag_offset in (local_header + 6, central_directory + 8):
        flags = int.from_bytes(payload[flag_offset : flag_offset + 2], "little") | 1
        payload[flag_offset : flag_offset + 2] = flags.to_bytes(2, "little")
    return bytes(payload)


def test_encrypted_zip_entry_returns_safe_422_without_writes(
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    other_user = User(
        username="zip-isolation-user",
        password_hash=user.password_hash,
        timezone=user.timezone,
    )
    db.add(other_user)
    db.flush()
    preserved_batch = ImportBatch(
        user_id=other_user.id,
        source_type="isolation_sentinel",
        client_identifier="preserved",
        status="completed",
    )
    db.add(preserved_batch)
    db.commit()
    preserved_batch_id = preserved_batch.id

    archive = _zip_with_encrypted_entry_flag()
    assert zipfile.is_zipfile(io.BytesIO(archive))

    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "password": PASSWORD_SENTINEL},
        )
        assert login.status_code == 200

        records: list[tuple[int, str]] = []
        monkeypatch.setattr(
            security_events.logger,
            "log",
            lambda level, message: records.append((level, message)),
        )
        response = client.post(
            "/api/v1/import/apple-health/file",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
            files={"file": (FILENAME_SENTINEL, archive, "application/zip")},
        )

    body = response.json()
    request_id = body["request_id"]
    assert response.status_code == 422
    assert isinstance(request_id, str)
    assert REQUEST_ID_PATTERN.fullmatch(request_id)
    assert body == {
        "type": "about:blank",
        "title": "Anfrage fehlgeschlagen",
        "status": 422,
        "detail": "Ungültige ZIP-Datei",
        "request_id": request_id,
    }

    assert [level for level, _ in records] == [logging.WARNING]
    event = json.loads(records[0][1])
    assert event["request_id"] == request_id
    assert REFERENCE_PATTERN.fullmatch(str(event["client_ref"]))
    assert {
        key: value
        for key, value in event.items()
        if key not in {"timestamp", "request_id", "client_ref"}
    } == {
        "event": "import.rejected",
        "outcome": "failure",
        "reason": "http_422",
        "status_code": 422,
    }
    assert all(
        json.loads(message)["event"] not in {"request.failed", "import.completed"}
        for _, message in records
    )

    exposed = response.text + caplog.text + "\n".join(message for _, message in records)
    for sentinel in (
        CONTENT_SENTINEL,
        FILENAME_SENTINEL,
        METADATA_SENTINEL.decode(),
        PASSWORD_SENTINEL,
    ):
        assert sentinel not in exposed

    db.expire_all()
    assert db.scalar(
        select(func.count())
        .select_from(ImportBatch)
        .where(ImportBatch.user_id == user.id)
    ) == 0
    assert db.scalar(select(func.count()).select_from(ImportError)) == 0
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 0
    assert db.get(ImportBatch, preserved_batch_id) is not None


def _zip_with_unsupported_compression() -> bytes:
    buffer = io.BytesIO()
    entry = zipfile.ZipInfo("export.xml")
    entry.comment = UNSUPPORTED_METADATA_SENTINEL
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.comment = UNSUPPORTED_METADATA_SENTINEL
        archive.writestr(
            entry,
            f"<HealthData><!--{UNSUPPORTED_CONTENT_SENTINEL}--></HealthData>".encode(),
        )

    payload = bytearray(buffer.getvalue())
    local_header = payload.index(b"PK\x03\x04")
    central_directory = payload.index(b"PK\x01\x02")
    for method_offset in (local_header + 8, central_directory + 10):
        payload[method_offset : method_offset + 2] = (99).to_bytes(2, "little")
    return bytes(payload)


def test_unsupported_zip_compression_returns_safe_422_without_writes(
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    archive = _zip_with_unsupported_compression()
    assert zipfile.is_zipfile(io.BytesIO(archive))

    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "password": PASSWORD_SENTINEL},
        )
        assert login.status_code == 200

        records: list[tuple[int, str]] = []
        monkeypatch.setattr(
            security_events.logger,
            "log",
            lambda level, message: records.append((level, message)),
        )
        response = client.post(
            "/api/v1/import/apple-health/file",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
            files={
                "file": (
                    UNSUPPORTED_FILENAME_SENTINEL,
                    archive,
                    "application/zip",
                )
            },
        )

    body = response.json()
    request_id = body["request_id"]
    assert response.status_code == 422
    assert isinstance(request_id, str)
    assert REQUEST_ID_PATTERN.fullmatch(request_id)
    assert body == {
        "type": "about:blank",
        "title": "Anfrage fehlgeschlagen",
        "status": 422,
        "detail": "Ungültige ZIP-Datei",
        "request_id": request_id,
    }

    assert [level for level, _ in records] == [logging.WARNING]
    event = json.loads(records[0][1])
    assert event["request_id"] == request_id
    assert REFERENCE_PATTERN.fullmatch(str(event["client_ref"]))
    assert {
        key: value
        for key, value in event.items()
        if key not in {"timestamp", "request_id", "client_ref"}
    } == {
        "event": "import.rejected",
        "outcome": "failure",
        "reason": "http_422",
        "status_code": 422,
    }
    assert all(
        json.loads(message)["event"] not in {"request.failed", "import.completed"}
        for _, message in records
    )

    exposed = response.text + caplog.text + "\n".join(message for _, message in records)
    for sentinel in (
        UNSUPPORTED_CONTENT_SENTINEL,
        UNSUPPORTED_FILENAME_SENTINEL,
        UNSUPPORTED_METADATA_SENTINEL.decode(),
        PASSWORD_SENTINEL,
    ):
        assert sentinel not in exposed

    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert db.scalar(select(func.count()).select_from(ImportError)) == 0
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 0


def _zip_with_bad_crc() -> bytes:
    xml = f"<HealthData><!--{CRC_CONTENT_SENTINEL}--></HealthData>".encode()
    buffer = io.BytesIO()
    entry = zipfile.ZipInfo("export.xml")
    entry.comment = CRC_METADATA_SENTINEL
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.comment = CRC_METADATA_SENTINEL
        archive.writestr(entry, xml)

    payload = bytearray(buffer.getvalue())
    local_header = payload.index(b"PK\x03\x04")
    filename_length = int.from_bytes(payload[local_header + 26 : local_header + 28], "little")
    extra_length = int.from_bytes(payload[local_header + 28 : local_header + 30], "little")
    data_offset = local_header + 30 + filename_length + extra_length
    sentinel_offset = xml.index(CRC_CONTENT_SENTINEL.encode())
    payload[data_offset + sentinel_offset] ^= 1
    return bytes(payload)


def test_bad_crc_returns_safe_422_without_partial_persistence(
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    other_user = User(
        username="crc-isolation-user",
        password_hash=user.password_hash,
        timezone=user.timezone,
    )
    db.add(other_user)
    db.flush()
    preserved_batch = ImportBatch(
        user_id=other_user.id,
        source_type="crc_isolation_sentinel",
        client_identifier="preserved",
        status="completed",
    )
    db.add(preserved_batch)
    db.commit()
    preserved_batch_id = preserved_batch.id

    archive = _zip_with_bad_crc()
    assert zipfile.is_zipfile(io.BytesIO(archive))

    with TestClient(app, raise_server_exceptions=False) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "password": PASSWORD_SENTINEL},
        )
        assert login.status_code == 200

        records: list[tuple[int, str]] = []
        monkeypatch.setattr(
            security_events.logger,
            "log",
            lambda level, message: records.append((level, message)),
        )
        response = client.post(
            "/api/v1/import/apple-health/file",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
            files={"file": (CRC_FILENAME_SENTINEL, archive, "application/zip")},
        )

    body = response.json()
    request_id = body["request_id"]
    assert response.status_code == 422
    assert isinstance(request_id, str)
    assert REQUEST_ID_PATTERN.fullmatch(request_id)
    assert body == {
        "type": "about:blank",
        "title": "Anfrage fehlgeschlagen",
        "status": 422,
        "detail": "Ungültige ZIP-Datei",
        "request_id": request_id,
    }

    assert [level for level, _ in records] == [logging.WARNING]
    event = json.loads(records[0][1])
    assert event["request_id"] == request_id
    assert REFERENCE_PATTERN.fullmatch(str(event["client_ref"]))
    assert {
        key: value
        for key, value in event.items()
        if key not in {"timestamp", "request_id", "client_ref"}
    } == {
        "event": "import.rejected",
        "outcome": "failure",
        "reason": "http_422",
        "status_code": 422,
    }
    assert all(
        json.loads(message)["event"]
        not in {
            "request.failed",
            "import.started",
            "import.partial_failed",
            "import.completed",
        }
        for _, message in records
    )

    exposed = response.text + caplog.text + "\n".join(message for _, message in records)
    for sentinel in (
        CRC_CONTENT_SENTINEL,
        CRC_FILENAME_SENTINEL,
        CRC_METADATA_SENTINEL.decode(),
        PASSWORD_SENTINEL,
    ):
        assert sentinel not in exposed

    db.expire_all()
    assert db.scalar(
        select(func.count())
        .select_from(ImportBatch)
        .where(ImportBatch.user_id == user.id)
    ) == 0
    assert db.scalar(select(func.count()).select_from(ImportError)) == 0
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 0
    assert db.get(ImportBatch, preserved_batch_id) is not None


def _zip_bytes(
    entries: list[tuple[str | zipfile.ZipInfo, bytes]],
    *,
    compression: int = zipfile.ZIP_STORED,
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for entry, payload in entries:
            archive.writestr(entry, payload)
    return buffer.getvalue()


def _login_headers(client: TestClient, user: User) -> dict[str, str]:
    login = client.post(
        "/api/v1/auth/login",
        json={"username": user.username, "password": PASSWORD_SENTINEL},
    )
    assert login.status_code == 200
    return {"X-CSRF-Token": login.json()["csrf_token"]}


def _upload_zip(
    client: TestClient,
    headers: dict[str, str],
    archive: bytes,
    *,
    filename: str = "trust-boundary.zip",
):
    return client.post(
        "/api/v1/import/apple-health/file",
        headers=headers,
        files={"file": (filename, archive, "application/zip")},
    )


@pytest.mark.parametrize(
    "entry_name",
    ["../export.xml", "nested/../../export.xml", "/export.xml"],
)
def test_unsafe_zip_paths_are_rejected_without_writes(
    client: TestClient,
    user: User,
    db: Session,
    entry_name: str,
) -> None:
    archive = _zip_bytes([(entry_name, EMPTY_HEALTH_XML)])

    response = _upload_zip(client, _login_headers(client, user), archive)

    assert response.status_code == 422
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert db.scalar(select(func.count()).select_from(ImportError)) == 0
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 0


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [("notes.txt", b"not an export")],
        [
            ("export.xml", EMPTY_HEALTH_XML),
            ("nested/export.xml", EMPTY_HEALTH_XML),
        ],
        [
            ("export.xml", EMPTY_HEALTH_XML),
            ("export.xml", EMPTY_HEALTH_XML),
        ],
    ],
)
@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_zip_requires_exactly_one_export_candidate(
    client: TestClient,
    user: User,
    db: Session,
    entries: list[tuple[str, bytes]],
) -> None:
    response = _upload_zip(
        client,
        _login_headers(client, user),
        _zip_bytes(entries),
    )

    assert response.status_code == 422
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


@pytest.mark.parametrize("entry_name", ["nested/export.xml", "nested/EXPORT.XML"])
def test_nested_case_insensitive_export_candidate_is_imported(
    client: TestClient,
    user: User,
    db: Session,
    entry_name: str,
) -> None:
    response = _upload_zip(
        client,
        _login_headers(client, user),
        _zip_bytes([(entry_name, EMPTY_HEALTH_XML)]),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_supported_zip_compression_is_imported(
    client: TestClient,
    user: User,
    db: Session,
    compression: int,
) -> None:
    response = _upload_zip(
        client,
        _login_headers(client, user),
        _zip_bytes([("export.xml", EMPTY_HEALTH_XML)], compression=compression),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1


@pytest.mark.parametrize(
    ("entry_count", "expected_status"),
    [(1, 200), (2, 200), (3, 413)],
)
def test_zip_entry_count_boundary(
    client: TestClient,
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    entry_count: int,
    expected_status: int,
) -> None:
    monkeypatch.setattr(settings, "max_zip_entries", 2)
    entries = [("export.xml", EMPTY_HEALTH_XML)]
    entries.extend((f"extra-{index}.txt", b"x") for index in range(entry_count - 1))

    response = _upload_zip(
        client,
        _login_headers(client, user),
        _zip_bytes(entries),
    )

    assert response.status_code == expected_status
    expected_batches = 1 if expected_status == 200 else 0
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == expected_batches


def test_zip_upload_size_boundary(
    client: TestClient,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _zip_bytes([("export.xml", EMPTY_HEALTH_XML)])
    headers = _login_headers(client, user)
    monkeypatch.setattr(settings, "file_import_user_rate_limit", 10)
    monkeypatch.setattr(settings, "file_import_ip_rate_limit", 10)

    monkeypatch.setattr(settings, "max_upload_bytes", len(archive))
    accepted = _upload_zip(client, headers, archive)
    monkeypatch.setattr(settings, "max_upload_bytes", len(archive) - 1)
    rejected = _upload_zip(client, headers, archive)

    assert accepted.status_code == 200
    assert rejected.status_code == 413


@pytest.mark.parametrize(
    ("limit_delta", "expected_status"),
    [(1, 200), (0, 200), (-1, 413)],
)
def test_zip_expanded_size_boundary(
    client: TestClient,
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    limit_delta: int,
    expected_status: int,
) -> None:
    archive = _zip_bytes([("export.xml", EMPTY_HEALTH_XML)])
    monkeypatch.setattr(
        settings,
        "max_zip_uncompressed_bytes",
        len(EMPTY_HEALTH_XML) + limit_delta,
    )

    response = _upload_zip(client, _login_headers(client, user), archive)

    assert response.status_code == expected_status
    expected_batches = 1 if expected_status == 200 else 0
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == expected_batches


def _zip_with_reported_ratio(ratio: int) -> bytes:
    payload = bytearray(
        _zip_bytes(
            [("export.xml", EMPTY_HEALTH_XML)],
            compression=zipfile.ZIP_DEFLATED,
        )
    )
    local_header = payload.index(b"PK\x03\x04")
    central_directory = payload.index(b"PK\x01\x02")
    compressed_size = int.from_bytes(
        payload[central_directory + 20 : central_directory + 24],
        "little",
    )
    reported_size = compressed_size * ratio
    payload[local_header + 22 : local_header + 26] = reported_size.to_bytes(4, "little")
    payload[central_directory + 24 : central_directory + 28] = reported_size.to_bytes(
        4,
        "little",
    )
    return bytes(payload)


@pytest.mark.parametrize(
    ("reported_ratio", "expected_status"),
    [(199, 422), (200, 422), (201, 413)],
)
def test_zip_compression_ratio_boundary(
    client: TestClient,
    user: User,
    db: Session,
    reported_ratio: int,
    expected_status: int,
) -> None:
    response = _upload_zip(
        client,
        _login_headers(client, user),
        _zip_with_reported_ratio(reported_ratio),
    )

    assert response.status_code == expected_status
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


def _zip_with_diluted_candidate_ratio(candidate_ratio: int) -> bytes:
    export = zipfile.ZipInfo("export.xml")
    export.compress_type = zipfile.ZIP_DEFLATED
    padding = zipfile.ZipInfo("padding.bin")
    padding.compress_type = zipfile.ZIP_STORED
    payload = bytearray(
        _zip_bytes(
            [
                (export, EMPTY_HEALTH_XML),
                (padding, b"x" * 512),
            ]
        )
    )
    local_header = payload.index(b"PK\x03\x04")
    central_directory = payload.index(b"PK\x01\x02")
    compressed_size = int.from_bytes(
        payload[central_directory + 20 : central_directory + 24],
        "little",
    )
    reported_size = compressed_size * candidate_ratio
    payload[local_header + 22 : local_header + 26] = reported_size.to_bytes(4, "little")
    payload[central_directory + 24 : central_directory + 28] = reported_size.to_bytes(
        4,
        "little",
    )
    return bytes(payload)


def test_per_entry_ratio_cannot_be_diluted_by_padding(
    client: TestClient,
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _zip_with_diluted_candidate_ratio(201)
    with zipfile.ZipFile(io.BytesIO(archive)) as parsed:
        entries = parsed.infolist()
        export = entries[0]
        assert export.file_size / export.compress_size > 200
        assert sum(item.file_size for item in entries) / sum(
            item.compress_size for item in entries
        ) <= 200

    def forbidden_open(*args, **kwargs):
        del args, kwargs
        raise AssertionError("Rejected ZIP entry must not be decompressed")

    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden_open)
    response = _upload_zip(client, _login_headers(client, user), archive)

    assert response.status_code == 413
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


def _zip_with_unsupported_multidisk_metadata() -> bytes:
    payload = bytearray(_zip_bytes([("export.xml", EMPTY_HEALTH_XML)]))
    end_record = payload.index(b"PK\x05\x06")
    payload[end_record + 4 : end_record + 6] = (1).to_bytes(2, "little")
    return bytes(payload)


def test_unsupported_multidisk_metadata_returns_safe_422_without_writes(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    archive = _zip_with_unsupported_multidisk_metadata()

    response = _upload_zip(client, _login_headers(client, user), archive)

    assert response.status_code == 422
    assert response.json()["detail"] == "Ungültige ZIP-Datei"
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


def _zip_with_mismatched_local_filename() -> bytes:
    payload = bytearray(_zip_bytes([("export.xml", EMPTY_HEALTH_XML)]))
    local_header = payload.index(b"PK\x03\x04")
    filename_offset = local_header + 30
    payload[filename_offset : filename_offset + len("export.xml")] = b"ixport.xml"
    return bytes(payload)


def test_mismatched_local_filename_returns_safe_422_without_writes(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    response = _upload_zip(
        client,
        _login_headers(client, user),
        _zip_with_mismatched_local_filename(),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Ungültige ZIP-Datei"
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0


def _zip_with_corrupt_compressed_data(compression: int) -> bytes:
    xml = b"<HealthData><!--" + (b"A" * 4096) + b"--></HealthData>"
    payload = bytearray(
        _zip_bytes(
            [("export.xml", xml)],
            compression=compression,
        )
    )
    local_header = payload.index(b"PK\x03\x04")
    filename_length = int.from_bytes(payload[local_header + 26 : local_header + 28], "little")
    extra_length = int.from_bytes(payload[local_header + 28 : local_header + 30], "little")
    compressed_size = int.from_bytes(payload[local_header + 18 : local_header + 22], "little")
    data_offset = local_header + 30 + filename_length + extra_length
    payload[data_offset + compressed_size // 2] ^= 0xFF
    return bytes(payload)


@pytest.mark.parametrize(
    "compression",
    [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA, zipfile.ZIP_ZSTANDARD],
)
def test_disallowed_zip_compression_returns_safe_422_without_writes(
    client: TestClient,
    user: User,
    db: Session,
    compression: int,
) -> None:
    response = _upload_zip(
        client,
        _login_headers(client, user),
        _zip_bytes([("export.xml", EMPTY_HEALTH_XML)], compression=compression),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Ungültige ZIP-Datei"
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert db.scalar(select(func.count()).select_from(ImportError)) == 0
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 0


def test_corrupt_deflate_stream_returns_safe_422_without_writes(
    client: TestClient,
    user: User,
    db: Session,
) -> None:
    response = _upload_zip(
        client,
        _login_headers(client, user),
        _zip_with_corrupt_compressed_data(zipfile.ZIP_DEFLATED),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Ungültige ZIP-Datei"
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
    assert db.scalar(select(func.count()).select_from(ImportError)) == 0
    assert db.scalar(select(func.count()).select_from(HealthSample)) == 0


@pytest.mark.parametrize(
    "mode",
    [stat.S_IFLNK | 0o777, stat.S_IFCHR | 0o600],
)
def test_unix_special_entry_metadata_is_treated_as_stream_data(
    client: TestClient,
    user: User,
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
) -> None:
    entry = zipfile.ZipInfo("export.xml")
    entry.create_system = 3
    entry.external_attr = mode << 16
    archive = _zip_bytes([(entry, EMPTY_HEALTH_XML)])

    def forbidden_extract(*args, **kwargs):
        del args, kwargs
        raise AssertionError("ZIP extraction must not be used")

    monkeypatch.setattr(zipfile.ZipFile, "extract", forbidden_extract)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", forbidden_extract)

    response = _upload_zip(client, _login_headers(client, user), archive)

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert db.scalar(select(func.count()).select_from(ImportBatch)) == 1
