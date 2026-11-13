# Nutrition Expert application contracts

The first Expert capability is a bounded, account-scoped canonical nutrition
day read. That read performs no model calls, stores no credentials, and does not yet
implement check-ins, historical target evaluation, or complete-history snapshots.

## Shared service boundary

`app.expert.service.NutritionExpertReader` binds the authenticated owner at the
adapter boundary. `get_nutrition_days(NutritionDaysQuery)` returns the same typed
application result to REST or a later curated internal tool adapter. It does
not call the REST API, accept a caller-selected account, or implement another
provider resolver. There is no MCP runtime dependency or MCP endpoint.

The service composes the existing canonical source selection and nutrition
reader. One provider owns each whole nutrition day. Missing secondary values
remain null; a lower-priority provider cannot fill them. Explicit zero remains
present evidence. Conflicts fail the read instead of returning a partial page.
The read excludes meal names and profile information, and skips meal-detail
loading entirely. It never imports, projects, or changes nutrition evidence.

## REST contract

`GET /api/v1/expert/nutrition-days` uses operation ID
`expert_get_nutrition_days` and tag `Nutrition Expert`.

- `start` and `end` are required inclusive account-local dates, spanning 1–365
  calendar days. They are not UTC timestamps.
- `page_size` is 1–31, default 31. Calendar days without usable data are included.
- `after` is the exclusive date cursor returned as `next_after`; retain the
  original range. It must fall inside that range before its end.
- `next_after` is required and null on the last page. There are at most 31 day
  records and 217 numeric evidence records per response.
- Every metric field is required. Null means absent or unusable evidence.
  Decimal values serialize as strings, including explicit zero. Scale or
  exponent notation may be retained; parse these as decimal values.
  A present value is not proof of a complete food diary.
- `provider_key` names the actual daily winner, not just the preference head.
- Evidence IDs are references within this read, not stable snapshot identifiers.
  Evidence is emitted only for present values, including zero.
- Pagination reads live data. It does not guarantee one MVCC snapshot across
  requests and must not be represented as a completed all-history assessment.
- Browser-session authentication is required. Unknown query parameters,
  including `user_id` and `source`, are rejected.
- 401, 409, 422 and 503 return documented `application/problem+json` envelopes.
  Provider payloads and invalid input values are not echoed.

This read-only GET has no mutating POST or idempotency-key semantics. Future
run-creation POSTs require explicit idempotency and reconnect/cancellation
contracts; they are not part of this capability. Future delegated access must
derive identity and scopes from its own authentication adapter, not reuse a
browser cookie or add an account argument to a tool.

## Provider contracts

`app.ai.contracts` defines typed, bounded requests, results, model capabilities,
usage and safe error classifications independently of any remote SDK. Unknown
limits and token usage remain unknown. ChatGPT-plan and OpenAI-API access are
distinct provider kinds. Merely listing a kind does not claim an implemented
or available adapter.

`FakeProvider` is dependency-injected for tests only. It is not registered in
the API, performs no network I/O, and does not retain prompts. The current
protocol supports one-shot generation and discovery; native tool definitions,
streaming run events and consent resources require their own
typed contracts before those features are exposed.

## Provider connection settings

`app.ai.connections.ProviderConnectionService` manages account-bound configuration
independently of the nutrition reader and provider SDKs. REST binds the account
from its authenticated session; application callers supply the trusted owner.
Administrator status grants no access to another account's connections.

The `/api/v1/settings/ai/connections` resource supports these operations:

- `GET` lists up to 20 connections, ordered by UUID. `limit` is 1–20 and `after`
  is the exclusive UUID cursor. `next_after` is required and null on the last
  page. Pagination reflects live configuration.
- `GET /{connection_id}` reads one account-local configuration.
- `PUT /{connection_id}` creates (201) or replaces (200) configuration using a
  client-generated UUID. The same UUID may exist independently in two accounts.
  Identical retries retain timestamps, credential ciphertext and configuration
  version. Meaningful changes increment the version; this is not a conditional
  update token. Concurrent mutations fail with a lifecycle conflict rather than
  bypassing the shared account-operation lock.
