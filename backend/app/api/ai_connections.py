"""REST adapter for user-scoped provider configuration; never probes providers."""

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, Security
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem, ProviderConnectionService
from app.ai.connections_contracts import (
    ProviderConnectionListQuery,
    ProviderConnectionPage,
    ProviderConnectionPut,
    ProviderConnectionView,
)
from app.ai.subscription_authorization_contracts import (
    SubscriptionAuthorizationStart,
    SubscriptionAuthorizationView,
    SubscriptionStatus,
)
from app.ai.subscription_credentials import SubscriptionCredentialProblem
from app.ai.subscription_device import (
    SubscriptionDeviceAuthorizationService,
    SubscriptionDeviceProblem,
)
from app.auth.dependencies import current_user, require_csrf_exclusive
from app.auth.openapi import session_scheme
from app.database import get_db
from app.models import User
from app.problem_types import ProblemHTTPException
from app.schemas import ProblemDetail

router = APIRouter(
    prefix="/settings/ai/connections", tags=["AI providers"], dependencies=[Security(session_scheme)],
    responses={
        status: {
            "description": description,
            "content": {"application/problem+json": {"schema": ProblemDetail.model_json_schema()}},
        }
        for status, description in (
            (401, "Missing or inactive account session."),
            (403, "Invalid CSRF token or request origin for a mutation."),
            (404, "Connection not found in the authenticated account."),
            (409, "Lifecycle conflict, immutable provider type, or account connection limit."),
            (422, "Invalid configuration; secrets and invalid values are not echoed."),
            (503, "Credential encryption is unavailable."),
        )
    },
)


def _problem(exc: ConnectionProblem) -> NoReturn:
    raise ProblemHTTPException(exc.status, exc.detail, f"urn:calograph:problem:{exc.code}") from exc


def _subscription_problem(exc: SubscriptionDeviceProblem | SubscriptionCredentialProblem) -> NoReturn:
    if isinstance(exc, SubscriptionCredentialProblem):
        status = 404 if exc.code == "connection_not_found" else 503
        code = "subscription-device-state-unavailable"
    else:
        status, code = exc.status, exc.code
    raise ProblemHTTPException(
        status, "Die ChatGPT-Verbindung konnte nicht verarbeitet werden.",
        f"urn:calograph:problem:{code}",
    ) from None


