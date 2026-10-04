# Prompt: assess deployment of the agent infrastructure control plane

**Current as of:** 2026-10-04, agent-v1 with Temporal; continuous drift detection (`drift_check` operations, optional sweep), upgrade detection, one-call upgrades and environment promotion; deployable-pattern placement (422 `cloud_not_available`), pattern changelogs and cost history; pattern onboarding (templates, contract checker CLI and `GET /patterns/{name}/check`); database-backed teams with an operator admin API and portal Teams screens (`FORGEAPI_TENANTS_SOURCE=db`); portal self-service Deploy; fixes from an independent three-part review (team-edit race closed, app workflow liveness, drift sweep limits, diagnostic redaction); one-request multi-cloud HA apps (`POST /apps`, app failover and ordered teardown, two exact-digest approval gates, Temporal rollout; capability `app_rollouts`) and the manual recipe (replicas + Route 53 failover router via `input_refs`, label `app`; [docs/ha-apps.md](ha-apps.md)); built-in developer portal (`GET /console` with an Apps view, capability `web_console`; resource `cloud`/`region`/`estimated_monthly_cost`/`owned_by_caller`/`created_at`/`managed_objects`, discovery `clouds`); failure diagnostics (`diagnostic`, capability `failure_diagnostics`); plan discard, bounded Terraform runs, refusal classification, child-environment filtering, fail-closed unauthenticated mode, resource inventory, operator reconciliation, plan summaries, error reasons, readiness, OpenAPI metadata, operation filters, long-poll, request IDs, Temporal TLS settings, AKS workload identity, an AKS manifest, drift visibility, a hardened image, Terraform 1.16.5, attribute-level plan detail, plan guardrails, resource filters, pattern listing, guardrail discovery, plan-file cleanup, pattern version upgrades, budget discovery, resource labels, output references with destroy protection, opt-in plan expiry and client support for pattern listing and inventory filters (local verification; composition, labels, filters, destroy protection, S3-backend version upgrade, discard/expiry and plan cleanup also proven through real Temporal and Floci AWS on Terraform 1.15.9 and 1.16.5; placed-mode budget/guardrail discovery, budget enforcement, labels, cross-environment ref refusal, locked-environment guardrails and accept-path plan expiry proven on Floci AWS, Azure and GCP; input_refs consumer apply and diagnostic redaction proven on Floci) (unreleased). Hosted storage consumption was verified on the previous image.

For the system walkthrough, local example and architecture/lifecycle/storage diagrams,
start with the [README](../README.md). This brief remains the deployment-specific reference.

The 2026-10-02 current-image local smoke passed with image
`sha256:2ff6b29460288abe49204d439ebae9656e10a754f5e39b1084d6b2f2bb8581d9`:
isolated API/Temporal engine, root/v1 same-key submission and repeated exact-digest execution,
real local-file readback, one plan/apply receipt and one accepted audit event per phase.
All disposable containers/storage/network were removed. This is fresh-install evidence;
hosted CI, image publication and an older-image upgrade are still unverified. The source
checkpoint passed 502 tests (seven optional Floci skips); see progress and session-handoff.


Paste the following into the work assistant. The old two-Container-App prompt is archived under `docs/archive-temporal/` and only applies to the old release.

---

You are assessing deployment of ForgeAPI's replacement agent infrastructure control plane. Read `AGENTS.md`, `docs/session-handoff.md`, `docs/work-deployment.md`, `docs/agent-architecture.md`, then `docs/progress.md` before acting. Follow the repository's Ponytail and model-routing instructions when available; disclose if unavailable.