- `DELETE /{connection_id}` removes configuration and credentials, returning 204
  even when already absent. It does not revoke a key at the external provider.

Mutations require the browser session's `X-CSRF-Token` and pass existing origin
validation. Unknown input fields, including `user_id`, are rejected. Errors use
`application/problem+json`; invalid values and secrets are not echoed. Explicit
operation IDs are `ai_list_connections`, `ai_get_connection`, `ai_put_connection`
and `ai_delete_connection` under the `AI providers` tag.

Configuration contains a label (1–100 characters), provider kind, model ID
(1–200 characters), optional custom base URL, enabled flag and timeout (5–300
seconds, default 60). There are at most 20 connections per account. Provider kind
is immutable; create another connection to change it. OpenAI API, Anthropic and
Gemini require a key when enabled. Disabled drafts may omit it. Local and gateway
providers require an HTTP(S) base URL and may omit a key. Native providers do not
accept custom URLs. URLs containing userinfo, query or fragment are rejected.

`api_key` is write-only: omission preserves the stored key, null removes it and a
non-empty string replaces it. Keys have a maximum of 8,192 characters and cannot
contain whitespace at the edges or control characters. Changing a custom
destination while retaining a key requires explicitly supplying the replacement
key or null. Credentials use the existing Fernet encryption configuration and a
separate table with an account-and-connection foreign key. Reads return only
`has_credential`, which indicates stored presence rather than decryptability or
successful connectivity. They never decrypt credentials. Missing encryption
configuration rejects a new key with 503 before changing configuration. Account
deletion cascades to both tables. Data export excludes these integration settings
and credentials; demo reset/removal refuses accounts with added connections.

Saving or enabling configuration makes no network request and grants no consent
to transmit nutrition or health data. Stored local/loopback URLs are configuration
only; a future outbound adapter must enforce its destination policy before use.
ChatGPT-plan access remains a distinct mandatory v1 provider and requires its
supported authentication flow; API keys cannot configure it through this resource.

## Explicit connection checks and destination policy

`GET /api/v1/settings/ai/providers` describes settings availability, authentication
and model-discovery support. Unknown generation/streaming/tool/schema capabilities
remain null. ChatGPT-plan is explicitly planned, separate from API-key providers.

`PUT /api/v1/settings/ai/connections/{connection_id}/checks/{check_id}` requires
CSRF, the current `configuration_version` and explicit acknowledgement of
`connection-metadata-2026-11-13`. It sends credentials and technical request metadata
to retrieve the model list, without a prompt or any nutrition/health data. This
acknowledgement is recorded with the check, owner, version and timestamp; it does
not authorize future analysis requests or health-data transmission.

The account-scoped client UUID identifies one request. Its claim is committed
before network I/O and identical retries replay the result for at least 24 hours.
A conflicting payload returns 409. An incomplete claim becomes `interrupted`
after five minutes and is never automatically re-executed. New checks require a
new UUID. Checks are limited to 10/minute and 100 retained entries per account;
entries older than 24h are removed when a new check starts. Mutations retain the
exclusive account-operation lock during the bounded check. Account/connection
deletion cascades to check history.

`GET /api/v1/settings/ai/connections/{connection_id}/checks/latest` returns the
latest check for the current configuration, or null. Configuration changes make
old results inapplicable. A successful model-list request is metadata evidence,
not a generation test. Results contain at most 100 model IDs, a truncation flag,
and nullable selected-model availability. Partial lists do not prove that an
unlisted model is unavailable. Local/gateway 404/405 model-list responses allow
manual selection without claiming verified authentication or generation.

