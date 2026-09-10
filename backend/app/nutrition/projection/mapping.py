from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.nutrition.enums import (
    CoverageState,
    LineageState,
    PresenceState,
    ProjectionLineageRole,
    ResolutionState,
)
from app.nutrition.resolution.contracts import ProviderCandidate
from app.nutrition.resolution.metrics import canonical_unit
from app.source_priority.contracts import PrioritySelection, PrioritySelectionRole

from .contracts import (
    CANONICAL_METRIC_KEYS,
    DailyProjectionBuildInput,
    ProjectionContractError,
    validate_projection_decimal,
)


@dataclass(frozen=True, slots=True)
class ProjectionLineagePayload:
    source_observation_id: UUID
    provider_key: str
    role: ProjectionLineageRole
    granularity: str | None
    contribution_value: Decimal | None
    presence_state: str | None
    coverage_state: str
    reason_code: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ProjectionFactPayload:
    metric_key: str
    value: Decimal | None
    unit: str
    selected_provider_key: str | None
    selected_granularity: str | None
    presence_state: str
    coverage_state: str
    resolution_state: str
    lineage_state: str
    diagnostic_metadata: dict[str, Any]
    lineage: tuple[ProjectionLineagePayload, ...]


def _safe_disposition_metadata(selection: PrioritySelection) -> dict[str, Any]:
    return {
        "selection_reason": selection.reason_code.value,
        "applied_scope": selection.applied_scope.value,
        "policy_version": selection.policy.version if selection.policy is not None else None,
        "dispositions": [
            {
                "provider_key": disposition.provider_key,
                "rule_id": str(disposition.rule_id) if disposition.rule_id is not None else None,
                "priority_rank": disposition.priority_rank,
                "candidate_present": disposition.candidate_present,
                "eligible": disposition.eligible,
                "role": disposition.role.value if disposition.role is not None else None,
                "reason_code": disposition.reason_code.value,
            }
            for disposition in sorted(
                selection.dispositions,
                key=lambda item: (
                    item.priority_rank if item.priority_rank is not None else 2**31,
                    item.provider_key,
                ),
            )
        ],
    }


@dataclass(frozen=True, slots=True)
class _LineageItem:
    source_observation_id: UUID
    evidence_id: UUID
    evidence_kind: str
    provider_key: str
    role: ProjectionLineageRole
    granularity: str | None
    value: Decimal | None
    presence_state: str | None
    coverage_state: str
    reason_codes: tuple[str, ...]
def _validate_candidate_scope(selection: PrioritySelection) -> None:
    for disposition in selection.dispositions:
        candidate = disposition.candidate
        if candidate is None:
            continue
        if candidate.user_id != selection.user_id:
            raise ProjectionContractError("candidate user_id does not match selection")
        if candidate.local_date != selection.local_date:
            raise ProjectionContractError("candidate local_date does not match selection")
        if candidate.metric_key != selection.metric_key:
            raise ProjectionContractError("candidate metric_key does not match selection")






def _candidate_evidence(
    candidate: ProviderCandidate,
    disposition_reason: str,
    role: ProjectionLineageRole,
    *,
    include_contributing: bool,
) -> tuple[_LineageItem, ...]:
    evidence = candidate.source_lineage if include_contributing else candidate.diagnostic_evidence
    numeric_role = role in {ProjectionLineageRole.SELECTED, ProjectionLineageRole.FALLBACK}
    items: list[_LineageItem] = []
    for item in evidence:
        is_contributing = item.value_contributing and numeric_role
        if is_contributing:
            validate_projection_decimal(item.value, "lineage contribution")
        if include_contributing and not is_contributing and numeric_role:
            continue
        items.append(
            _LineageItem(
                source_observation_id=item.source_observation_id,
                evidence_id=item.evidence_id,
                evidence_kind=item.evidence_kind.value,
                provider_key=candidate.provider_key,
                role=role,
                granularity=(
                    candidate.selected_granularity.value
                    if candidate.selected_granularity is not None
                    else None
                ),
                value=item.value if is_contributing else None,
                presence_state=item.presence_state.value,
                coverage_state=candidate.coverage_state.value,
                reason_codes=tuple(
                    sorted(
                        {
                            item.reason_code.value if item.reason_code is not None else disposition_reason,
                            disposition_reason,
                        }
                    )
                ),
            )
        )
    return tuple(items)


