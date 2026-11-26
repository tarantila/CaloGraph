# Nutrition Expert application contracts

The Expert provides a bounded, account-scoped canonical nutrition-day read and
durable chat/analysis runs. The read endpoint itself performs no model calls and
remains live data rather than a completed all-history assessment. The run service
builds an internal selected-history context and dispatches only after checking the
current account, provider configuration and purpose-specific data permission.

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

## Internal whole-history context

`app.expert.context.ContextBuilder` is an owner-bound database reader. It accepts
the trusted owner and current typed preferences; it has no REST route, provider
client, or model dependency. It reads complete available selected history in
bounded 365-day chunks and fails explicitly if a required chunk is incomplete or
the history exceeds the supported span. Nutrition, weight, and activity summaries
compact older values by calendar year and retain monthly summaries for the latest
ten years. Recent daily details cover 31 account-local days. Older history is
not silently dropped to satisfy a recent-only limit.

Nutrition remains one canonical provider per whole local day. Missing evidence
stays null and explicit zero remains present. Weight follows the configured
per-day scalar provider priority and includes provider provenance. Activity uses
the selected canonical source instance and is not added to other providers.
Historical target comparison uses the effective base target interval without
activity credits; it is not an energy-deficit calculation. Profile and health
notes are read only when their corresponding domains are enabled. Building this
context does not grant permission to send it; the durable run service rechecks the
active account and purpose-specific consent immediately before an outbound send.

## Data-domain preferences

`GET /api/v1/settings/expert` returns the authenticated account's
`data_domains`, `include_meal_names`, and `configuration_version`. If no row
exists, the default enables nutrition, targets, weight, activity, profile, and
health context, with meal names enabled. `PUT` replaces these values and requires
the browser session's CSRF token. Domains are unique and returned in canonical
order; meal names require the nutrition domain. An identical write is a no-op
and preserves the version. A changed write increments it. Inactive accounts
cannot read or change preferences, and administrator status grants no access to
another account's settings. Saving preferences reads no health data and grants
no provider permission.

The account page at `/konto/nutrition-expert` exposes these preferences with
automatic, serialized saves. It also shows the currently selected provider's
permission status. Revocation enumerates every consent page for that owned
connection, revokes each still-unrevoked consent by ID, and verifies the full
list again before confirming success. An incomplete read or remaining consent
does not confirm revocation and leaves the status available for retry. Changing
preferences does not grant or revoke permission and makes no provider request.

Migration `20261114_0047` adds the owner-keyed preferences row. Revision
`20261124_0057` merges that migration branch with the retained consent
revocation branch without changing either migration's schema operations.

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

This read-only GET has no mutating POST or idempotency-key semantics. Durable
run writes use the UUID-addressed PUT resources described below, with explicit
replay and cancellation contracts. Future delegated access must
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
protocol supports one-shot generation and discovery; native tool definitions and
streaming run events require their own typed contracts before those features are
exposed. Data permissions have the separate contracts described below.

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
  Deleted connection UUIDs are retained as retired and cannot be reused. A new
  connection needs a new UUID, preventing delayed permission requests from
  binding to another destination after deletion.

Mutations require the browser session's `X-CSRF-Token` and pass existing origin
validation. Unknown input fields, including `user_id`, are rejected. Errors use
`application/problem+json`; invalid values and secrets are not echoed. Explicit
operation IDs are `ai_list_connections`, `ai_get_connection`, `ai_put_connection`
and `ai_delete_connection` under the `AI providers` tag.

Configuration contains a label (1–100 characters), provider kind, optional model ID
(1–200 characters when set), optional custom base URL, enabled flag and timeout (5–300
seconds, default 60). There are at most 20 connections per account. Provider kind
is immutable; create another connection to change it. OpenAI API, Anthropic and
Gemini require a key when enabled. Disabled drafts may omit the model and key so
model discovery can precede selection. Enabled configurations require a model;
the database enforces this invariant as well. Migration 0045 preserves existing
configurations. Its downgrade refuses model-less drafts before changing the
schema rather than deleting them or inventing model IDs. Local and gateway
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

`GET /api/v1/settings/ai/routing` returns the account's ordered connection list,
enabled state, readiness and current configuration version. Before the first
routing write it projects the legacy active connection as the first enabled entry
without persisting anything. New connections are appended disabled after routing
has been saved. `PUT /api/v1/settings/ai/routing` replaces the complete list and
requires `expected_configuration_version`; stale writes return 409. Identical
retries at the immediately preceding version are idempotent. Routing updates make
no provider requests and do not change consent. The first enabled entry is the
compatibility active-provider selection only while it is ready; otherwise the
active selection is null rather than promoting another connection. No automatic
request fallback is performed. Deleting a connection invalidates the routing
version.

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

