"""Closed public contracts for conversations, generation runs and setup."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.ai.connections_contracts import ProviderConnectionView
from app.ai.consents_contracts import AIConsentView
from app.expert.contracts import ExpertContract
from app.expert.preferences_contracts import ExpertPreferencesView

ExpertMode = Literal["chat", "analysis"]
RunState = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]


class ExpertSetupQuery(ExpertContract):
    mode: ExpertMode = "chat"


class ExpertSetup(ExpertContract):
    connection: ProviderConnectionView | None
    preferences: ExpertPreferencesView
    consent: AIConsentView | None
    generation_available: bool


class ConversationPut(ExpertContract):
    mode: ExpertMode


class ConversationView(ExpertContract):
    id: UUID
    mode: ExpertMode
    created_at: datetime
    updated_at: datetime


class ListQuery(ExpertContract):
    limit: int = Field(default=20, ge=1, le=50)
    before_created_at: datetime | None = None
    before_id: UUID | None = None

    @model_validator(mode="after")
    def paired_cursor(self) -> ListQuery:
        if (self.before_created_at is None) != (self.before_id is None):
            raise ValueError("cursor fields must be supplied together")
        if self.before_created_at is not None and self.before_created_at.tzinfo is None:
            raise ValueError("cursor timestamp must include timezone")
        return self


class ListCursor(ExpertContract):
    created_at: datetime
    id: UUID


class ConversationPage(ExpertContract):
    items: tuple[ConversationView, ...] = Field(max_length=50)
    next_before: ListCursor | None


class RunPut(ExpertContract):
    kind: ExpertMode
    message: str = Field(min_length=1, max_length=8000, repr=False)
    connection_id: UUID
    connection_version: int = Field(ge=1)
    preferences_version: int = Field(ge=1)
    consent_id: UUID

    @field_validator("message")
    @classmethod
    def readable_message(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 and char not in "\n\r\t" for char in value):
            raise ValueError("message must contain readable text")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("message must be valid UTF-8") from None
        return value


class AnswerFinding(ExpertContract):
    kind: Literal["observation", "interpretation", "guidance"]
    text: str = Field(min_length=1, max_length=2500, repr=False)
    evidence_ids: tuple[str, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def grounded_finding(self) -> AnswerFinding:
        if self.kind != "guidance" and not self.evidence_ids:
            raise ValueError("observations and interpretations require evidence")
        return self


class ExpertAnswer(ExpertContract):
    text: str = Field(min_length=1, max_length=4000, repr=False)
    findings: tuple[AnswerFinding, ...] = Field(default=(), max_length=8, repr=False)
    limitations: tuple[str, ...] = Field(default=(), max_length=8)
    follow_up: tuple[str, ...] = Field(default=(), max_length=4)


class RunView(ExpertContract):
    id: UUID
    conversation_id: UUID
    kind: ExpertMode
    message: str = Field(repr=False)
    state: RunState
    error_code: str | None
    provider_kind: str
    model_id: str
    connection_id: UUID
    connection_version: int
    preferences_version: int
    created_at: datetime
    dispatched_at: datetime | None
    finished_at: datetime | None
    coverage_start: date | None
    coverage_end: date | None
    answer: ExpertAnswer | None = Field(repr=False)


class RunPage(ExpertContract):
    items: tuple[RunView, ...] = Field(max_length=50, repr=False)
    next_before: ListCursor | None


class RunEvent(ExpertContract):
    sequence: int = Field(ge=1, le=3)
    state: RunState
    at: datetime


class RunEvents(ExpertContract):
    run_id: UUID
    events: tuple[RunEvent, ...] = Field(max_length=3)
