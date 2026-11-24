"""Thin authenticated REST adapters; reconnecting never creates generation."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, Security
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem
from app.api.ai_connections import _problem
from app.api.ai_connections import router as ai_router
from app.auth.dependencies import current_user, require_csrf, require_csrf_exclusive
from app.auth.openapi import session_scheme
from app.database import get_db
from app.expert.chat_contracts import (
    ConversationPage,
    ConversationPut,
    ConversationView,
    ExpertSetup,
    ExpertSetupQuery,
    ListQuery,
    RunEvents,
    RunPage,
    RunPut,
    RunView,
)
from app.expert.runs import ExpertRunService, execute_run
from app.models import User

router = APIRouter(
    prefix="/expert",
    tags=["Nutrition Expert"],
    dependencies=[Security(session_scheme)],
    responses=ai_router.responses,
)
Owner = Annotated[User, Depends(current_user)]
Writer = Annotated[User, Depends(require_csrf_exclusive, scope="function")]
DB = Annotated[Session, Depends(get_db)]


@router.get("/setup", response_model=ExpertSetup, operation_id="expert_get_setup")
def setup(query: Annotated[ExpertSetupQuery, Query()], owner: Owner, db: DB) -> ExpertSetup:
    try:
        return ExpertRunService(db, owner=owner).setup(
            query.mode, include_attachments=query.include_attachments,
        )
    except ConnectionProblem as exc:
        _problem(exc)


@router.get(
    "/conversations", response_model=ConversationPage, operation_id="expert_list_conversations"
)
def list_conversations(
    query: Annotated[ListQuery, Query()], owner: Owner, db: DB
) -> ConversationPage:
    return ExpertRunService(db, owner=owner).conversations(query)


@router.put(
    "/conversations/{identifier}",
    response_model=ConversationView,
    operation_id="expert_put_conversation",
    responses={201: {"model": ConversationView}},
    description="Idempotently create an account-local chat or analysis conversation. Its mode is immutable. No generation is started.",
)
def put_conversation(
    identifier: UUID, payload: ConversationPut, response: Response, owner: Writer, db: DB
) -> ConversationView:
    try:
        view, created = ExpertRunService(db, owner=owner).put_conversation(
            identifier, payload, _operation_locked=True
        )
        response.status_code = 201 if created else 200
        return view
    except ConnectionProblem as exc:
        _problem(exc)


@router.get(
    "/conversations/{identifier}",
    response_model=ConversationView,
    operation_id="expert_get_conversation",
)
def get_conversation(identifier: UUID, owner: Owner, db: DB) -> ConversationView:
    try:
        return ExpertRunService(db, owner=owner).get_conversation(identifier)
    except ConnectionProblem as exc:
        _problem(exc)


@router.delete(
    "/conversations/{identifier}",
    status_code=204,
    response_class=Response,
    operation_id="expert_delete_conversation",
)
def delete_conversation(identifier: UUID, owner: Writer, db: DB) -> Response:
    ExpertRunService(db, owner=owner).delete_conversation(identifier, _operation_locked=True)
    return Response(status_code=204)


@router.get(
    "/conversations/{identifier}/turns", response_model=RunPage, operation_id="expert_list_turns"
)
def list_turns(
    identifier: UUID, query: Annotated[ListQuery, Query()], owner: Owner, db: DB
) -> RunPage:
    try:
        return ExpertRunService(db, owner=owner).turns(identifier, query)
    except ConnectionProblem as exc:
        _problem(exc)


@router.put(
    "/conversations/{conversation_id}/turns/{identifier}",
    response_model=RunView,
    operation_id="expert_put_turn",
    responses={201: {"model": RunView}},
    description="Create one durable generation claim at a client UUID. Identical retries replay current state and NEVER schedule a second generation. Conflicting input returns409. All-history context and selected settings are server controlled; consent and active provider are rechecked at final dispatch. No database transaction remains open during provider I/O.",
)
def put_turn(
    conversation_id: UUID,
    identifier: UUID,
    payload: RunPut,
    response: Response,
    background: BackgroundTasks,
    owner: Writer,
    db: DB,
) -> RunView:
    try:
        view, created = ExpertRunService(db, owner=owner).put_run(
            conversation_id, identifier, payload, _operation_locked=True
        )
        if created:
            background.add_task(execute_run, owner.id, identifier)
        response.status_code = 201 if created else 200
        return view
    except ConnectionProblem as exc:
        _problem(exc)


@router.get("/runs/{identifier}", response_model=RunView, operation_id="expert_get_run")
def get_run(identifier: UUID, owner: Owner, db: DB) -> RunView:
    try:
        return ExpertRunService(db, owner=owner).get_run(identifier)
    except ConnectionProblem as exc:
        _problem(exc)


@router.get(
    "/runs/{identifier}/events",
    response_model=RunEvents,
    operation_id="expert_get_run_events",
    description="Bounded, typed durable state events; reconnect/read never triggers generation. sequence is stable, terminal state is immutable.",
)
def get_events(identifier: UUID, owner: Owner, db: DB) -> RunEvents:
    try:
        return ExpertRunService(db, owner=owner).events(identifier)
    except ConnectionProblem as exc:
        _problem(exc)


@router.put(
    "/runs/{identifier}/cancel",
    response_model=RunView,
    operation_id="expert_cancel_run",
    description="Idempotently cancel an owned pending run. Before dispatch this prevents sending; after dispatch it discards the result but cannot retrieve data already sent.",
)
def cancel_run(identifier: UUID, owner: Annotated[User, Depends(require_csrf)], db: DB) -> RunView:
    try:
        return ExpertRunService(db, owner=owner).cancel(identifier, _operation_locked=True)
    except ConnectionProblem as exc:
        _problem(exc)
