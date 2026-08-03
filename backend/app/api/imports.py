import json
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import PurePosixPath
from typing import IO, Never
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import current_user, import_token, require_csrf
from app.config import settings
from app.database import get_db
from app.importers.errors import ImportFormatError
from app.importers.json_adapter import AdapterResult, parse_json_payload
from app.importers.yazio import parse_yazio_export
from app.models import ApiToken, ImportBatch, ImportError, User
from app.schemas import (
    ImportBatchDetailResponse,
    ImportBatchResponse,
    ImportErrorResponse,
    ImportSummary,
)
from app.security_events import log_security_event, security_reference
from app.services.import_guard import ImportAlreadyRunning, import_slot
from app.services.import_service import persist_apple_health_stream, persist_import
from app.services.rate_limit import check_rate_limit, normalize_client_ip

router = APIRouter(tags=["Import"])


async def _json_body(request: Request) -> tuple[bytes, dict[str, object]]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.max_json_payload_bytes:
                raise HTTPException(status_code=413, detail="JSON-Payload ist zu groß")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length ist ungültig") from exc
    buffer = bytearray()
    async for chunk in request.stream():
        buffer.extend(chunk)
        if len(buffer) > settings.max_json_payload_bytes:
            raise HTTPException(status_code=413, detail="JSON-Payload ist zu groß")
    raw = bytes(buffer)
    try:
        payload = json.loads(raw, parse_float=Decimal)
    except (UnicodeDecodeError, RecursionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Ungültiges JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="JSON-Wurzel muss ein Objekt sein")
    return raw, payload


def _validated_client_identifier(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) > 190 or "\x00" in value:
        raise HTTPException(status_code=422, detail="X-Client-Identifier ist ungültig")
    return value


def _raise_invalid_import(exc: ValueError) -> Never:
    detail = (
        str(exc)
        if isinstance(exc, ImportFormatError)
        else "Importdaten enthalten ungültige Felder"
    )
    raise HTTPException(status_code=422, detail=detail) from exc


def _rate_limit_api_import(
    db: Session,
    request: Request,
    token: ApiToken,
) -> None:
    client = normalize_client_ip(request.client.host if request.client else None)
    check_rate_limit(
        db,
        "import-ip",
        f"ip:{client}",
        settings.import_ip_rate_limit,
        settings.import_rate_limit_window_seconds,
    )
    check_rate_limit(
        db,
        "import-token",
        f"token:{token.id}",
        settings.import_rate_limit,
        settings.import_rate_limit_window_seconds,
    )


def _rate_limit_file_import(
    db: Session,
    request: Request,
    user: User,
) -> None:
    client = normalize_client_ip(request.client.host if request.client else None)
    check_rate_limit(
        db,
        "file-import-ip",
        f"ip:{client}",
        settings.file_import_ip_rate_limit,
        settings.file_import_rate_limit_window_seconds,
    )
    check_rate_limit(
        db,
        "file-import-user",
        f"user:{user.id}",
        settings.file_import_user_rate_limit,
        settings.file_import_rate_limit_window_seconds,
    )


@contextmanager
def _user_import_slot(user: User) -> Iterator[None]:
    try:
        with import_slot(user.id):
            yield
    except ImportAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail="Für dieses Konto läuft bereits ein Import oder eine Validierung.",
        ) from exc


def _validate_result(result: AdapterResult, user: User) -> ImportSummary:
    summary = ImportSummary(
        status="valid" if not result.failed_count else "valid_with_errors",
        received=result.received,
        skipped=result.unknown_count,
        failed=result.failed_count,
        unknown_types=sorted(result.unknown_types),
    )
    log_security_event(
        "import.validation_completed",
        actor_ref=security_reference("user", user.id),
        details={
            "source_type": result.source_type,
            "received": summary.received,
            "inserted": summary.inserted,
            "updated": summary.updated,
            "skipped": summary.skipped,
            "failed": summary.failed,
        },
    )
    return summary


