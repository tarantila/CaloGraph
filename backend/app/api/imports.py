import json
import tempfile
import zipfile
from pathlib import PurePosixPath
from typing import IO
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import current_user, import_token, require_csrf
from app.config import settings
from app.database import get_db
from app.importers.apple_xml import parse_apple_health_xml
from app.importers.json_adapter import AdapterResult, parse_json_payload
from app.importers.yazio import parse_yazio_export
from app.models import ApiToken, ImportBatch, ImportError, User
from app.schemas import (
    ImportBatchDetailResponse,
    ImportBatchResponse,
    ImportErrorResponse,
    ImportSummary,
)
from app.services.import_service import persist_import
from app.services.rate_limit import check_rate_limit

router = APIRouter(tags=["Import"])


async def _json_body(request: Request) -> tuple[bytes, dict[str, object]]:
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.max_json_payload_bytes:
        raise HTTPException(status_code=413, detail="JSON-Payload ist zu groß")
    raw = await request.body()
    if len(raw) > settings.max_json_payload_bytes:
        raise HTTPException(status_code=413, detail="JSON-Payload ist zu groß")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Ungültiges JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="JSON-Wurzel muss ein Objekt sein")
    return raw, payload


@router.post("/import/apple-health", response_model=ImportSummary)
async def import_json(
    request: Request,
    client_identifier: str | None = Header(default=None, alias="X-Client-Identifier"),
    identity: tuple[User, ApiToken] = Depends(import_token),
    db: Session = Depends(get_db),
) -> ImportSummary:
    user, token = identity
    check_rate_limit(db, "import", str(token.id), settings.import_rate_limit)
    raw, payload = await _json_body(request)
    try:
        result = parse_json_payload(payload, user.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return persist_import(
        db, user, result, raw, "application/json", client_identifier or token.label
    )


@router.post("/import/apple-health/validate", response_model=ImportSummary)
async def validate_json(
    request: Request,
    identity: tuple[User, ApiToken] = Depends(import_token),
) -> ImportSummary:
    user, _ = identity
    _, payload = await _json_body(request)
    try:
        result = parse_json_payload(payload, user.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ImportSummary(
        status="valid" if not result.errors else "valid_with_errors",
        received=result.received,
        skipped=result.unknown_count,
        failed=len(result.errors),
        unknown_types=sorted(result.unknown_types),
    )


@router.post("/import/yazio", response_model=ImportSummary)
async def import_yazio_json(
    request: Request,
    client_identifier: str | None = Header(default=None, alias="X-Client-Identifier"),
    identity: tuple[User, ApiToken] = Depends(import_token),
    db: Session = Depends(get_db),
) -> ImportSummary:
    user, token = identity
    check_rate_limit(db, "import", str(token.id), settings.import_rate_limit)
    _, payload = await _json_body(request)
    source_identifier = client_identifier or "yazio-account"
    try:
        result = parse_yazio_export(payload, user.timezone, source_identifier)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return persist_import(
        db, user, result, None, "application/json", client_identifier or token.label
    )


@router.post("/import/yazio/validate", response_model=ImportSummary)
async def validate_yazio_json(
    request: Request,
    identity: tuple[User, ApiToken] = Depends(import_token),
) -> ImportSummary:
    user, _ = identity
    _, payload = await _json_body(request)
    try:
        result = parse_yazio_export(payload, user.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ImportSummary(
        status="valid" if not result.errors else "valid_with_errors",
        received=result.received,
        skipped=result.unknown_count,
        failed=len(result.errors),
        unknown_types=sorted(result.unknown_types),
    )


@router.post("/import/apple-health/file", response_model=ImportSummary)
async def import_file(
    file: UploadFile = File(...),
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ImportSummary:
    filename = (file.filename or "").lower()
    if not (filename.endswith(".xml") or filename.endswith(".zip")):
        raise HTTPException(status_code=415, detail="Nur Apple-Health-XML oder ZIP ist erlaubt")
    with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024, mode="w+b") as buffer:
        total = 0
        while chunk := await file.read(1024 * 1024):
            total += len(chunk)
            if total > settings.max_upload_bytes:
                raise HTTPException(status_code=413, detail="Upload ist zu groß")
            buffer.write(chunk)
        buffer.seek(0)
        if filename.endswith(".zip"):
            result = _parse_zip(buffer, user.timezone)
        else:
            result = parse_apple_health_xml(buffer, user.timezone)
    return persist_import(
        db, user, result, None, file.content_type or "application/octet-stream", filename
    )


@router.post("/import/yazio/file", response_model=ImportSummary)
async def import_yazio_file(
    file: UploadFile = File(...),
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> ImportSummary:
    filename = (file.filename or "").lower()
    if not filename.endswith(".json"):
        raise HTTPException(
            status_code=415,
            detail="Nur eine YAZIO-days.json oder -nutrients.json ist erlaubt",
        )

    raw = bytearray()
    while chunk := await file.read(1024 * 1024):
        raw.extend(chunk)
        if len(raw) > settings.max_json_payload_bytes:
            raise HTTPException(status_code=413, detail="JSON-Datei ist zu groß")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Ungültiges JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="JSON-Wurzel muss ein Objekt sein")
    try:
        result = parse_yazio_export(payload, user.timezone)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return persist_import(
        db, user, result, None, file.content_type or "application/json", filename
    )


def _parse_zip(buffer: IO[bytes], timezone: str) -> AdapterResult:
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
            return parse_apple_health_xml(xml_stream, timezone)


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
