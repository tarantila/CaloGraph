from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class ProviderSourceInstance:
    provider_key: str
    source_instance_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.provider_key, str):
            raise ValueError("provider_key must be a string")
        normalized_provider_key = self.provider_key.strip().lower()
        if not normalized_provider_key or any(
            character.isspace() for character in normalized_provider_key
        ):
            raise ValueError("provider_key must be a non-empty token")
        if not isinstance(self.source_instance_id, UUID) or self.source_instance_id.int == 0:
            raise ValueError("source_instance_id must be a non-zero UUID")
        object.__setattr__(self, "provider_key", normalized_provider_key)


@dataclass(frozen=True, slots=True)
class ProviderSourceBindings:
    bindings: tuple[ProviderSourceInstance, ...] = ()

    def __post_init__(self) -> None:
        normalized = tuple(self.bindings)
        if any(not isinstance(binding, ProviderSourceInstance) for binding in normalized):
            raise ValueError("bindings must contain ProviderSourceInstance values")
        provider_keys = [binding.provider_key for binding in normalized]
        if len(provider_keys) != len(set(provider_keys)):
            raise ValueError("duplicate provider source binding")
        object.__setattr__(
            self,
            "bindings",
            tuple(
                sorted(
                    normalized,
                    key=lambda binding: (binding.provider_key, str(binding.source_instance_id)),
                )
            ),
        )

    def __iter__(self) -> Iterator[ProviderSourceInstance]:
        return iter(self.bindings)

    def __len__(self) -> int:
        return len(self.bindings)

    def for_provider(self, provider_key: str) -> ProviderSourceInstance | None:
        normalized_provider_key = provider_key.strip().lower()
        return next(
            (binding for binding in self.bindings if binding.provider_key == normalized_provider_key),
            None,
        )

    @property
    def by_provider(self) -> Mapping[str, ProviderSourceInstance]:
        return MappingProxyType({binding.provider_key: binding for binding in self.bindings})


class ProviderSourceResolver(Protocol):
    provider_key: str

    def resolve_source_instance_id(self, db: Session, *, user_id: UUID) -> UUID | None: ...

    def owns_source_instance_id(
        self,
        db: Session,
        *,
        user_id: UUID,
        source_instance_id: UUID,
    ) -> bool: ...


class YazioSourceInstanceResolver:
    provider_key = "yazio"

    def resolve_source_instance_id(self, db: Session, *, user_id: UUID) -> UUID | None:
        from app.models import YazioConnection

        return db.scalar(select(YazioConnection.id).where(YazioConnection.user_id == user_id))

    def owns_source_instance_id(
        self,
        db: Session,
        *,
        user_id: UUID,
        source_instance_id: UUID,
    ) -> bool:
        from app.models import YazioConnection

        return (
            db.scalar(
                select(YazioConnection.id).where(
                    YazioConnection.id == source_instance_id,
                    YazioConnection.user_id == user_id,
                )
            )
            is not None
        )


DEFAULT_SOURCE_RESOLVERS: Mapping[str, ProviderSourceResolver] = MappingProxyType(
    {"yazio": YazioSourceInstanceResolver()}
)


def normalize_provider_sources(
    bindings: ProviderSourceBindings | Sequence[ProviderSourceInstance],
) -> ProviderSourceBindings:
    if isinstance(bindings, ProviderSourceBindings):
        return bindings
    return ProviderSourceBindings(tuple(bindings))


def resolve_default_provider_sources(
    db: Session,
    *,
    user_id: UUID,
    provider_keys: Iterable[str],
    resolver_registry: Mapping[str, ProviderSourceResolver] | None = None,
) -> ProviderSourceBindings:
    registry = DEFAULT_SOURCE_RESOLVERS if resolver_registry is None else resolver_registry
    resolved: list[ProviderSourceInstance] = []
    for provider_key in provider_keys:
        normalized_provider_key = provider_key.strip().lower()
        resolver = registry.get(normalized_provider_key)
        if resolver is None:
            continue
        source_instance_id = resolver.resolve_source_instance_id(db, user_id=user_id)
        if source_instance_id is not None:
            resolved.append(ProviderSourceInstance(normalized_provider_key, source_instance_id))
    return ProviderSourceBindings(tuple(resolved))


def validate_provider_source_bindings(
    db: Session,
    *,
    user_id: UUID,
    bindings: ProviderSourceBindings,
    resolver_registry: Mapping[str, object] | None = None,
) -> None:
    for binding in bindings:
        resolver = DEFAULT_SOURCE_RESOLVERS.get(binding.provider_key)
        if resolver is None:
            if resolver_registry is None:
                raise ValueError("provider source resolver is not configured")
            injected_resolver = resolver_registry.get(binding.provider_key)
            owns_source_instance_id = getattr(
                injected_resolver,
                "owns_source_instance_id",
                None,
            )
            if not callable(owns_source_instance_id):
                raise ValueError("provider source resolver must validate ownership")
            if not owns_source_instance_id(
                db,
                user_id=user_id,
                source_instance_id=binding.source_instance_id,
            ):
                raise ValueError("source_instance_id must belong to the same user")
            continue
        if not resolver.owns_source_instance_id(
            db,
            user_id=user_id,
            source_instance_id=binding.source_instance_id,
        ):
            raise ValueError("source_instance_id must belong to the same user")