OpenAI API, Anthropic and Gemini use fixed HTTPS model-list endpoints. Custom
local/OpenAI-compatible base URLs require an exact operator-controlled
`AI_ALLOWED_ENDPOINTS` entry (comma-separated base URLs). Saving configuration
does not grant that permission. Only explicitly approved local destinations may
use HTTP; credentials over HTTP remain visible to that endpoint/network. Never
add an internal administrative service or a metadata endpoint to this allowlist.
Model IDs are transmitted as metadata only when needed; they are never URL paths
selected by a model or browser input in these checks.

The transport validates all DNS answers and pins a validated numeric destination
while retaining the original TLS hostname/SNI and certificate verification. Native
destinations require global addresses. Approved custom endpoints may additionally
use RFC1918, loopback or ULA addresses. Link-local/metadata, multicast, unspecified
and reserved destinations are blocked. Redirects, environment proxies and retries
are disabled. DNS resources, total time and response bytes are bounded; compressed
responses are rejected. Checks use a maximum 30-second deadline and a 256-KiB
response ceiling. Errors never expose provider bodies, headers, credentials or
raw network exception text. Only normalized bounded model metadata is persisted.

Endpoint references: [OpenAI models](https://developers.openai.com/api/reference/resources/models/methods/list),
[Anthropic models](https://platform.claude.com/docs/en/api/models/list),
[Gemini models](https://aistudio.google.com/api-reference/models).

## ChatGPT-plan authentication track

As checked on 2026-11-13, the official
[plan-usage guide](https://developers.openai.com/siwc/token-sharing-open-source)
documents dynamic registration for open-source/local applications and directs
paid or remotely hosted applications to an interest form. The
[self-hosted VM guide](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)
requires completing OAuth locally and securely transferring the selected grant
to a VM with its own stable host ID. A browser's loopback callback reaches the
browser machine. The [client-ID guide](https://developers.openai.com/siwc/request-client-id)
describes selected-partner access.

These pages establish an official integration direction, but do not settle
CaloGraph's centrally hosted multi-account browser authorization flow or its
client eligibility. Official access/flow confirmation is still required before
implementing that adapter. Keep per-account/per-workspace grants, PKCE/state/nonce,
serialized refresh rotation and revocation as separate authentication contracts.
No private ChatGPT backend endpoint, browser cookie or existing tool credential
store is used. ChatGPT-plan remains required for v1; this unresolved authentication
boundary does not prevent independent provider-settings implementation.

## OpenAPI validation

`tests/test_expert_openapi.py` validates the complete generated OpenAPI document,
checks operation ID uniqueness and compares the Expert paths plus transitively
referenced schemas with `tests/fixtures/expert_openapi.json`. These tests run in
the ordinary backend CI suite. A contract change requires reviewing and updating
that fixture; it must not be regenerated implicitly by tests.
`tests/test_ai_connections_openapi.py` applies the same contract-drift guard to
provider settings with `tests/fixtures/ai_connections_openapi.json`.

The existing conditional `/api/openapi.json` exposure is retained. Public
response schemas contain no credential models or secret values. The provider
settings request explicitly marks its credential input write-only. New contracts forbid
unknown fields and define bounds and nullable values explicitly.

Generated TypeScript types from this OpenAPI contract are preferable when the
first Expert frontend consumer is implemented. No parallel handwritten Expert
TypeScript schema or client generator is introduced before that consumer
exists. Transport wrappers for authentication, CSRF and errors remain necessary
even with generated types.

## Offline quality evaluations

`tests/test_expert_evaluations.py` provides synthetic scenarios and deterministic
expectations for weekend differences without invented plateaus, low
micronutrient coverage without deficiency diagnoses, and missing fiber without
provider blending. It checks a bounded semantic assertion format against allowed
evidence and required/forbidden findings using the Fake Provider.

These tests validate the evaluator and fixtures, not real model reasoning or
unstructured prose. A later adapter must map actual validated model output into
the same expectations. Live-provider evaluations remain separate, optional,
credential-gated and synthetic-only. Canonical reader regressions independently
verify the underlying values, missingness and ownership.
