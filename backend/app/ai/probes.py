"""Explicit metadata-only connection checks, reusable by trusted adapters."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem, ProviderConnectionService, _aware
from app.ai.contracts import ProviderKind
from app.ai.outbound import OutboundError, fetch_provider_json
from app.ai.probes_contracts import (
    CONNECTION_PRIVACY_VERSION,
    CheckStatus,
    ConnectionCheckPut,
    ConnectionCheckView,
    ProviderCatalog,
    ProviderDescriptor,
)
from app.models import AIProviderCheck, AIProviderCredential, User
from app.services.credential_crypto import CredentialEncryptionError, decrypt_credential
from app.services.rate_limit import check_rate_limit
from app.services.user_operation_lock import exclusive_user_lifecycle_operation


def provider_catalog() -> ProviderCatalog:
    return ProviderCatalog(
        providers=tuple(ProviderDescriptor(
            kind=kind, availability="planned" if kind == ProviderKind.CHATGPT_PLAN else "available",
            authentication=("chatgpt_oauth" if kind == ProviderKind.CHATGPT_PLAN else
                            "optional_api_key" if kind in {ProviderKind.LOCAL, ProviderKind.GATEWAY} else "api_key"),
            custom_endpoint=kind in {ProviderKind.LOCAL, ProviderKind.GATEWAY},
            model_discovery=kind != ProviderKind.CHATGPT_PLAN,
            manual_model=kind != ProviderKind.CHATGPT_PLAN,
        ) for kind in ProviderKind),
        privacy_version=CONNECTION_PRIVACY_VERSION,
    )


def _model_ids(data: object, kind: ProviderKind, secret: str | None) -> tuple[tuple[str, ...], bool]:
    if not isinstance(data, dict):
        raise ValueError("invalid model list")
    values = data.get("models" if kind == ProviderKind.GEMINI else "data")
    if not isinstance(values, list) or len(values) > 2000:
        raise ValueError("invalid model list")
    identifiers: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("invalid model")
        identifier = value.get("name" if kind == ProviderKind.GEMINI else "id")
        if not isinstance(identifier, str):
            raise ValueError("invalid model ID")
        if kind == ProviderKind.GEMINI:
            identifier = identifier.removeprefix("models/")
        if (not 1 <= len(identifier) <= 200 or identifier != identifier.strip()
            or any(ord(c) < 32 or ord(c) == 127 for c in identifier)
            or (secret and secret in identifier)):
            raise ValueError("invalid model ID")
        identifiers.add(identifier)
    return tuple(sorted(identifiers)), bool(data.get("has_more") or data.get("nextPageToken"))


class ProviderCheckService:
    def __init__(self, db: Session, *, owner: User) -> None:
        self._db, self._owner = db, owner
        self._connections = ProviderConnectionService(db, owner=owner)

    def _view(self, row: AIProviderCheck) -> ConnectionCheckView:
        if row.result is not None:
            return ConnectionCheckView.model_validate(row.result)
        return ConnectionCheckView(
            id=row.id, connection_id=row.connection_id, configuration_version=row.configuration_version,
            privacy_version=row.privacy_version, started_at=_aware(row.started_at), completed_at=None,
            status="interrupted" if _aware(row.started_at) < datetime.now(UTC) - timedelta(minutes=5) else "checking",
            models=(), models_truncated=False, selected_model_available=None,
        )

    def latest(self, connection_id: UUID) -> ConnectionCheckView | None:
        connection = self._connections.get(connection_id)
        row = self._db.scalar(select(AIProviderCheck).where(
            AIProviderCheck.user_id == self._owner.id, AIProviderCheck.connection_id == connection_id,
            AIProviderCheck.configuration_version == connection.configuration_version,
        ).order_by(AIProviderCheck.started_at.desc(), AIProviderCheck.id.desc()).limit(1).execution_options(populate_existing=True))
        return self._view(row) if row is not None else None

    def put(
        self, connection_id: UUID, check_id: UUID, payload: ConnectionCheckPut, *, _operation_locked: bool = False,
    ) -> ConnectionCheckView:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner.id)
        with context:
            connection = self._connections.get(connection_id)
            previous = self._db.get(AIProviderCheck, (self._owner.id, check_id), populate_existing=True)
            if previous is not None:
                if (previous.connection_id != connection_id
                    or previous.configuration_version != payload.configuration_version
                    or previous.privacy_version != payload.privacy_version):
                    raise ConnectionProblem("ai-check-conflict", 409, "Test-ID wurde bereits für eine andere Anfrage verwendet.")
                return self._view(previous)
            if connection.configuration_version != payload.configuration_version:
                raise ConnectionProblem("ai-configuration-changed", 409, "Die Verbindung wurde geändert. Bitte neu laden.")
            now = datetime.now(UTC)
            # Replay guarantees last 24h; bounded account-local retention prevents
            # unbounded probe history. Replays do not consume rate-limit capacity.
            self._db.execute(delete(AIProviderCheck).where(
                AIProviderCheck.user_id == self._owner.id, AIProviderCheck.started_at < now - timedelta(hours=24),
            ))
            count = self._db.scalar(select(func.count()).select_from(AIProviderCheck).where(AIProviderCheck.user_id == self._owner.id)) or 0
            if count >= 100:
                raise ConnectionProblem("ai-check-limit", 409, "Das tägliche Verbindungstest-Limit ist erreicht.")
            check_rate_limit(self._db, "ai-connection-check", f"user:{self._owner.id}", 10, 60)
            row = AIProviderCheck(
                user_id=self._owner.id, id=check_id, connection_id=connection_id,
                configuration_version=payload.configuration_version, privacy_version=payload.privacy_version,
                started_at=now,
            )
            self._db.add(row)
            self._db.commit()  # Persist the claim before any external operation.
            status: CheckStatus = "unsupported"
            models: tuple[str, ...] = ()
            truncated, available = False, None
            kind = connection.provider_kind
            credential = self._db.get(AIProviderCredential, (self._owner.id, connection_id), populate_existing=True)
            try:
                secret = decrypt_credential(credential.encrypted_api_key) if credential is not None else None
                native = kind in {ProviderKind.OPENAI_API, ProviderKind.ANTHROPIC, ProviderKind.GEMINI}
                if native and secret is None:
                    status = "authentication"
                elif kind != ProviderKind.CHATGPT_PLAN:
                    base = {
                        ProviderKind.OPENAI_API: "https://api.openai.com/v1",
                        ProviderKind.ANTHROPIC: "https://api.anthropic.com/v1",
                        ProviderKind.GEMINI: "https://generativelanguage.googleapis.com/v1beta",
                    }.get(kind, str(connection.base_url))
                    headers = {}
                    if secret is not None:
                        if kind == ProviderKind.ANTHROPIC:
                            headers = {"x-api-key": secret, "anthropic-version": "2023-06-01"}
                        elif kind == ProviderKind.GEMINI:
                            headers = {"x-goog-api-key": secret}
                        else:
                            headers = {"Authorization": f"Bearer {secret}"}
                    data = fetch_provider_json(base, "/models", headers=headers, timeout_seconds=min(connection.timeout_seconds, 30), native=native)
                    all_models, partial = _model_ids(data, kind, secret)
                    available = True if connection.model_id in all_models else None if partial else False
                    models = all_models[:100]
                    if available and connection.model_id not in models:
                        models = (*models[:99], connection.model_id)
                    truncated = partial or len(all_models) > 100
                    status = "available" if available else "model_not_listed"
            except CredentialEncryptionError:
                status = "credential_unavailable"
            except OutboundError as exc:
                if exc.code == "http_error":
                    status = "manual_selection" if kind in {ProviderKind.LOCAL, ProviderKind.GATEWAY} and exc.status in {404, 405} else "unavailable"
                else:
                    status = exc.code
            except ValueError:
                status = "invalid_response"
            result = ConnectionCheckView(
                id=check_id, connection_id=connection_id, configuration_version=payload.configuration_version,
                privacy_version=payload.privacy_version, started_at=now, completed_at=datetime.now(UTC),
                status=status, models=models, models_truncated=truncated, selected_model_available=available,
            )
            row.result = result.model_dump(mode="json")
            self._db.commit()
            return result