The new API accepts idempotent intents, records them in a transactional ledger and dispatches real Terraform work through Temporal, and executes only a saved plan whose digest matches the request. Temporal runs deterministic workflows and Python activities. Terraform activities run once; API responses return before Terraform completes. The API and worker currently share persistent local disk. HTTP endpoints let an agent discover, validate, plan, execute or discard, and observe operations. Agents can list patterns (`GET /patterns`), label resources, feed one resource's outputs into another's inputs (`input_refs`), filter inventory by pattern, environment, state and label, see each environment's guardrails and remaining estimated budget in discovery, and upgrade a resource to another pattern version through a reviewed plan (state must live outside the workspace, as with the azurerm backend). There is no MCP server in this solution. Azure, AWS and GCP have Floci lifecycle tests; that is emulator evidence only.

Read `docs/api-versioning.md`: the compatibility guarantee covers the new operation API only.
Use canonical `/v1` routes; root routes remain v1 aliases sharing idempotency and execution.
Real local Temporal tests race first admission and exact-digest execution across both aliases,
verifying one operation/resource, one acceptance per phase and one actual Terraform apply.
Worker restart and late-dispatch checks remain; this is not hosted load evidence.
`/healthz` stays unversioned; `/v1/openapi.json` is the canonical contract. Bootstrap clients
through retained `/agent` discovery and use advertised capabilities and links. Older discovery
without capability metadata supports only baseline behavior. Ignore extra response fields,
reject unsupported features before mutation, and stop on unknown execution states/actions.
Request fields stay strict. Application releases and Terraform pattern revisions are distinct
from the HTTP major version. Do not silently repoint aliases to v2 or remove v1 fields/defaults.
Capability values must be literal JSON booleans; strings/numbers are not feature permission.
Malformed discovery is rejected before optional requests. Unknown correctly typed capability
names and missing old-server metadata retain their existing behavior.
Catalog version enumeration ignores both lightweight and annotated nonversion tags. Annotated
version tags remain pinned to their peeled commit; version syntax and ordering are unchanged.
Optional pattern `config.yaml` must be a mapping or empty/null. Parser failures and nonmapping
roots return sanitized catalog 502 before acceptance/dispatch; refused submissions are audited.
Missing files and valid mapping contents remain supported. Nested configuration validation
has not been expanded by this correction.
Malformed Terraform HCL and invalid text encoding in `.tf` or `config.yaml` also receive
fixed catalog errors without source text or local paths. HCL variable and backend readers
share this boundary. It adds no dependency, nested schema validation or execution retry.
Missing or unexecutable Git returns a fixed catalog 502. Failed submissions are audited before
returning and create no operation/reservation/dispatch; restoring Git permits same-key recovery.
Credential preparation is outside this process-start catch. No automatic retry is introduced.
Git subprocess text decoding replaces invalid bytes before version filtering. Unrelated invalid
tag bytes are ignored by the existing version rules; valid version ordering and commit pins
remain unchanged. Invalid stderr bytes still receive sanitized errors. Local focused tests pass.
Ledger directory creation errors return fixed 503 responses. If refusal audit cannot access
storage, submissions remain unaccepted with no dispatch. The API does not overwrite a file
occupying that directory path; local collision tests verify same-key recovery after repair.
Catalog Git commands have a fixed 60-second deadline and up to five seconds for owned-process
group cleanup. Timeout returns sanitized 502 before acceptance/dispatch, with no automatic retry.
This does not bound credential acquisition, cache-lock waiting, total requests or Terraform,
and does not repair arbitrary Git lock files. Local stalled-command/child-pipe tests pass.

The bundled `python -m app.client --url URL` client supports discover/describe/validate/submit/
operations/status/execute/events with JSON output; command examples are in README. It requires explicit
intent keys and reviewed plan digests, has no autonomous apply or mutation retry, and rejects
unsafe links, redirects and unfamiliar execution instructions. It supports HTTPS or loopback
HTTP through the existing SSH tunnel. The client-only `FORGEAPI_CLIENT_TOKEN` environment
variable supplies a runtime caller bearer token without persistence; it is not a server setting
or a cloud executor identity. Use an approved client environment, not local work-Mac development.

