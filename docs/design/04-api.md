# 4. OpenAPI 3.1 sketch and conventions

**Status:** proposed local profile `platform-api-design/0.1.0`, not organization-approved. [OpenAPI artifact](openapi.yaml). [Index](README.md).

## Standards baseline and explicit review items

Use OpenAPI **3.1.1**, JSON Schema 2020-12 semantics, and design API version **0.2.0**. These are distinct versions. The organization-approved Zalando revision and lint profile have not been supplied. The public guidelines were consulted on 2026-09-05; their changelog includes 2026-03-16 changes, but that date is not a complete immutable revision. V01 must record the approved commit and local rule profile before contract freeze. [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html), [Zalando guidelines](https://opensource.zalando.com/restful-api-guidelines/).

Local proposals: plural kebab-case paths, snake_case fields, opaque IDs, absolute same-origin links, JSON objects, cursor pagination, bearer authentication, `application/problem+json`, and no path version prefix. Begin with `application/json`; compatible additions are preferred. If simultaneous incompatible representations become necessary, review media-type versioning before adding them.

| Review item | Proposed decision | Why it needs explicit treatment |
| --- | --- | --- |
| Async creation | `202` with the durable execution as operation/status resource | Zalando async guidance recommends a separate job and `201` initiation in its example; this domain already is an execution operation |
| Enum spelling | Preserve the source's lowercase states/error codes | Deliberate local choice against the guideline's uppercase recommendation |
| Governed input | Reject unknown execution-input fields | Security/admission needs exact allowed input; record exception to permissive input-evolution guidance where applicable |
| Response evolution | Open objects and example values for output state strings; clients tolerate unknown values | Closed request schemas do not imply closed response contracts |
| Flow correlation | Validate or generate `X-Flow-ID`, also generate a per-request ID; retain W3C context | Execution ID is server-generated and independent of caller correlation |
| Permission vocabulary | Dot-separated registry permissions, represented as required roles on the bearer security requirement | Proposed deviation from Zalando scope naming; these are not invented Entra OAuth scopes and never replace object checks |

These are proposed conventions, not a claim the sketch passes the enterprise linter. ADR-0002 records them. The executable documentation profile is `platform-api-design/0.1.0` in [redocly.yaml](redocly.yaml); its structure/examples rules do not stand in for the unknown enterprise guideline profile.

## Active API surface

M1 exercises every active route using the fake and fixture artifacts. M3 replaces fixture transfer with authorized object-store transfer; it adds no provider fields.

| Method and route | Behavior | Permission |
| --- | --- | --- |
| GET `/identity-context` | Current principal, authorized app/environment choices, effective permissions | Valid API token; return only own context |
| GET `/execution-templates` | Authorized immutable template versions and allowed input descriptions | `catalog.read` in caller scope |
| POST `/executions` | Expand immutable template defaults, validate complete resolved spec; commit admission | `execution.submit` |
| POST `/execution-templates/{template_id}/executions` | Same semantics; path and body template IDs must agree | Same submission permission; same canonical operation for idempotency |
| GET `/executions/{execution_id}` | Current normalized projection and independent outcome fields | `execution.read` on object |
| POST `/executions/{execution_id}/cancellations` | Persist eventual stop intent | `execution.cancel` on object |
| GET `/executions/{execution_id}/events` | Ordered, cursor-paginated JSON events with bounded long-poll | Object read; current permission checked before return |
| GET `/executions/{execution_id}/logs` | Sanitized bounded log pages, published through the platform | Object read plus data-read permission |
| GET `/executions/{execution_id}/results` | Result metadata, exit code if known, authorized portable artifact references | Same data checks |
| GET `/executions/{execution_id}/artifacts/{artifact_id}` | Authenticated bounded byte stream; metadata lists expected type/digest | Artifact must belong to this execution and permitted classification |

Private `/livez` and `/readyz` are probe routes on a separate administrative listener, not public product endpoints. No authentication details, connection strings or diagnostics in probe bodies. No execution collection listing is required for the first slice; clients persist their submission ID.

## Submission and cancellation semantics

Successful submission commits spec, owner, decisions, execution, idempotency response, initial event and outbox in one PostgreSQL transaction. `202` means accepted durably, not that Temporal or compute has started. A database failure returns `503` and cannot return acceptance. A recoverable Temporal outage may leave accepted work pending; admission shuts when the configured backlog/deadline budget is exhausted (429 for caller limits, 503 for service overload). Set `Location`, `Retry-After`, `X-Request-ID`, `X-Flow-ID`, `ETag`, and `Cache-Control: no-store` as applicable.

Idempotency requires a 16–128 character key with bounded allowed characters on both POST kinds. Scope by tenant, principal, authorized app/environment, operation kind and parent ID. Normalize alias-route input, reject duplicate JSON keys, and hash canonical caller JSON, excluding tokens/trace IDs. Omission and an explicit default are distinct caller payloads under the same key; object-key order and alias-route spelling are not. Look up that fingerprint before rechecking mutable catalog availability, reauthorizing access to the original object. For a new key, expand immutable template defaults and validate the resolved spec. Persist caller and resolved-spec hashes separately. An identical authorized retry returns the original `202` representation and Location even if live status or catalog eligibility advanced; a changed caller payload returns `409 idempotency_conflict`. Concurrent duplicates converge through the unique constraint, not an in-memory cache.

Store only durably accepted 202 outcomes under the key, including cancellation acceptance. Pre-admission 400/401/403/404/409/410/413/415/429/503 and other 5xx responses are not cached: a later retry reauthenticates and reevaluates current policy. A conflict cannot overwrite an existing accepted mapping. If commit acknowledgement is lost, retry the same key and exact caller payload to discover any committed acceptance before attempting new work. Replayed acceptance does not guarantee a revoked execution will dispatch. This policy avoids persisting a denial across corrected authorization or policy while preserving accepted intent.

Proposed retention: retain response/key mapping until at least 90 days after terminal workload **and** resolved cleanup; never expire keys for active or uncertain executions. After the documented expiry a reused key is a new request. Historical replay within retention does not depend on Temporal history. Reject token/key floods with per-principal admission limits. Keys are hashed at rest and omitted from logs.

Cancellation takes an optional enumerated reason, not sensitive free text. Duplicate intents converge to the same cancellation object; repeat the original idempotent response. New requests against already terminal executions return `409 execution_terminal`. `202 requested` does not imply stop, rollback, or removal. A racing completion may set `superseded`; inspect execution and cleanup status. The cancellation outbox signals only after its durable intent exists.

An absent cancellation body normalizes to `{}`; retain the idempotency key for durable retries. Preserve the supplied reason in the cancellation representation. A CI cleanup handler may treat `execution_terminal` as “no new cancellation needed,” but must still inspect outcome and cleanup; it is not proof the job succeeded. Clients persist the submission ID/key/payload. If the ID is lost, replay that exact submission within key retention to recover its Location; no collection-list endpoint is added for this slice.

## Reading, concurrency, pagination, and limits

Use long-poll events (`wait_seconds` 0–25, default 0), not SSE in v1. This avoids persistent streaming requirements for the proposed enterprise ingress. Empty timeout returns `200` with an empty `items` list and unchanged cursor; disconnect does not cancel an execution. Recheck access after waiting. Event history has explicit retention; an expired cursor returns `410 cursor_expired` and the client refreshes execution status. Duplicate events are safe to consume by `(execution_id, sequence)`.

Opaque signed cursors bind resource, authorized scope, filter, ordering, and expiry; never use a cursor as authorization. Collection pages default to 50, max 200; next links are absolute and absent at end. Templates sort by `(template_id, version)`; events by strictly increasing sequence; logs by immutable chunk/offset. Filters apply before pagination. Do not return global counts or reveal inaccessible objects through cursors.

For events and logs, response `cursor` always denotes the position immediately after the returned records (unchanged on an empty page). `page.next` uses that cursor only when more committed records are currently available; its absence is not end-of-stream. Poll the same endpoint with `cursor` to await newly published records, including while running. Log entries carry trusted-ingestion `recorded_at`, optional observed time, logical source and stream. `truncated` means content was omitted by a limit/redaction policy, not merely that another page exists. Expired log cursors return 410 as events do.

Public event vocabulary: `execution.accepted`, `execution.state_changed`, `execution.stage_observed`, `execution.dispatch_changed`, `execution.infrastructure_changed`, `execution.delivery_changed`, `execution.cleanup_changed`, `execution.cancellation_changed`, and `execution.error_changed`. Every event includes a complete safe status-facet snapshot at its revision; cancellation/error fields appear when present. Multiple events in one projection transaction may share revision but have unique increasing sequence. Clients process sequence, tolerate new event types and consult the snapshot instead of parsing free-text messages. No event type itself claims cloud truth without the observation rules in section 3.

Catalog entries include a description, immutable version, allowed overrides, public input schema, defaults and revocation flag. Return authorized retained revoked versions marked `revoked=true` for discoverability; new admission denies them and dispatch rechecks eligibility. `version` is the catalog version mapped to request `template_version`. A tiny paginated catalog does not require a second single-template endpoint or a mutable “latest” alias.

Human curl onboarding is part of M1.1: register an approved public client and redirect URI, obtain an API-audience token through its browser PKCE flow, then pass that token to curl from protected local process state without logging it. Document the approved helper/CLI and exact audience/scope after V02; curl alone is not an OAuth client. Device code remains optional subject to policy. Signed local test tokens must be labeled fixtures and are rejected by connected/live profiles.

Execution projection revision increases atomically with each accepted update and becomes an ETag. GET supports `If-None-Match`/304 after authorization. Cancel is a separate intent resource and deliberately does not require a potentially stale execution `If-Match`. Future mutable registries use `If-Match`/412 with required preconditions; M1 catalog publication is reviewed immutable import, so no PATCH/admin placeholder exists.

Provisional parser limits (not performance acceptance targets): JSON body 64 KiB, command ≤16 arguments of ≤512 characters, ≤32 environment entries of ≤1024 characters each, ≤16 input references, ≤8 secret-policy references. Profiles further bound timeout and data/compute limits; deployment must refuse live execution if mandatory limits or spend/concurrency caps are unset. A logs page is ≤256 KiB with truncation markers; each response page's bytes are bounded even when fewer than requested items fit. Artifact downloads enforce the registered size ceiling and stop on mismatch; no signed cloud URL or redirect bypasses object authorization.

## Error contract

Problem details include `type`, `title`, HTTP `status`, safe `detail`, request-scoped `instance`, stable `code`, and `request_id`; optional invalid-field pointers contain no rejected values. Public problem URIs are stable same-origin `/problems/{code}` documentation identifiers. This does not introduce executable problem endpoints. Invalid/missing token is 401 with `WWW-Authenticate`; forbidden capability is 403; missing/forbidden object is 404. Other transport errors: 400 invalid input, 406 unsupported representation, 409 conflict, 410 expired cursor/object, 413 size, 415 media type, 429 throttling with retry headers, 503 unavailable with `Retry-After`.

After 202, failures appear in the execution/result/events, not as a retrospectively changed submission response. Keep the ten required portable provider codes from section 6. Cloud error details go only to an authorized operator view/evidence path, not a public `detail`, signed URL, filename, or log page. [RFC 9457 problem details](https://www.rfc-editor.org/rfc/rfc9457).

## Later route families — design only

Future concepts include stacks/revisions, deployment targets, previews/plans, promotions, approvals/decisions, budgets/reservations, module catalogs and audit administration. Their schemas/authorization will be designed when those milestones are approved. They are intentionally absent from the active OpenAPI artifact. M1 audit evidence is available to reviewers through a controlled evidence export, not an unbuilt administrative API.
