"""Generate the settings consumer's TypeScript types from public Pydantic schemas.

Run with the repository's Python 3.15 backend container. No parallel handwritten
wire contracts are maintained in the frontend.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from app.ai.connections_contracts import (
    ProviderConnectionPage,
    ProviderConnectionPut,
    ProviderConnectionView,
)
from app.ai.consents_contracts import (
    CONSENT_POLICY_VERSION,
    AIConsentPage,
    AIConsentPut,
    AIConsentView,
)
from app.ai.preferences import AISettingsPut, AISettingsView
from app.ai.probes_contracts import (
    CONNECTION_PRIVACY_VERSION,
    ConnectionCheckPut,
    ConnectionCheckView,
    ProviderCatalog,
)
from app.ai.routing_contracts import (
    AIRoutingEntryPut,
    AIRoutingEntryView,
    AIRoutingPut,
    AIRoutingView,
)
from app.ai.subscription_authorization_contracts import (
    SUBSCRIPTION_DEVICE_PRIVACY_ACKNOWLEDGEMENT,
    SubscriptionAuthorizationStart,
    SubscriptionAuthorizationView,
    SubscriptionStatus,
)
from app.expert.chat_contracts import (
    AttachmentView,
    ConversationPage,
    ConversationPut,
    ConversationView,
    ExpertSetup,
    RunEvents,
    RunPage,
    RunPut,
    RunView,
)
from app.expert.contracts import NutritionDaysPage, NutritionDaysQuery
from app.expert.preferences_contracts import ExpertPreferencesPut, ExpertPreferencesView


def _type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(value) for value in schema["enum"])
    if "anyOf" in schema:
        return " | ".join(_type(value) for value in schema["anyOf"])
    match schema.get("type"):
        case "string":
            return "string"
        case "integer" | "number":
            return "number"
        case "boolean":
            return "boolean"
        case "null":
            return "null"
        case "array":
            return f"ReadonlyArray<{_type(schema['items'])}>"
        case "object":
            required = schema.get("required", [])
            fields = [f"  {key}{'' if key in required else '?'}: {_type(value)}" for key, value in schema.get("properties", {}).items()]
            return "{\n" + "\n".join(fields) + "\n}"
        case _:
            raise ValueError("Unsupported public schema type")


def generate_typescript() -> str:
    schemas: dict[str, Any] = {}
    for model in (ProviderConnectionPut, ProviderConnectionView, ProviderConnectionPage, ProviderCatalog, ConnectionCheckPut, ConnectionCheckView, AISettingsPut, AISettingsView, AIRoutingEntryPut, AIRoutingEntryView, AIRoutingPut, AIRoutingView, AIConsentPut, AIConsentView, AIConsentPage, SubscriptionAuthorizationStart, SubscriptionAuthorizationView, SubscriptionStatus, NutritionDaysPage, NutritionDaysQuery, ExpertPreferencesPut, ExpertPreferencesView, ExpertSetup, ConversationPut, ConversationView, ConversationPage, AttachmentView, RunPut, RunView, RunPage, RunEvents):
        schema = model.model_json_schema(mode="validation", ref_template="#/$defs/{model}")
        schemas.update(schema.pop("$defs", {}))
        schemas[model.__name__] = schema
    return (
        "// Generated from public backend AI settings and Expert contracts. Do not edit by hand.\n\n"
        + f"export const CONNECTION_PRIVACY_VERSION = {json.dumps(CONNECTION_PRIVACY_VERSION)} as const\n\n"
        + f"export const CONSENT_POLICY_VERSION = {json.dumps(CONSENT_POLICY_VERSION)} as const\n\n"
        + f"export const SUBSCRIPTION_DEVICE_PRIVACY_ACKNOWLEDGEMENT = {json.dumps(SUBSCRIPTION_DEVICE_PRIVACY_ACKNOWLEDGEMENT)} as const\n\n"
        + "\n\n".join(f"export type {name} = {_type(schema)}" for name, schema in sorted(schemas.items()))
        + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate public AI settings TypeScript contracts")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(generate_typescript())