## Bounded provider request transport

`app.ai.generation` provides internal one-shot adapters for native API providers
and operator-approved compatible endpoints. It uses the same destination checks,
DNS pinning and TLS validation as metadata discovery. Request and response bodies
are capped at 256 KiB by default, and generation has an absolute transport budget
of at most 60 seconds. Only a generation request that actually contains validated
inline media can opt into the 16 MiB absolute request ceiling; response bodies
remain capped at 256 KiB. Native destinations are fixed; custom destinations
require the existing exact operator allowlist.

The transport sends each request once, without redirects, implicit retries or
fallback. It normalizes finish reasons and available token usage and emits safe
error classifications without retaining provider error bodies or credentials.
Request options follow each adapter's protocol; unsupported cross-provider
options are not forwarded.

This is an internal transport, not a generic public generation endpoint or a
data-sharing authorization service. The durable Expert run service checks account
state and current purpose-bound permission immediately before dispatch. Stored
configuration, metadata checks and category preferences alone do not authorize a
request.

## Versioned data permissions

`/api/v1/settings/ai/consents` records explicit, immutable permission for a
Nutrition Expert data transfer. Configuration, metadata checks and choosing an
active connection do not create this permission. The resource performs no health
data read, credential decryption or provider request.

- `PUT /{id}` creates a grant with a client-generated UUID (201). Replaying the
  same ID and payload returns its current state (200), including revoked or stale
  state. Reusing the ID with a different payload returns 409.
- `GET /{id}` reads an account-local grant. `GET` lists up to 50 grants in UUID
  order with an exclusive `after` cursor and optional `connection_id` filter.
- `DELETE /{id}` idempotently revokes an account-local grant (204). Revocation is
  terminal; retrying an earlier creation request cannot restore permission.
  Unknown IDs are retained as invalidated, so delayed creation returns 409.

The snapshot binds the authenticated account, provider connection and current
configuration version, provider kind, selected model, destination, policy version
and purpose (`nutrition_chat` or `nutrition_check_in`). Data domains are explicit:
nutrition, targets, weight, activity, profile and health context. Including meal
names requires separate permission and the nutrition domain. History is either
all available history or an inclusive, ordered date range. Unknown fields and
ambiguous scopes are rejected. All mutations require CSRF protection.

Only enabled connections with a selected model can receive new grants. Changed
connection settings make old grants inapplicable. At most 200 current grants are
allowed per account; revoked or stale records do not consume that allowance.
Account and connection deletion cascade through the composite owner/connection
foreign key. Before connection deletion, its permission IDs are invalidated in
the same transaction. Retained invalidations survive connection deletion;
replaying an old ID returns 409 even if the old resource has been removed. A new
explicit permission needs a new UUID. Administrator status grants no cross-account access.

Migration 0056 backfills existing revocations into the account-local invalidation
table. It also retains deleted connection UUIDs, preventing their reuse. These
records contain only account/resource IDs and timestamps. Downgrade is refused
while either kind of invalidation exists. Account deletion removes these records.

`AIConsentService.require_usable` validates a server-constructed `ConsentDataUse`
against current state, purpose, domains, meal-name permission and history bounds,
and rejects future data-use dates in the account timezone. The durable run
service authorizes again immediately before an outbound send under the account
operation lock; reading a grant alone does not serialize later provider traffic.

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

## Durable conversation and run resources

All routes bind the session owner; administrator status does not widen access.
Mutations require CSRF. An account may keep at most 200 conversations and 200
turns per conversation, with one queued/running turn per account.

- `GET /api/v1/expert/setup?mode=chat|analysis` returns the active configuration,
  preferences, matching purpose-specific permission and adapter availability.
  `include_attachments=true` requests a matching document grant; it does not
  grant permission or disclose file metadata. The response also reports the
  routing version and each configured candidate's own applicable consent ID.
- `PUT /api/v1/expert/conversations/{uuid}` creates (201) or replays (200) an
  immutable chat/analysis conversation. GET lists/reads; DELETE removes its turns.
  Consumed UUIDs remain reserved after deletion, including IDs reserved by an
  idempotent delete of an unknown conversation. Views include a nullable
  generated topic title of at most 80 characters. The title is requested inline
  with the first successful chat answer; a missing or invalid title does not
  invalidate that answer. Analysis conversations remain untitled. Historical
  first-message excerpts remain stored but are hidden because their provenance
  is unknown.
