import json
from pathlib import Path

from app.main import app

FIXTURE = Path(__file__).parent / "fixtures" / "ai_connections_openapi.json"


def connection_contract() -> dict:
    schema = app.openapi()
    paths = {key: value for key, value in schema["paths"].items() if key.startswith("/api/v1/settings/ai/")}
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
    return {"paths": paths, "schemas": {key: schema["components"]["schemas"][key] for key in sorted(names)}}


def test_connection_openapi_contract_has_no_accidental_drift() -> None:
    assert connection_contract() == json.loads(FIXTURE.read_text())


def test_connection_public_response_has_no_secret_fields_and_write_is_documented() -> None:
    contract = connection_contract()
    request = contract["schemas"]["ProviderConnectionPut"]
    assert request["properties"]["api_key"]["writeOnly"] is True
    response = contract["schemas"]["ProviderConnectionView"]
    assert not {"api_key", "encrypted_api_key", "user_id"} & set(response["properties"])
    assert all("Credential" not in key for key in contract["schemas"])
    operations = [operation for path in contract["paths"].values() for operation in path.values()]
    assert all(item["security"] == [{"CaloGraphSession": []}] for item in operations)
    ids = {item["operationId"] for item in operations}
    assert ids == {"ai_list_connections", "ai_get_connection", "ai_put_connection", "ai_delete_connection"}
    mutation = contract["paths"]["/api/v1/settings/ai/connections/{connection_id}"]["put"]
    assert "Idempotently" in mutation["description"]
    assert "201" in mutation["responses"]