def _disposition_items(
    selection: PrioritySelection,
    *,
    winner: ProviderCandidate | None,
) -> tuple[_LineageItem, ...]:
    items: list[_LineageItem] = []
    for disposition in selection.dispositions:
        candidate = disposition.candidate
        if candidate is None:
            continue
        if disposition.rule_id is None:
            continue
        reason = disposition.reason_code.value
        if winner is candidate and disposition.role in {
            PrioritySelectionRole.SELECTED,
            PrioritySelectionRole.FALLBACK,
        }:
            role = ProjectionLineageRole(disposition.role.value)
            items.extend(
                _candidate_evidence(
                    candidate,
                    reason,
                    role,
                    include_contributing=True,
                )
            )
            items.extend(
                _candidate_evidence(
                    candidate,
                    reason,
                    ProjectionLineageRole.DIAGNOSTIC,
                    include_contributing=False,
                )
            )
            continue
        if disposition.role is PrioritySelectionRole.REJECTED:
            items.extend(
                _candidate_evidence(
                    candidate,
                    reason,
                    ProjectionLineageRole.REJECTED,
                    include_contributing=True,
                )
            )
            items.extend(
                _candidate_evidence(
                    candidate,
                    reason,
                    ProjectionLineageRole.DIAGNOSTIC,
                    include_contributing=False,
                )
            )
            continue
        if disposition.role is PrioritySelectionRole.DIAGNOSTIC:
            items.extend(
                _candidate_evidence(
                    candidate,
                    reason,
                    ProjectionLineageRole.DIAGNOSTIC,
                    include_contributing=True,
                )
            )
            items.extend(
                _candidate_evidence(
                    candidate,
                    reason,
                    ProjectionLineageRole.DIAGNOSTIC,
                    include_contributing=False,
                )
            )
    return tuple(items)


def _aggregate_lineage(
    items: tuple[_LineageItem, ...],
    *,
    fact_value: Decimal | None,
    winner_role: ProjectionLineageRole | None,
) -> tuple[ProjectionLineagePayload, ...]:
    grouped: dict[tuple[UUID, ProjectionLineageRole], list[_LineageItem]] = defaultdict(list)
    for item in items:
        grouped[(item.source_observation_id, item.role)].append(item)

    payloads: list[ProjectionLineagePayload] = []
    for (source_observation_id, role), group in sorted(
        grouped.items(), key=lambda pair: (str(pair[0][0]), pair[0][1].value)
    ):
        values = [item.value for item in group if item.value is not None]
        contribution_value: Decimal | None = None
        if role in {ProjectionLineageRole.SELECTED, ProjectionLineageRole.FALLBACK}:
            if winner_role is not role:
                raise ProjectionContractError("selected and fallback lineage roles cannot be mixed")
            if not values or any(value is None for value in (item.value for item in group)):
                raise ProjectionContractError("winner lineage must contain only contributing evidence")
            contribution_value = sum(values, Decimal("0"))
        elif values:
            raise ProjectionContractError("rejected or diagnostic lineage cannot contribute values")

        reason_codes = sorted({code for item in group for code in item.reason_codes})
        metadata: dict[str, Any] = {
            "evidence": [
                {
                    "evidence_id": str(item.evidence_id),
                    "evidence_kind": item.evidence_kind,
                }
                for item in sorted(group, key=lambda item: (str(item.evidence_id), item.evidence_kind))
            ]
        }
        reason_code = reason_codes[0] if len(reason_codes) == 1 else "multiple_evidence_reasons"
        if len(reason_codes) > 1:
            metadata["reason_codes"] = reason_codes
        first = group[0]
        payloads.append(
            ProjectionLineagePayload(
                source_observation_id=source_observation_id,
                provider_key=first.provider_key,
                role=role,
                granularity=first.granularity,
                contribution_value=contribution_value,
                presence_state=first.presence_state,
                coverage_state=first.coverage_state,
                reason_code=reason_code,
                metadata=metadata,
            )
        )

    if fact_value is not None:
        contributing: list[Decimal] = [
            payload.contribution_value
            for payload in payloads
            if payload.role in {ProjectionLineageRole.SELECTED, ProjectionLineageRole.FALLBACK}
            and payload.contribution_value is not None
        ]
        if not contributing or sum(contributing, Decimal("0")) != fact_value:
            raise ProjectionContractError("lineage contribution sum does not equal fact value")
    elif any(
        payload.role in {ProjectionLineageRole.SELECTED, ProjectionLineageRole.FALLBACK}
        for payload in payloads
    ):
        raise ProjectionContractError("value-less fact cannot have winner lineage")
    return tuple(payloads)


