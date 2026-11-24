"""Owner-bound Nutrition Expert data-category preferences."""

from contextlib import nullcontext

from sqlalchemy.orm import Session

from app.ai.connections import ConnectionProblem
from app.ai.consents_contracts import ConsentDataDomain
from app.expert.preferences_contracts import ExpertPreferencesPut, ExpertPreferencesView
from app.models import ExpertPreferences, User
from app.services.user_operation_lock import (
    exclusive_user_lifecycle_operation,
    shared_user_operation,
)

DEFAULT_DATA_DOMAINS: tuple[ConsentDataDomain, ...] = (
    "nutrition", "targets", "weight", "activity", "profile", "health_context",
)
DEFAULT_INCLUDE_MEAL_NAMES = True


class ExpertPreferencesService:
    def __init__(self, db: Session, *, owner: User) -> None:
        self._db, self._owner = db, owner

    def _active(self) -> None:
        if not self._owner.is_active:
            raise ConnectionProblem("account-inactive", 403, "Das Konto ist nicht aktiv.")

    def _row(self) -> ExpertPreferences | None:
        return self._db.get(ExpertPreferences, self._owner.id, populate_existing=True)

    @staticmethod
    def _view(row: ExpertPreferences | None) -> ExpertPreferencesView:
        if row is None:
            return ExpertPreferencesView(
                data_domains=DEFAULT_DATA_DOMAINS,
                include_meal_names=DEFAULT_INCLUDE_MEAL_NAMES,
                configuration_version=1,
            )
        return ExpertPreferencesView(
            data_domains=tuple(row.data_domains),
            include_meal_names=row.include_meal_names,
            configuration_version=row.configuration_version,
        )

    def get(self, *, _operation_locked: bool = False) -> ExpertPreferencesView:
        context = nullcontext() if _operation_locked else shared_user_operation(
            self._db, self._owner.id,
        )
        with context:
            self._active()
            return self._view(self._row())

    def put(
        self, payload: ExpertPreferencesPut, *, _operation_locked: bool = False,
    ) -> ExpertPreferencesView:
        context = nullcontext() if _operation_locked else exclusive_user_lifecycle_operation(
            self._db, self._owner.id,
        )
        with context:
            self._active()
            row = self._row()
            before = self._view(row)
            if (
                before.data_domains == payload.data_domains
                and before.include_meal_names == payload.include_meal_names
            ):
                return before
            if row is None:
                row = ExpertPreferences(
                    user_id=self._owner.id,
                    data_domains=list(payload.data_domains),
                    include_meal_names=payload.include_meal_names,
                    configuration_version=before.configuration_version + 1,
                )
                self._db.add(row)
            else:
                row.data_domains = list(payload.data_domains)
                row.include_meal_names = payload.include_meal_names
                row.configuration_version += 1
            self._db.commit()
            self._db.refresh(row)
            return self._view(row)