- `PUT /api/v1/expert/conversations/{uuid}/turns/{run_uuid}` binds the message,
  request kind, active connection/version, preference version and consent ID.
  Identical retries return current state without scheduling another execution;
  `attachment_ids` may name at most three already uploaded files owned by that
  conversation. An optional `routing_configuration_version` opts into the
  current ordered provider route. Every enabled candidate is snapshotted with
  its configuration and own consent ID; candidates without a matching all-history
  grant are recorded as skipped. Legacy requests that omit this field keep their
  original single-provider behavior and replay identity. Changed input returns
  409. Run UUIDs remain reserved after conversation deletion. Run views may return
  safe attachment metadata and provider attempt provenance (provider, model,
  connection ID and safe status only).
- `GET /api/v1/expert/runs/{uuid}` reads queued, running, succeeded, failed,
  cancelled or interrupted state. `/events` returns bounded typed state events.
  Reconnection and polling never trigger generation.
- `PUT /api/v1/expert/runs/{uuid}/cancel` cancels an owned pending run. Before the
  committed dispatch boundary this prevents sending. Afterwards it discards the
  result, but cannot retract data already sent. Conversation/account deletion also
  prevents late completion from being stored.
- Conversation and turn lists use paired aware timestamp/UUID cursors with limits
  of 1–50. Responses expose no context payloads, credentials, title-source
  metadata or owner arguments.

A worker claims once, builds the complete selected history in a PostgreSQL
repeatable-read snapshot, closes that session, then rechecks the live account,
configuration, category version and individual purpose-bound consent before
committing each provider attempt for dispatch. The commit is the once-only
dispatch boundary for that candidate. No database transaction remains open
during provider I/O. An opted-in route advances only after a DNS/connect failure
known to precede request bytes or an explicit HTTP 429/503 response. TLS or
destination-policy failures, authentication errors, ambiguous transport failures,
invalid output and completed/refused responses never trigger another candidate.
A request admitted before later revocation or cancellation cannot be recalled
from a provider; its result will not be stored after cancellation or account
deletion. Lost workers become interrupted after ten minutes and are never
automatically retried.

Identity receipts retain only an account ID, resource UUID and timestamp. They
survive conversation/connection deletion and are removed on account deletion. The
complete `0047` preference, `0056` consent/connection-retention and `0057` merge
lineages are preserved; `0058` merges the conversation schema and `0059` adds
identity receipts while backfilling any pre-existing conversations and runs.
Revision `0062` adds nullable generated-title provenance without rewriting legacy
title values; downgrade is refused while generated-title provenance remains.
Revision `0064` adds optional per-run route snapshots and durable one-time
provider attempts; downgrade is refused while opt-in routing snapshots or attempts
remain.

The model receives bounded untrusted data and no executable tools. Responses must
match the closed `ExpertAnswer` schema and reference known evidence for findings.
Invalid responses and credential echoes fail without storing provider error bodies.

### Private document and image attachments

`PUT /api/v1/expert/conversations/{conversation_id}/attachments/{attachment_id}`
uploads a raw PDF, PNG, JPEG or WebP body using the corresponding content type and
`X-Attachment-Filename` header. `GET .../{conversation_id}/attachments` lists
unused drafts and `DELETE .../{conversation_id}/attachments/{attachment_id}`
deletes an unused file or reserves an unknown UUID. Uploads require session and
CSRF validation. The request stream is bounded to 4 MiB/file and 20 seconds; at
most four upload slots are held per backend process. Account-locked database
quota checks run after the body arrives, so locks and transactions do not span
untrusted client uploads. The database enforces at most three files and 8 MiB per
conversation and 64 MiB per account under the account operation lock, including
uploads handled by separate workers. Draft listing includes files referenced by
failed or cancelled runs so they can be explicitly selected again. File deletion
is blocked for every run-referenced attachment; those files are removed with the
conversation.

The service checks the declared MIME type and a small file signature only. It
does not parse, execute, OCR, fetch URLs from, or otherwise inspect file contents
on the host. Bytes stay private and are not returned by metadata routes. The
dispatch boundary requires a current consent grant with `include_attachments`;
legacy grants default to false. Only files explicitly attached to the current
turn and files from earlier successful document turns are sent. Failed or
cancelled turns remain drafts and require explicit resubmission. The provider
receives inline base64 media through its native or compatible request format;
the existing destination, DNS pinning, TLS, redirect and bounded-response
controls remain in force. Uploaded content is untrusted; external claims are
referenced as `attachment:<uuid>`, never treated as canonical CaloGraph data.

Attachment UUIDs are immutable per account. Upload consumes the UUID; deleting a
file or conversation retains a minimal owner-scoped identity receipt, and a late
upload cannot recreate it. Receipts contain only the UUID and timestamp, cascade
on account deletion, and do not expose names, hashes or health content.
