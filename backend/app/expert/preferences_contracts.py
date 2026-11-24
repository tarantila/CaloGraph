"""Typed public account-local Nutrition Expert preference contracts."""

from pydantic import Field, field_validator, model_validator

from app.ai.connections_contracts import ConnectionContract
from app.ai.consents_contracts import ConsentDataDomain

_DOMAIN_ORDER: tuple[ConsentDataDomain, ...] = (
    "nutrition", "targets", "weight", "activity", "profile", "health_context",
)


class ExpertPreferencesPut(ConnectionContract):
    data_domains: tuple[ConsentDataDomain, ...] = Field(min_length=1, max_length=6)
    include_meal_names: bool

    @field_validator("data_domains")
    @classmethod
    def unique_canonical_domains(
        cls, value: tuple[ConsentDataDomain, ...],
    ) -> tuple[ConsentDataDomain, ...]:
        if len(set(value)) != len(value):
            raise ValueError("data domains must be unique")
        return tuple(domain for domain in _DOMAIN_ORDER if domain in value)

    @model_validator(mode="after")
    def meal_names_need_nutrition(self) -> ExpertPreferencesPut:
        if self.include_meal_names and "nutrition" not in self.data_domains:
            raise ValueError("meal names require the nutrition domain")
        return self


class ExpertPreferencesView(ConnectionContract):
    data_domains: tuple[ConsentDataDomain, ...] = Field(min_length=1, max_length=6)
    include_meal_names: bool
    configuration_version: int = Field(ge=1)
