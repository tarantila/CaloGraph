"""Offline semantic-evaluation seam, not a claim of live model quality.

A future model-output adapter can map validated findings to this assertion
format. These fixtures exercise the evaluator with a scripted provider today;
they do not evaluate unstructured prose or replace canonical reader tests.
"""

import json
from dataclasses import dataclass
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.ai.contracts import AIMessage, FinishReason, GenerationRequest, GenerationResult
from app.ai.fake import FakeProvider

Assertion = Literal[
    "weekend_intake_higher", "protein_stable", "weight_decreasing", "plateau",
    "causal_link", "low_nutrient_coverage", "deficiency", "missing_is_zero",
    "fiber_missing", "lower_provider_fiber",
]


class EvaluatedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assertion: Assertion
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=4)


class EvaluatedOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: tuple[EvaluatedFinding, ...] = Field(max_length=10)


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    synthetic_context: str
    available_evidence: frozenset[str]
    required: frozenset[Assertion]
    forbidden: frozenset[Assertion]
    good: Assertion
    bad: Assertion


CASES = (
    EvaluationCase(
        name="weekends_without_plateau",
        synthetic_context=(
            "Synthetic periods: weekdays 2026-10-05..09 average 1800 kcal; weekend "
            "2026-10-10..11 average 2400 kcal. Protein 120 g on all seven days. "
            "Actual weights: 80 kg on 2026-10-05 and 79.5 kg on 2026-10-11. "
            "Evidence weekend-comparison covers both dated periods."
        ),
        available_evidence=frozenset({"weekend-comparison"}),
        required=frozenset(), forbidden=frozenset({"plateau", "causal_link"}),
        good="weekend_intake_higher", bad="plateau",
    ),
    EvaluationCase(
        name="low_micronutrient_coverage",
        synthetic_context=(
            "Synthetic period 2026-10-01..10: iron present on 2 of 10 canonical nutrition "
            "days, absent on 8; recorded mean is 5 mg over the 2 present days. "
            "Evidence nutrient-coverage contains n=2, denominator=10 and coverage=20%."
        ),
        available_evidence=frozenset({"nutrient-coverage"}),
        required=frozenset({"low_nutrient_coverage"}),
        forbidden=frozenset({"deficiency", "missing_is_zero"}),
        good="low_nutrient_coverage", bad="deficiency",
    ),
    EvaluationCase(
        name="whole_day_provider",
        synthetic_context=(
            "Synthetic day 2026-10-01: canonical winner apple_health; fiber is null. "
            "A lower-priority provider must not contribute. Evidence day-fiber is "
            "the canonical missing-state assertion, not a numeric measurement."
        ),
        available_evidence=frozenset({"day-fiber"}),
        required=frozenset({"fiber_missing"}),
        forbidden=frozenset({"lower_provider_fiber", "missing_is_zero"}),
        good="fiber_missing", bad="lower_provider_fiber",
    ),
)


def evaluate(case: EvaluationCase, output: EvaluatedOutput) -> tuple[str, ...]:
    assertions = {finding.assertion for finding in output.findings}
    failures = [f"missing:{item}" for item in sorted(case.required - assertions)]
    failures.extend(f"forbidden:{item}" for item in sorted(case.forbidden & assertions))
    for finding in output.findings:
        if not set(finding.evidence_refs) <= case.available_evidence:
            failures.append("unknown_evidence")
    return tuple(failures)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
@pytest.mark.asyncio
async def test_offline_semantic_cases_accept_grounded_and_reject_unsafe_findings(case: EvaluationCase) -> None:
    evidence = sorted(case.available_evidence)[0]
    fake = FakeProvider([
        GenerationResult(
            model_id="fake-model", finish_reason=FinishReason.SUCCESS,
            content=json.dumps({"findings": [{"assertion": assertion, "evidence_refs": [evidence]}]}),
        )
        for assertion in (case.good, case.bad)
    ])
    request = GenerationRequest(
        model_id="fake-model", system_instruction="Use supplied synthetic evidence only.",
        messages=(AIMessage(role="user", content=case.synthetic_context),),
    )
    good = EvaluatedOutput.model_validate_json((await fake.generate(request)).content)
    bad = EvaluatedOutput.model_validate_json((await fake.generate(request)).content)
    assert evaluate(case, good) == ()
    assert f"forbidden:{case.bad}" in evaluate(case, bad)
    forged = EvaluatedOutput(findings=(EvaluatedFinding(assertion=case.good, evidence_refs=("invented",)),))
    assert "unknown_evidence" in evaluate(case, forged)