@router.post("/import/apple-health", response_model=ImportSummary)
async def import_json(
    request: Request,
    client_identifier: str | None = Header(default=None, alias="X-Client-Identifier"),
    identity: tuple[User, ApiToken] = Depends(import_token),
    db: Session = Depends(get_db),
) -> ImportSummary:
    user, token = identity
    _rate_limit_api_import(db, request, token)
    validated_client_identifier = _validated_client_identifier(client_identifier)
    with _user_import_slot(user):
        raw, payload = await _json_body(request)
        try:
            result = await run_in_threadpool(parse_json_payload, payload, user.timezone)
        except ValueError as exc:
            _raise_invalid_import(exc)
        return await run_in_threadpool(
            persist_import,
            db,
            user,
            result,
            raw,
            "application/json",
            validated_client_identifier or token.label,
        )


@router.post("/import/apple-health/validate", response_model=ImportSummary)
async def validate_json(
    request: Request,
    identity: tuple[User, ApiToken] = Depends(import_token),
    db: Session = Depends(get_db),
) -> ImportSummary:
    user, token = identity
    _rate_limit_api_import(db, request, token)
    with _user_import_slot(user):
        _, payload = await _json_body(request)
        try:
            result = await run_in_threadpool(parse_json_payload, payload, user.timezone)
        except ValueError as exc:
            _raise_invalid_import(exc)
        return _validate_result(result, user)


@router.post("/import/yazio", response_model=ImportSummary)
async def import_yazio_json(
    request: Request,
    client_identifier: str | None = Header(default=None, alias="X-Client-Identifier"),
    identity: tuple[User, ApiToken] = Depends(import_token),
    db: Session = Depends(get_db),
) -> ImportSummary:
    user, token = identity
    _rate_limit_api_import(db, request, token)
    validated_client_identifier = _validated_client_identifier(client_identifier)
    with _user_import_slot(user):
        _, payload = await _json_body(request)
        source_identifier = validated_client_identifier or "yazio-account"
        try:
            result = await run_in_threadpool(
                parse_yazio_export,
                payload,
                user.timezone,
                source_identifier,
            )
        except ValueError as exc:
            _raise_invalid_import(exc)
        return await run_in_threadpool(
            persist_import,
            db,
            user,
            result,
            None,
            "application/json",
            validated_client_identifier or token.label,
        )


@router.post("/import/yazio/validate", response_model=ImportSummary)
async def validate_yazio_json(
    request: Request,
    identity: tuple[User, ApiToken] = Depends(import_token),
    db: Session = Depends(get_db),
) -> ImportSummary:
    user, token = identity
    _rate_limit_api_import(db, request, token)
    with _user_import_slot(user):
        _, payload = await _json_body(request)
        try:
            result = await run_in_threadpool(
                parse_yazio_export,
                payload,
                user.timezone,
            )
        except ValueError as exc:
            _raise_invalid_import(exc)
        return _validate_result(result, user)