@router.get("", response_model=ProviderConnectionPage, operation_id="ai_list_connections")
def list_connections(
    query: Annotated[ProviderConnectionListQuery, Query()],
    owner: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderConnectionPage:
    return ProviderConnectionService(db, owner=owner).list(query)


@router.get("/{connection_id}", response_model=ProviderConnectionView, operation_id="ai_get_connection")
def get_connection(
    connection_id: UUID,
    owner: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderConnectionView:
    try:
        return ProviderConnectionService(db, owner=owner).get(connection_id)
    except ConnectionProblem as exc:
        _problem(exc)


@router.put(
    "/{connection_id}", response_model=ProviderConnectionView, operation_id="ai_put_connection",
    description=(
        "Idempotently create/replace configuration at a client-generated UUID scoped to this account. "
        "Deleted UUIDs cannot be reused; provide a new UUID for a new connection. "
        "Requires X-CSRF-Token. Omit api_key to retain it, null removes it; saved secrets are never "
        "returned. Provider kind is immutable. Changing a custom destination requires explicit key "
        "replacement/removal. No network request or consent to transmit health data is made. "
        "ChatGPT-plan credentials must use a future supported OAuth flow, not this API-key resource."
    ),
    responses={201: {"model": ProviderConnectionView, "description": "Configuration created."}},
)
def put_connection(
    connection_id: UUID,
    payload: ProviderConnectionPut,
    response: Response,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> ProviderConnectionView:
    try:
        result, created = ProviderConnectionService(db, owner=owner).put(connection_id, payload, _operation_locked=True)
        response.status_code = 201 if created else 200
        return result
    except ConnectionProblem as exc:
        _problem(exc)


@router.delete(
    "/{connection_id}", status_code=204, response_class=Response,
    operation_id="ai_delete_connection",
    description="Idempotently remove account-local configuration and credentials and retire this UUID, even if not yet created; requires X-CSRF-Token. Does not revoke a key at its provider.",
)
def delete_connection(
    connection_id: UUID,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    ProviderConnectionService(db, owner=owner).delete(connection_id, _operation_locked=True)
    return Response(status_code=204)


@router.get(
    "/{connection_id}/subscription", response_model=SubscriptionStatus,
    operation_id="ai_get_subscription_status",
)
def get_subscription_status(
    connection_id: UUID,
    owner: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SubscriptionStatus:
    try:
        return SubscriptionDeviceAuthorizationService(db, owner=owner).status(connection_id)
    except (SubscriptionDeviceProblem, SubscriptionCredentialProblem) as exc:
        _subscription_problem(exc)


@router.delete(
    "/{connection_id}/subscription", status_code=204, response_class=Response,
    operation_id="ai_disconnect_subscription",
    description=(
        "Locally disables ChatGPT-plan use, cancels any pending device authorization and clears stored tokens "
        "before at most one best-effort remote revocation. Requires X-CSRF-Token."
    ),
)
def disconnect_subscription(
    connection_id: UUID,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        SubscriptionDeviceAuthorizationService(db, owner=owner).disconnect(
            connection_id, _operation_locked=True,
        )
        return Response(status_code=204)
    except (SubscriptionDeviceProblem, SubscriptionCredentialProblem) as exc:
        _subscription_problem(exc)


@router.get(
    "/{connection_id}/subscription/authorizations/current",
    response_model=SubscriptionAuthorizationView | None,
    operation_id="ai_get_current_subscription_authorization",
)
def get_current_subscription_authorization(
    connection_id: UUID,
    owner: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SubscriptionAuthorizationView | None:
    try:
        return SubscriptionDeviceAuthorizationService(db, owner=owner).current(connection_id)
    except (SubscriptionDeviceProblem, SubscriptionCredentialProblem) as exc:
        _subscription_problem(exc)


@router.get(
    "/{connection_id}/subscription/authorizations/{attempt_id}",
    response_model=SubscriptionAuthorizationView,
    operation_id="ai_get_subscription_authorization",
)
def get_subscription_authorization(
    connection_id: UUID,
    attempt_id: UUID,
    owner: Annotated[User, Depends(current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SubscriptionAuthorizationView:
    try:
        return SubscriptionDeviceAuthorizationService(db, owner=owner).get(connection_id, attempt_id)
    except (SubscriptionDeviceProblem, SubscriptionCredentialProblem) as exc:
        _subscription_problem(exc)


@router.put(
    "/{connection_id}/subscription/authorizations/{attempt_id}",
    response_model=SubscriptionAuthorizationView,
    operation_id="ai_put_subscription_authorization",
    responses={
        201: {"model": SubscriptionAuthorizationView, "description": "Device authorization started."},
        429: {
            "description": "The account-local device authorization rate limit was reached.",
            "content": {"application/problem+json": {"schema": ProblemDetail.model_json_schema()}},
        },
    },
    description=(
        "Starts one account-bound official device-code authorization. Requires the current privacy acknowledgement "
        "and expected credential version. The server contacts the fixed OpenAI authorization endpoint; only the "
        "short user code and fixed verification URI are returned. Tokens, authorization codes, PKCE material and "
        "device identifiers are never returned. Replays never issue a second start request. Requires X-CSRF-Token."
    ),
)
def put_subscription_authorization(
    connection_id: UUID,
    attempt_id: UUID,
    payload: SubscriptionAuthorizationStart,
    response: Response,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> SubscriptionAuthorizationView:
    try:
        view, created = SubscriptionDeviceAuthorizationService(db, owner=owner).start(
            connection_id, attempt_id, payload, _operation_locked=True,
        )
        response.status_code = 201 if created else 200
        return view
    except (SubscriptionDeviceProblem, SubscriptionCredentialProblem) as exc:
        _subscription_problem(exc)


@router.post(
    "/{connection_id}/subscription/authorizations/{attempt_id}/poll",
    response_model=SubscriptionAuthorizationView,
    operation_id="ai_poll_subscription_authorization",
    description=(
        "Performs at most one server-side poll when the provider interval permits. Completion exchanges the "
        "authorization code server-side and stores verified tokens encrypted; credentials are never returned. "
        "Requires X-CSRF-Token."
    ),
)
def poll_subscription_authorization(
    connection_id: UUID,
    attempt_id: UUID,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> SubscriptionAuthorizationView:
    try:
        return SubscriptionDeviceAuthorizationService(db, owner=owner).poll(
            connection_id, attempt_id, _operation_locked=True,
        )
    except (SubscriptionDeviceProblem, SubscriptionCredentialProblem) as exc:
        _subscription_problem(exc)


@router.delete(
    "/{connection_id}/subscription/authorizations/{attempt_id}",
    status_code=204, response_class=Response,
    operation_id="ai_cancel_subscription_authorization",
    responses={
        429: {
            "description": "The account-local device authorization rate limit was reached.",
            "content": {"application/problem+json": {"schema": ProblemDetail.model_json_schema()}},
        },
    },
    description=(
        "Cancels and scrubs a device authorization. An unknown attempt ID is retained as a cancellation receipt "
        "so a delayed start using that ID cannot be issued later. Requires X-CSRF-Token."
    ),
)
def cancel_subscription_authorization(
    connection_id: UUID,
    attempt_id: UUID,
    owner: Annotated[User, Depends(require_csrf_exclusive)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        SubscriptionDeviceAuthorizationService(db, owner=owner).cancel(
            connection_id, attempt_id, _operation_locked=True,
        )
        return Response(status_code=204)
    except (SubscriptionDeviceProblem, SubscriptionCredentialProblem) as exc:
        _subscription_problem(exc)