Authenticated identities and relevant group claims must have valid types; missing or malformed
identities receive generic 401 instead of a shared fallback caller ID. JWT retains valid `oid`
precedence and `sub` fallback. Easy Auth still requires a trusted proxy that strips caller-supplied
principal headers. Unknown additive claims remain allowed. The shared fix also affects malformed
legacy API principals; auth-disabled loopback development is unchanged. Local identity fixtures
do not prove hosted Entra/proxy integration.
Real loopback HTTP tests also verify that the default opener refuses redirect responses and
cross-origin discovery links without contacting a second origin or forwarding its dummy token.
This does not establish hosted TLS/proxy behavior.

New Terraform plans fail safely if a public change summary contains a known placement ID,
including a `for_each` address key. No public changes or executable digest are recorded;
private placement data and protected plan/state files remain intact. Correct such patterns
in their own repositories with non-sensitive instance keys. Existing summaries are not migrated.

Before upgrading, prove that existing requests retain their fingerprints and operation IDs,
including across root/v1 aliases, and that old saved plans execute exactly once without
replanning. Preserve Temporal history and verify deterministic replay for workflow changes.
Coordinate API/worker upgrades and retain the compatible Terraform/provider toolchain for
pending plans. No automatic ledger data rewrite, mixed-worker guarantee, downgrade guarantee or
real hosted rollout is implied by the new contract tests.
`tests/test_workflow_replay.py` replays captured plan, successful local-file apply and controlled
activity-failure histories with normal timeouts/retries. The failure is test-only and uses the
real uncertainty writer. These are current baselines, not pre-versioning or actual-timeout evidence.

Operation listing has optional stable pagination when discovery advertises
`stable_operation_pagination`: request a first page with `limit`, then pass its `next_before`
as `before` until null. Do not mix `before` with nonzero `offset` or send it to an older server
without the capability. Existing offset clients remain valid. Anchors respect current caller
visibility; operation states remain live rather than a frozen snapshot.
The bundled client's `operations` command supports `--limit`, `--offset` and `--before`, fetching
one page only. It refuses cursors without explicit capability support and validates every
returned operation. Baseline/offset listing still works with older servers.
The client rejects duplicate operation IDs, a returned cursor anchor, and continuation values
that do not match the returned page. Older pages without `next_before` and additive response
fields remain supported. This checks one page; it does not prove a complete snapshot.
Event pages support explicit `--after` and `--limit`; follow `next_after` until null. The
client checks event operation identity and ascending sequence, preserves additive fields and
unknown action/outcome strings, and fetches one page at a time.
Operation `offset` and event `after` accept integers from 0 through 9223372036854775807;
oversized positions return structured 422 instead of the previous SQLite overflow.
The client checks submission responses against the requested action, pattern and any explicit
resource target. A contradictory response is an unconfirmed mutation: retain the original
body/key; do not trust its operation ID. The client performs no automatic retry or follow-up.

Public Terraform outputs must serialize as strict JSON after sensitive and placement-ID
filtering. If an oversized number decodes to infinity, publication fails through the existing
uncertain execution path: keep the reservation and reconcile with an operator; never reapply
automatically. Sensitive outputs are withheld before this check. Finite values and exact large integers remain
valid; overflowing command-output responses are a fault-injection boundary, not an observed
normal Terraform output behavior.

Ordinary operation/resource/replay/list/event reads use deferred SQLite transactions;
mutations retain `BEGIN IMMEDIATE`. This lets readers observe committed data while another
connection holds a reserved write lock. Cursor-anchor and page reads share one transaction.
It does not promise nonblocking reads under exclusive locks or during first-time schema/index
creation, and does not change journal mode or single-host storage requirements.

A real local HTTP regression also verifies root/v1 status and event reads return committed
snapshots while one refused mutation waits behind a reserved writer. After release, its audit
persists before the normal refusal response; operation/resource/dispatch counts stay unchanged.

Injected SQLite read failures on root/v1 status/list/events return the fixed 503 envelope,
without exposing error details or attempting refusal audit. Tests verify unchanged stored rows
and dispatch; this is fault-injection coverage of existing behavior, not an actual disk-failure test.

