"""Owner-scoped, immutable Nutrition Expert data-consent resources.

This service records the exact consent that a future, trusted Expert
application service may require at read time. It performs no health-data read,
credential decryption, provider request, or model call.
"""

from collections.abc import Sequence
from contextlib import nullcontext
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem, ProviderConnectionService, _aware
from app.ai.consents_contracts import (
    CONSENT_POLICY_VERSION,
    MAX_CONSENTS_PER_ACCOUNT,
    AIConsentListQuery,
    AIConsentPage,
    AIConsentPut,
    AIConsentView,
    ConsentDataUse,
    ConsentHistory,
)
from app.ai.contracts import ProviderKind
from app.models import AIConsent, AIConsentRevocation, AIProviderConnection, User
from app.services.user_operation_lock import (
    exclusive_user_lifecycle_operation,
    shared_user_operation,
)


def _now() -> datetime:
    return datetime.now(UTC)


class AIConsentService:
    def __init__(self, db: Session, *, owner: User) -> None:
        self._db, self._owner = db, owner
        self._connections = ProviderConnectionService(db, owner=owner)

    def _active(self) -> None:
        self._connections._active()

    def _row(self, consent_id: UUID) -> AIConsent | None:
        return self._db.get(AIConsent, (self._owner.id, consent_id), populate_existing=True)

    def _view(self, row: AIConsent) -> AIConsentView:
        revocation = self._db.get(
            AIConsentRevocation, (self._owner.id, row.id), populate_existing=True,
        )
        revoked_at = revocation.revoked_at if revocation is not None else row.revoked_at
        connection = self._db.get(
            AIProviderConnection, (self._owner.id, row.connection_id), populate_existing=True,
        )
        if revoked_at is not None:
            state = "revoked"
        elif connection is None or not connection.enabled or connection.model_id is None:
            state = "connection_disabled"
        elif (
            connection.configuration_version != row.configuration_version
            or connection.provider_kind != row.provider_kind
            or connection.model_id != row.model_id
            or connection.base_url != row.base_url
            or row.policy_version != CONSENT_POLICY_VERSION
        ):
            state = "configuration_changed"
        else:
            state = "active"
        return AIConsentView(
            id=row.id, connection_id=row.connection_id,
            configuration_version=row.configuration_version,
            provider_kind=row.provider_kind, model_id=row.model_id, base_url=row.base_url,
            purpose=row.purpose, data_domains=tuple(row.data_domains),
            include_meal_names=row.include_meal_names,
            history=ConsentHistory(mode=row.history_mode, start=row.history_start, end=row.history_end),
            policy_version=row.policy_version, state=state,
            created_at=_aware(row.created_at),
            revoked_at=_aware(revoked_at) if revoked_at is not None else None,
        )

    @staticmethod
    def _same_grant(row: AIConsent, payload: AIConsentPut) -> bool:
        return (
            row.connection_id == payload.connection_id
            and row.configuration_version == payload.configuration_version
            and row.purpose == payload.purpose
            and tuple(row.data_domains) == payload.data_domains
            and row.include_meal_names == payload.include_meal_names
            and row.history_mode == payload.history.mode
            and row.history_start == payload.history.start
            and row.history_end == payload.history.end
            and row.policy_version == payload.policy_version
        )

    def put(
        self, consent_id: UUID, payload: AIConsentPut, *, _operation_locked: bool = False,
    ) -> tuple[AIConsentView, bool]:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner.id)
        with context:
            self._active()
            existing = self._row(consent_id)
            if existing is not None:
                if not self._same_grant(existing, payload):
                    raise ConnectionProblem(
                        "ai-consent-id-conflict", 409,
                        "Eine Einwilligung mit dieser ID existiert bereits.",
                    )
                # Retrying an old client UUID returns its resource, including
                # revoked/stale state, and never restores its authorization.
                return self._view(existing), False

            if self._db.get(AIConsentRevocation, (self._owner.id, consent_id), populate_existing=True) is not None:
                raise ConnectionProblem(
                    "ai-consent-revoked", 409,
                    "Diese Einwilligungs-ID wurde dauerhaft widerrufen. Bitte erneut ausdrücklich zustimmen.",
                )

            connection = self._connections.get(payload.connection_id)
            base_url = str(connection.base_url) if connection.base_url is not None else None
            if not connection.enabled or connection.model_id is None:
                raise ConnectionProblem(
                    "ai-consent-connection-disabled", 422,
                    "Für diese Einwilligung muss eine aktivierte Modellverbindung gewählt sein.",
                )
            if connection.provider_kind == ProviderKind.CHATGPT_PLAN:
                raise ConnectionProblem(
                    "ai-consent-provider-unavailable", 422,
                    "Dieser Anmeldeweg ist noch nicht verfügbar.",
                )
            if connection.configuration_version != payload.configuration_version:
                raise ConnectionProblem(
                    "ai-configuration-changed", 409,
                    "Die Verbindung wurde geändert. Bitte neu laden.",
                )
            if payload.policy_version != CONSENT_POLICY_VERSION:
                raise ConnectionProblem(
                    "ai-consent-policy-outdated", 409,
                    "Der Datenschutzhinweis wurde aktualisiert. Bitte erneut zustimmen.",
                )

            # The exclusive account lock also prevents two clients from
            # granting the exact same current permission under different IDs.
            # Only current-version candidates are considered, bounded by the
            # per-account current-grant cap; old or revoked grants do not block
            # an explicit replacement.
            equivalents = self._db.scalars(select(AIConsent).where(
                AIConsent.user_id == self._owner.id,
                AIConsent.connection_id == connection.id,
                AIConsent.configuration_version == connection.configuration_version,
                AIConsent.policy_version == CONSENT_POLICY_VERSION,
                AIConsent.revoked_at.is_(None),
            ).execution_options(populate_existing=True))
            if any(self._same_grant(row, payload) for row in equivalents):
                raise ConnectionProblem(
                    "ai-consent-already-exists", 409,
                    "Eine entsprechende aktuelle Einwilligung besteht bereits.",
                )

            active_count = self._db.scalar(
                select(func.count()).select_from(AIConsent).join(
                    AIProviderConnection,
                    (AIProviderConnection.user_id == AIConsent.user_id)
                    & (AIProviderConnection.id == AIConsent.connection_id),
                ).where(
                    AIConsent.user_id == self._owner.id,
                    AIConsent.revoked_at.is_(None),
                    AIConsent.configuration_version == AIProviderConnection.configuration_version,
                    AIConsent.provider_kind == AIProviderConnection.provider_kind,
                    AIConsent.model_id == AIProviderConnection.model_id,
                    AIConsent.base_url.is_not_distinct_from(AIProviderConnection.base_url),
                    AIConsent.policy_version == CONSENT_POLICY_VERSION,
                    AIProviderConnection.enabled.is_(True),
                    AIProviderConnection.model_id.is_not(None),
                )
            ) or 0
            if active_count >= MAX_CONSENTS_PER_ACCOUNT:
                raise ConnectionProblem(
                    "ai-consent-limit", 409,
                    "Maximal 200 aktuelle KI-Einwilligungen sind erlaubt.",
                )
            row = AIConsent(
                user_id=self._owner.id, id=consent_id,
                connection_id=connection.id,
                configuration_version=connection.configuration_version,
                provider_kind=connection.provider_kind, model_id=connection.model_id,
                base_url=base_url, purpose=payload.purpose,
                data_domains=list(payload.data_domains),
                include_meal_names=payload.include_meal_names,
                history_mode=payload.history.mode, history_start=payload.history.start,
                history_end=payload.history.end, policy_version=payload.policy_version,
                created_at=_now(), revoked_at=None,
            )
            self._db.add(row)
            self._db.commit()
            return self._view(row), True

    def get(self, consent_id: UUID) -> AIConsentView:
        with shared_user_operation(self._db, self._owner.id):
            self._active()
            row = self._row(consent_id)
            if row is None:
                raise ConnectionProblem("ai-consent-not-found", 404, "Einwilligung nicht gefunden.")
            return self._view(row)

    def list(self, query: AIConsentListQuery) -> AIConsentPage:
        with shared_user_operation(self._db, self._owner.id):
            self._active()
            statement = select(AIConsent).where(AIConsent.user_id == self._owner.id)
            if query.connection_id is not None:
                statement = statement.where(AIConsent.connection_id == query.connection_id)
            if query.after is not None:
                statement = statement.where(AIConsent.id > query.after)
            rows = list(self._db.scalars(
                statement.order_by(AIConsent.id).limit(query.limit + 1)
                .execution_options(populate_existing=True),
            ))
            page_rows: Sequence[AIConsent] = rows[:query.limit]
            views = tuple(self._view(row) for row in page_rows)
            return AIConsentPage(
                items=views,
                next_after=rows[query.limit - 1].id if len(rows) > query.limit else None,
            )

    def revoke(self, consent_id: UUID, *, _operation_locked: bool = False) -> None:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(self._db, self._owner.id)
        with context:
            self._active()
            row = self._row(consent_id)
            revocation = self._db.get(
                AIConsentRevocation, (self._owner.id, consent_id), populate_existing=True,
            )
            changed = False
            if revocation is None:
                revocation = AIConsentRevocation(
                    user_id=self._owner.id, consent_id=consent_id,
                    revoked_at=row.revoked_at if row is not None and row.revoked_at is not None else _now(),
                )
                self._db.add(revocation)
                changed = True
            if row is not None and row.revoked_at is None:
                row.revoked_at = revocation.revoked_at
                changed = True
            if changed:
                self._db.commit()

    def require_usable(
        self, consent_id: UUID, use: ConsentDataUse, *, _operation_locked: bool = False,
    ) -> AIConsentView:
        """Authorize a bounded trusted read; this does not serialize a later send."""
        context = nullcontext() if _operation_locked else shared_user_operation(self._db, self._owner.id)
        with context:
            self._active()
            row = self._row(consent_id)
            if row is None:
                raise ConnectionProblem("ai-consent-not-found", 404, "Einwilligung nicht gefunden.")
            view = self._view(row)
            if view.state != "active":
                raise ConnectionProblem("ai-consent-inactive", 409, "Die Einwilligung ist nicht mehr aktuell.")
            if (
                view.purpose != use.purpose
                or not set(use.data_domains).issubset(view.data_domains)
                or (use.include_meal_names and not view.include_meal_names)
            ):
                raise ConnectionProblem("ai-consent-scope", 403, "Die Einwilligung deckt diese Daten nicht ab.")
            if view.history.mode == "date_range":
                assert view.history.start is not None and view.history.end is not None
                if use.start < view.history.start or use.end > view.history.end:
                    raise ConnectionProblem("ai-consent-history-scope", 403, "Der Zeitraum ist nicht freigegeben.")
            today = datetime.now(UTC).astimezone(ZoneInfo(self._owner.timezone)).date()
            if use.end > today:
                raise ConnectionProblem("ai-consent-history-scope", 422, "Der Zeitraum darf nicht in der Zukunft liegen.")
            return view