@router.post("/import/apple-health/file", response_model=ImportSummary)
def import_file(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ImportSummary:
    filename = (file.filename or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    filename = filename[-190:]
    lowered_filename = filename.lower()
    if not (lowered_filename.endswith(".xml") or lowered_filename.endswith(".zip")):
        raise HTTPException(status_code=415, detail="Nur Apple-Health-XML oder ZIP ist erlaubt")
    if _upload_size(file) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Upload ist zu groß")
    _rate_limit_file_import(db, request, user)
    with _user_import_slot(user):
        if lowered_filename.endswith(".zip"):
            return _import_zip(
                file.file,
                user,
                db,
                file.content_type or "application/zip",
                filename,
            )
        file.file.seek(0)
        return persist_apple_health_stream(
            db,
            user,
            file.file,
            file.content_type or "application/xml",
            filename,
        )


@router.post("/import/yazio/file", response_model=ImportSummary)
def import_yazio_file(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ImportSummary:
    filename = (file.filename or "").replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    filename = filename[-190:]
    if not filename.lower().endswith(".json"):
        raise HTTPException(
            status_code=415,
            detail="Nur eine YAZIO-days.json oder -nutrients.json ist erlaubt",
        )

    if _upload_size(file) > settings.max_json_payload_bytes:
        raise HTTPException(status_code=413, detail="JSON-Datei ist zu groß")
    _rate_limit_file_import(db, request, user)
    file.file.seek(0)
    raw = file.file.read(settings.max_json_payload_bytes + 1)
    if len(raw) > settings.max_json_payload_bytes:
        raise HTTPException(status_code=413, detail="JSON-Datei ist zu groß")
    try:
        payload = json.loads(raw, parse_float=Decimal)
    except (UnicodeDecodeError, RecursionError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Ungültiges JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="JSON-Wurzel muss ein Objekt sein")
    with _user_import_slot(user):
        try:
            result = parse_yazio_export(payload, user.timezone)
        except ValueError as exc:
            _raise_invalid_import(exc)
        return persist_import(
            db,
            user,
            result,
            None,
            file.content_type or "application/json",
            filename,
        )


def _upload_size(file: UploadFile) -> int:
    if file.size is not None:
        return file.size
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    return size


def _import_zip(
    buffer: IO[bytes],
    user: User,
    db: Session,
    content_type: str,
    client_identifier: str,
) -> ImportSummary:
    if not zipfile.is_zipfile(buffer):
        raise HTTPException(status_code=422, detail="Ungültige ZIP-Datei")
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        entries = archive.infolist()
        if len(entries) > settings.max_zip_entries:
            raise HTTPException(status_code=413, detail="ZIP enthält zu viele Dateien")
        total_uncompressed = sum(item.file_size for item in entries)
        total_compressed = max(1, sum(item.compress_size for item in entries))
        if (
            total_uncompressed > settings.max_zip_uncompressed_bytes
            or total_uncompressed / total_compressed > 200
        ):
            raise HTTPException(status_code=413, detail="ZIP überschreitet sichere Entpackgrenzen")
        candidates = []
        for item in entries:
            path = PurePosixPath(item.filename)
            if path.is_absolute() or ".." in path.parts:
                raise HTTPException(status_code=422, detail="Unsicherer Pfad in ZIP-Datei")
            if path.name.lower() == "export.xml":
                candidates.append(item)
        if len(candidates) != 1:
            raise HTTPException(status_code=422, detail="ZIP muss genau eine export.xml enthalten")
        with archive.open(candidates[0], "r") as xml_stream:
            return persist_apple_health_stream(
                db,
                user,
                xml_stream,
                content_type,
                client_identifier,
            )


@router.get("/imports", response_model=list[ImportBatchResponse])
def list_imports(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[ImportBatch]:
    return list(
        db.scalars(
            select(ImportBatch)
            .where(ImportBatch.user_id == user.id)
            .order_by(ImportBatch.started_at.desc())
            .limit(100)
        )
    )


@router.get("/imports/{batch_id}", response_model=ImportBatchDetailResponse)
def import_detail(
    batch_id: UUID, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> ImportBatchDetailResponse:
    batch = db.scalar(
        select(ImportBatch).where(ImportBatch.id == batch_id, ImportBatch.user_id == user.id)
    )
    if not batch:
        raise HTTPException(status_code=404, detail="Importlauf nicht gefunden")
    errors = list(
        db.scalars(
            select(ImportError)
            .where(ImportError.batch_id == batch.id)
            .order_by(ImportError.item_index, ImportError.id)
            .limit(100)
        )
    )
    return ImportBatchDetailResponse(
        **ImportBatchResponse.model_validate(batch).model_dump(),
        errors=[ImportErrorResponse.model_validate(item) for item in errors],
    )