def map_selection_to_fact(selection: PrioritySelection) -> ProjectionFactPayload:
    _validate_candidate_scope(selection)
    candidate = selection.selected_candidate
    winner_role = (
        ProjectionLineageRole(selection.selected_role.value)
        if selection.selected_role is not None
        else None
    )
    if candidate is None:
        unit = canonical_unit(selection.metric_key)
        if unit is None:
            raise ProjectionContractError("selection metric is not canonical")
        value = None
        selected_provider_key = None
        selected_granularity = None
        presence_state = PresenceState.UNKNOWN.value
        coverage_state = CoverageState.UNKNOWN.value
        resolution_state = ResolutionState.UNRESOLVED.value
        lineage_state = LineageState.UNKNOWN.value
    else:
        if candidate.metric_key != selection.metric_key:
            raise ProjectionContractError("winner candidate metric does not match selection")
        if candidate.value is None:
            raise ProjectionContractError("selected candidate must have a numeric value")
        value = candidate.value
        validate_projection_decimal(candidate.value, "fact value")
        unit = candidate.unit
        if unit is None:
            raise ProjectionContractError("selected candidate must have a unit")
        selected_provider_key = candidate.provider_key
        selected_granularity = (
            candidate.selected_granularity.value if candidate.selected_granularity is not None else None
        )
        presence_state = candidate.presence_state.value
        coverage_state = candidate.coverage_state.value
        resolution_state = candidate.resolution_state.value
        lineage_state = candidate.lineage_state.value

    items = _disposition_items(selection, winner=candidate)
    lineage = _aggregate_lineage(items, fact_value=value, winner_role=winner_role)
    if candidate is not None and winner_role is None:
        raise ProjectionContractError("candidate without winner role cannot produce a fact")
    if candidate is not None and not any(
        item.role is winner_role and item.contribution_value is not None for item in lineage
    ):
        raise ProjectionContractError("value-bearing fact requires contributing winner lineage")
    return ProjectionFactPayload(
        metric_key=selection.metric_key,
        value=value,
        unit=unit,
        selected_provider_key=selected_provider_key,
        selected_granularity=selected_granularity,
        presence_state=presence_state,
        coverage_state=coverage_state,
        resolution_state=resolution_state,
        lineage_state=lineage_state,
        diagnostic_metadata=_safe_disposition_metadata(selection),
        lineage=lineage,
    )


def map_build_input_to_facts(
    build_input: DailyProjectionBuildInput,
) -> tuple[ProjectionFactPayload, ...]:
    facts = tuple(map_selection_to_fact(selection) for selection in build_input.selections)
    if tuple(fact.metric_key for fact in facts) != CANONICAL_METRIC_KEYS:
        raise ProjectionContractError("mapped facts must contain exactly the canonical metrics")
    return facts