Ledger initialization creates the event `(operation_id, seq)` index if missing, including
on an existing database. This additive index leaves rows, audit triggers and cursor semantics
unchanged. A large existing history can lengthen the first ledger open while SQLite builds
the index; it is not a ledger data rewrite or a distributed-storage migration.

Intent inputs must contain finite JSON numbers, including nested values. `NaN`, infinities
and literals that decode to infinity receive sanitized 422 before acceptance or dispatch.
Finite inputs and ordinary strings such as `"NaN"` retain their existing behavior.
Resolved numeric cost estimates and selected budget limits must be finite and nonnegative;
budget booleans/strings are invalid. Configuration failures return sanitized 503 before
admission. Zero and omitted/unlimited budgets retain their meaning; missing estimates under
a configured budget still receive 403. No historical records are silently repaired.
A present selected `estimated_costs.<size>` entry must be a mapping; null, scalar, boolean
or list entries return fixed 503. Missing sizes retain environment/global fallback, and
requests without a size ignore unrelated size entries. Valid selection is unchanged.
Admission also checks contributing stored estimates and their aggregate, returning sanitized
503 for invalid accounting before acceptance or dispatch. Legacy rows remain read-only.
Validation-only does not scan operation-ledger reservations; submission can therefore refuse
after successful validation. Exact-key replay and destroy remain available. Stored NULL counts
as zero; types already normalized by SQLite cannot be recovered.

