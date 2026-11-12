"""Validate the complete document and snapshot the new public contract only."""

import json
from pathlib import Path

from openapi_spec_validator import validate

from app.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "expert_openapi.json"
METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def expert_contract() -> dict:
    schema = app.openapi()
    paths = {path: item for path, item in schema["paths"].items() if path.startswith("/api/v1/expert/")}
    names: set[str] = set()
    pending = [paths]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            reference = item.get("$ref", "")
            if reference.startswith("#/components/schemas/"):
                name = reference.rsplit("/", 1)[1]
                if name not in names:
                    names.add(name)
                    pending.append(schema["components"]["schemas"][name])
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return {
        "paths": paths,
        "schemas": {name: schema["components"]["schemas"][name] for name in sorted(names)},
        "securitySchemes": {
            "CaloGraphSession": schema["components"]["securitySchemes"]["CaloGraphSession"],
        },
    }


def test_complete_openapi_document_validates_and_operation_ids_are_unique() -> None:
    schema = app.openapi()
    validate(schema)
    ids = [operation["operationId"] for path in schema["paths"].values()
           for method, operation in path.items() if method in METHODS]
    assert len(ids) == len(set(ids))


def test_expert_openapi_contract_has_no_accidental_drift() -> None:
    assert expert_contract() == json.loads(FIXTURE.read_text())


def test_expert_documents_explicit_operation_nullable_fields_and_problem_media_type() -> None:
    contract = expert_contract()
    operation = contract["paths"]["/api/v1/expert/nutrition-days"]["get"]
    assert operation["operationId"] == "expert_get_nutrition_days"
    assert operation["tags"] == ["Nutrition Expert"]
    assert operation["security"] == [{"CaloGraphSession": []}]
    assert contract["securitySchemes"]["CaloGraphSession"]["in"] == "cookie"
    assert not any(parameter["name"] in {"user_id", "source"} for parameter in operation["parameters"])
    for code in ("401", "409", "422", "503"):
        assert set(operation["responses"][code]["content"]) == {"application/problem+json"}
    values = contract["schemas"]["NutritionValues"]
    assert "fiber_g" in values["required"]
    assert {"type": "null"} in values["properties"]["fiber_g"]["anyOf"]
    assert not any("credential" in key.lower() or "token" in key.lower() for key in contract["schemas"])