The image workflow now depends on reusable read-only checks plus an image build and Trivy gate: Python 3.12, Terraform 1.16.5,
locked dependency sync and frozen lint/default tests. Local workflow checks passed, but no
GitHub-hosted run, image publication or deployment has been performed for these changes.
Pushed image tags must exactly match `v` plus the project/runtime version; the workflow checks
this before registry login. Manual builds publish SHA tags only, even when dispatched on a tag.
External actions in both check and publishing workflows are pinned to verified full commit
references, with local regression coverage. Base-image tags remain mutable; this alone does
not establish reproducible image bytes or a verified hosted build.
Local boundary tests cover malformed root/v1 requests, redacted refusal events and no
acceptance/dispatch on rejection; validation/read failures leave audit/state unchanged.
Exception responses await refusal audit work in the existing threadpool, preserving
audit-before-response without blocking the async event loop on a SQLite writer. A local real-lock
test verifies health remains responsive while one refusal waits; audit failure stays sanitized
503. This is not a throughput guarantee under threadpool saturation.
The current client also passed read-only discovery, baseline listing and retained status/event
reads against the older hosted `agent-v1` server without version/capability metadata. See the
[repeatable check](api-versioning.md#observed-older-server-check). This did not deploy the new API
or test mutations against the older server.
Before any authorized release, require successful checks for its exact revision.

The work goal is multi-cloud provisioning. `pve-desktop` is the simulation host, using its existing `k3s-server-01` guest for the API, Temporal and three emulators. The engineer's desktop calls it over HTTP through SSH. See `deploy/emulator/README.md`; do not redirect this task toward provisioning home-lab infrastructure or a standalone PostgreSQL VM.

First inspect the available deployment tools and target capabilities, read-only. Confirm whether the API and Temporal worker can share durable **local** disk on one host. The existing MCP service's separate stateless HTTP Container Apps cannot run this architecture unchanged. If that is the only target, report the mismatch and the required remote-ledger/plan-storage milestone. Do not deploy the previous layout with this working tree or put SQLite on a network share.

Do not set up local development at work. Preserve all existing state and live resources. Do not migrate deployments, change identities or roles, or deploy real patterns unless explicitly instructed. The legacy release remains the supported path for existing hosted resources.

If a suitable target exists, present its concrete configuration and the brief's verification sequence before deployment. Report exactly what is supported, tested and still unverified.

[fill in: work target and deployment-tool access]
[fill in: today's authorized deployment or verification scope]

Verify Temporal connectivity and matching namespace/task queue. Check asynchronous `202` with the worker stopped, then worker recovery, saved-plan execution and duplicate request behavior. On `503 dispatch_unconfirmed`, retry the same request. A queued operation can require a client retry after an API crash before dispatch; an uncertain Terraform outcome requires operator reconciliation. The dev server persists history but is not a production Temporal service.

`tests/test_dispatch_crash.py` verifies local API SIGKILL after acceptance, a preserved resource
reservation across restart and recovery to one completed plan on the identical request. Startup
does not dispatch it automatically. This does not prove hosted recovery and stops before apply.

Local interruption tests now kill an owned worker during a real gated Terraform apply and
exercise a surviving worker's late completion. Both retain uncertainty and prohibit duplicate
apply/replacement work. The test-only timeout is five seconds after checking the production
30-minute request; production code and retries are unchanged. This is accelerated local
evidence, not a hosted interruption test. Preserve operation IDs, audit and protected state;
inspect surviving Terraform children and independent provider evidence before reconciliation.
Do not clear uncertainty by editing the ledger, force-unlocking or automatically replaying work.

Local storage is a persistent directory writable by UID/GID 1000, shared at `/data` by API and
worker; it contains ledger/audit, Temporal dev history, Terraform state/plans and provider
cache. The isolated lab demo uses `.local/floci-demo/data` (about 227 MB initially). Do not
confuse this with emulated Azure storage: Floci-AZ 0.13.0 lost its ARM resource group on restart
despite WAL configuration. API records and Temporal history survived. The group was recreated
through a new operation against the same resource ID. This does not establish distributed
hosting or real Azure evidence.

For backup/restore, first stop admissions and all writers, including the worker, Temporal dev
server and any surviving Terraform children. Preserve the complete protected data directory
and matching configuration/toolchain; restore at the same paths while original writers stay
stopped. An older snapshot cannot undo later provider changes and can erase newer execution
records. Reconcile any post-snapshot changes before resuming work. Copying ForgeAPI's data
does not restore external Temporal or cloud/emulator state.
`tests/test_backup_restore.py` proves a same-path offline copy/restore with real local Temporal
and Terraform, retaining execution identities and applying the original pending digest once
without replanning. It uses the same configuration/toolchain and no post-snapshot changes;
the local-file resource is inside the copied directory. This is not a hosted recovery test.


The placement simulation added `cloud` to catalog entries and environment `targets` for Azure
subscription, AWS account and GCP project, with a platform-fixed region. Patterns must declare
and wire the injected variables. AWS `allowed_account_ids` validates the executor account; it
does not select credentials. Caller overrides are refused and output IDs withheld. Target moves
block execution/update/destroy; do not silently move state. Existing Azure-only mappings remain
supported. Read `docs/tenancy.md` for the exact contract. `deploy/emulator-placement/README.md`
describes the storage deployment and desktop client at port 38000; the older 28000 demo is intact.
Real multi-cloud identity and the work environment remain unverified.

Three-cloud object consumption passed on the hosted placement demo: 1,068 mixed text/binary
bytes uploaded, independently read and compared exactly by bytes/length/SHA-256, deleted and
confirmed absent, followed by API-planned destruction of only the newly created resources.
Retained examples remain intact. The opt-in `--consume-objects` verifier uses placement loopback
ports and incremental safe evidence, stops mutation on uncertainty or failed verification,
and retries ambiguous API dispatch only with the original request identity. See the placement
guide and `docs/progress.md` for commands and operation IDs. This proves emulator behavior,
not work-cloud identity or storage parity. The thin HTTP client, accelerated interruption,
acceptance/dispatch crash verification, plan/apply/failure replay and quiesced backup/restore
are implemented locally. Stable pagination, client listing/event pagination and local release
checks are complete. Hosted execution of the release workflow remains unverified; operator
reconciliation remains manual.
Distributed storage is needed only if the chosen work host cannot satisfy the single-host
durable-local-disk requirement.
