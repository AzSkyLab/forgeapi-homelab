# Work deployment brief — agent control plane

**Current as of:** 2026-10-04, agent-v1 with Temporal; real Entra tokens verified through the full stack and opt-in live group re-checks (`FORGEAPI_LIVE_GROUP_CHECKS`, Microsoft Graph) proven against a real Entra tenant from the home lab; Temporal TLS/mTLS proven in a real handshake and AKS workload identity proven through the stack on Floci Azure (2026-10-04, lab only); continuous drift detection (`drift_check` operations, optional sweep), upgrade detection, one-call upgrades and environment promotion; deployable-pattern placement (422 `cloud_not_available`), pattern changelogs and cost history; pattern onboarding (templates, contract checker CLI and `GET /patterns/{name}/check`); database-backed teams with an operator admin API and portal Teams screens (`FORGEAPI_TENANTS_SOURCE=db`); portal self-service Deploy; fixes from an independent three-part review (team-edit race closed, app workflow liveness, drift sweep limits, diagnostic redaction); one-request multi-cloud HA apps (`POST /apps`, app failover and ordered teardown, two exact-digest approval gates, Temporal rollout; capability `app_rollouts`) and the manual recipe (replicas + Route 53 failover router via `input_refs`, label `app`; [docs/ha-apps.md](ha-apps.md)); built-in developer portal (`GET /console` with an Apps view, capability `web_console`; resource `cloud`/`region`/`estimated_monthly_cost`/`owned_by_caller`/`created_at`/`managed_objects`, discovery `clouds`); failure diagnostics (`diagnostic`, capability `failure_diagnostics`); plan discard, bounded Terraform runs, refusal classification, child-environment filtering, fail-closed unauthenticated mode, resource inventory, operator reconciliation, plan summaries, error reasons, readiness, OpenAPI metadata, operation filters, long-poll, request IDs, Temporal TLS settings, AKS workload identity, an AKS manifest, drift visibility, a hardened image, Terraform 1.16.5, attribute-level plan detail, plan guardrails, resource filters, pattern listing, guardrail discovery, plan-file cleanup, pattern version upgrades, budget discovery, resource labels, output references with destroy protection, opt-in plan expiry and client support for pattern listing and inventory filters (local verification; composition, labels, filters, destroy protection, S3-backend version upgrade, discard/expiry and plan cleanup also proven through real Temporal and Floci AWS on Terraform 1.15.9 and 1.16.5; placed-mode budget/guardrail discovery, budget enforcement, labels, cross-environment ref refusal, locked-environment guardrails and accept-path plan expiry proven on Floci AWS, Azure and GCP; input_refs consumer apply and diagnostic redaction proven on Floci), unreleased. Hosted three-cloud storage consumption is verified on the previous image. The engineer selected Temporal for asynchronous HTTP requests. This brief supersedes the old two-Container-App instructions; those are retained in `docs/archive-temporal/work-deployment.md` for the old release only.

For the system walkthrough, local example and architecture/lifecycle/storage diagrams,
start with the [README](../README.md). This brief remains the deployment-specific reference.

The 2026-10-02 current-image local smoke passed with image
`sha256:2ff6b29460288abe49204d439ebae9656e10a754f5e39b1084d6b2f2bb8581d9`:
isolated API/Temporal engine, root/v1 same-key submission and repeated exact-digest execution,
real local-file readback, one plan/apply receipt and one accepted audit event per phase.
All disposable containers/storage/network were removed. This is fresh-install evidence;
the first GitHub-hosted Check run passed on `main` `bd19198` (2026-10-04, run 37240347133: `check` and `image-scan` both succeeded); image publication and an older-image upgrade are still unverified. The source
checkpoint passed 502 tests (seven optional Floci skips); see progress and session-handoff.


For a new session, read the [session handoff](session-handoff.md) and current progress before acting. The placement demo has verified object upload, exact byte/length/SHA-256 readback, object deletion and API-planned infrastructure cleanup on all three emulators. Do not treat that result as authorization to deploy this working tree in the work environment.

## First verify the deployment target

The current API and Temporal worker require the **same persistent local disk on one host**. SQLite acceptance, audit, idempotency and budget reservations are transactional; Terraform binary plans must remain available for later exact-plan execution. Temporal schedules phase activities; ledger claims and stable workflow IDs prevent duplicate phase execution.

The organisation's existing MCP server builds separate stateless HTTP Container Apps. **That deployment model does not satisfy this version's storage/runtime requirements.** Do not deploy this working tree as the previous two apps, use Azure Table as its operation ledger, place SQLite on an Azure Files/network share. Report this limitation if that is the only available work target. Continue to use the previous released version for existing hosted deployments.

A supported local/lab target has one host, durable local disk, an API process, Temporal service and Python worker. The root Dockerfile can supply both roles; `compose.yaml` demonstrates their shared bind mount. API command: `uvicorn app.main:app --host 0.0.0.0 --port 8000`. Worker command: `python -m app.worker`. The engine command `python -m app.engine` runs the worker plus a development Temporal server/UI, persisting history in `DATA_DIR/temporal.db`. Setting `FORGEAPI_TEMPORAL_ADDRESS` makes the engine worker-only. A production deployment needs an appropriate external Temporal service. The worker has no HTTP endpoint. Agents call the HTTP API directly using its OpenAPI contract.

Do not set up local development on the work Mac as part of deployment. Confirm the target and report feasibility first. Do not change live resources or migrate their state unless asked. No new real AWS/GCP identity, cloud deployment, role assignment or app registration is authorized by this redesign.

## Work simulation hosted on pve-desktop

The engineer's desktop is the HTTP client. The existing `k3s-server-01` guest
(`10.0.20.10`) on `pve-desktop` hosts one pod with API, Temporal/worker and Floci Azure,
AWS and GCP. Its node selector fixes the local ledger/state to that guest. The protected
host directory `/var/lib/forgeapi-emulator` is shared only by API, worker and catalog init.
Emulators have separate disposable volumes. No home-lab VM or database is provisioned.

The API binds loopback inside the pod. An SSH tunnel plus Kubernetes port-forward gives the
client desktop `http://127.0.0.1:28000`; there is no public/LAN Service or Ingress. The
namespace denies pod ingress and contains no cloud credentials. Deployment, reconnection
and remote HTTP verification commands: [emulator guide](../deploy/emulator/README.md).
This is a manually loaded unreleased image; it is not an ArgoCD or work production rollout.
Cloud emulator state is disposable, while operation history and Terraform state persist.
After emulator state loss, inspect and replan the same resource ID; never replay an old plan.

## Local storage requirement and lab evidence

A persistent local disk directory writable by the API and worker is sufficient for this
milestone. In the container image both run as UID/GID 1000. Mount the same directory at
`/data`; it contains the SQLite operation ledger/audit, the dev Temporal database, Terraform
state, saved plans, receipts and provider cache. Restrict access because Terraform state and
plans can contain sensitive material. Back up only after stopping admissions and quiescing
the API, worker, Temporal dev server and all Terraform children. Stopping the API/engine alone
does not prove that a previously orphaned Terraform process has stopped.
SQLite on an SMB/NFS share is still unsupported. A remote Temporal service alone does not
replace this directory.

For a quiesced snapshot, preserve the complete data tree together, including SQLite sidecar
files, Temporal history, workspaces, exact saved plans, receipts and provider cache. Preserve
the matching application revision, Terraform/provider versions, catalog/tenant configuration
and absolute workspace paths. Keep backups protected like the original state; do not include
raw state, plans or credentials in verification output.

Restore with all original writers still stopped, at the same paths and with the compatible
toolchain. Check the restored databases and plan digests before allowing execution. A snapshot
is only a record of its capture point: it does not undo later provider changes. If execution or
external changes occurred afterward, reconcile them first; restoring an older ledger can erase
execution records and cannot establish that an apply is safe to repeat. Do not run original
and restored workers concurrently. External Temporal and cloud/emulator state require their own
coordinated recovery; copying ForgeAPI's directory does not back up those systems.

`tests/test_backup_restore.py` verifies this procedure using temporary local files and the real
persisted Temporal dev server. It applies a resource, plans an update, stops all writers, copies
the whole data tree and moves the original aside. A same-path restore retains ledger/audit,
workflow run IDs/results, Terraform state and the saved-plan digest. The pending update then
applies exactly once without init/replanning; identical request and execution retries retain
their identities. This test keeps the same configuration/toolchain and has no changes after
capture. Its local-file resource is also inside the backup; external resource recovery is unproven.

The isolated home-lab demo uses `.local/floci-demo/data`, separate from existing live state,
and occupied approximately 227 MB after installing the Azure provider. Its catalog and
versioned fixture are under `.local/floci-demo/`; its emulator files use a separate `azure/`
subdirectory. API: `localhost:18000`; Temporal UI: `localhost:18233`; Azure emulator:
`localhost:4577`. This is local evidence, not a work deployment.

A restart preserved the ForgeAPI ledger and Temporal history. **The Floci-AZ 0.13.0 ARM
resource group did not survive an emulator restart**, even with WAL storage configured. A
fresh Terraform plan on the same resource ID recreated it. Do not treat emulator persistence
settings as proof of service-specific persistence. Configuration reference:
[Floci-AZ storage modes](https://floci.io/floci-az/configuration/storage/).

## Request flow and verification

The [versioning policy](api-versioning.md) covers only the operation-based API. `/v1` is the
canonical route family; the existing root routes below remain aliases with the same handlers,
permissions, audit and idempotency scope. They are not redirects or aliases to a future v2.
The local real Temporal scenario races first admission and execution across both aliases:
one operation/resource, one accepted event per phase and one real Terraform plan/apply receipt
remain, including across worker restart. This is local execution evidence, not a hosted load test.

| Existing alias | Canonical route | Method |
| --- | --- | --- |
| `/agent` | `/v1/agent` | GET |
| `/patterns` | `/v1/patterns` | GET |
| `/patterns/{name}` | `/v1/patterns/{name}` | GET |
| `/patterns/{name}/changes` | `/v1/patterns/{name}/changes` | GET |
| `/patterns/{name}/check` | `/v1/patterns/{name}/check` | GET |
| `/budgets/history` | `/v1/budgets/history` | GET |
| `/intents/validate` | `/v1/intents/validate` | POST |
| `/operations` | `/v1/operations` | GET, POST |
| `/operations/{operation_id}` | `/v1/operations/{operation_id}` | GET |
| `/operations/{operation_id}/events` | `/v1/operations/{operation_id}/events` | GET |
| `/operations/{operation_id}/execute` | `/v1/operations/{operation_id}/execute` | POST |
| `/operations/{operation_id}/discard` | `/v1/operations/{operation_id}/discard` | POST |
| `/operations/{operation_id}/reconcile` | `/v1/operations/{operation_id}/reconcile` | POST |
| `/resources` | `/v1/resources` | GET |
| `/resources/{resource_id}` | `/v1/resources/{resource_id}` | GET |
| `/apps` | `/v1/apps` | GET, POST |
| `/apps/{app_id}` | `/v1/apps/{app_id}` | GET |
| `/apps/{app_id}/approve` | `/v1/apps/{app_id}/approve` | POST |
| `/apps/{app_id}/failover` | `/v1/apps/{app_id}/failover` | POST |
| `/apps/{app_id}/destroy` | `/v1/apps/{app_id}/destroy` | POST |
| `/apps/{app_id}/discard` | `/v1/apps/{app_id}/discard` | POST |
| `/resources/{resource_id}/drift-check` | `/v1/resources/{resource_id}/drift-check` | POST |
| `/resources/{resource_id}/promote` | `/v1/resources/{resource_id}/promote` | POST |
| `/resources/{resource_id}/upgrade` | `/v1/resources/{resource_id}/upgrade` | POST |
| `/admin/teams` | `/v1/admin/teams` | GET, POST |
| `/admin/teams/validate` | `/v1/admin/teams/validate` | POST |
| `/admin/teams/import` | `/v1/admin/teams/import` | POST |
| `/admin/teams/{name}` | `/v1/admin/teams/{name}` | GET, PUT |
| `/admin/teams/{name}/revisions` | `/v1/admin/teams/{name}/revisions` | GET |
| `/admin/teams/{name}/revisions/{revision}` | `/v1/admin/teams/{name}/revisions/{revision}` | GET |
| `/admin/teams/{name}/revert` | `/v1/admin/teams/{name}/revert` | POST |
| `/admin/teams/{name}/archive` | `/v1/admin/teams/{name}/archive` | POST |
| `/admin/teams/{name}/unarchive` | `/v1/admin/teams/{name}/unarchive` | POST |

Resources also show `cloud`, `region` (placement region only), `estimated_monthly_cost` (null once destroyed), `owned_by_caller` (whether the caller ran the resource's last successful operation; the actor ID is never shown), `created_at` (null for resources created before this field) and `managed_objects` (the Terraform objects, address and type, in state after the last successful apply, from the saved plan with no extra Terraform run; `[]` after destroy, null if never applied). Discovery's business units show `clouds` per deployable environment (cloud names only, never IDs). A deploy that ends `failed` (nothing changed by definition) releases its budget reservation back to the previous amount, exactly like discard and expiry; reconcile-as-failed does too; `uncertain` keeps it until reconciled. Terraform runs with `TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE=true` so fresh workspaces link the shared provider cache instead of rewriting binaries that concurrent plans/applies execute ("text file busy"); workspace lock files then hold only this platform's checksums. If the worker is started with SIGINT ignored (a background shell launch), Terraform children get default SIGINT (reset in the child only) so deadline SIGINTs still stop Terraform gracefully; the worker's own signal handling is unchanged. Waiting for the provider-cache init lock counts against the phase deadline (a plan that cannot get it in time ends `failed`, never `uncertain`). Diagnostics never include state-lock holder text and redact platform-injected values (resource groups, subnets, storage accounts) as well as placement IDs. Teams (business units) can live in the database: with `FORGEAPI_TENANTS_SOURCE=db` operators manage them through `/admin/teams` (capability `team_admin`; discovery `caller.operator`): create, full replace with `If-Match` (428 missing, 412 stale) and a required `reason`, dry-run `validate` with findings and an impact summary, append-only revisions with path diffs, `revert`, `archive`/`unarchive`, and atomic YAML `import`. Every write is refused on error findings (422 `team_invalid`), on unacknowledged warnings (409 `warnings_not_acknowledged`), and on changes that would strand or move live resources (`env_has_resources`, `placement_change_with_resources`); it is audited as `team.<action> <name> r<revision>` in the same transaction. Target IDs appear only in an operator's `GET /admin/teams/{name}`. Archived teams accept no new intents or updates; destroy, discard, execute of an already-accepted plan, reads and drift checks still work, so nothing is stranded. `python -m app.team_check tenants.yaml [--catalog patterns.yaml]` validates a mapping file (exit 1 on errors). Team edits cannot race in-flight work: acceptance re-reads the team inside its transaction and refuses 409 `placement_stale` (audited, nothing written) when the team revision changed since validation (any edit — resubmit), the team was archived, the caller lost deploy rights, or the placement changed; execute refuses new work accepted after an archive (409 `team_archived`) while plans accepted before it and destroys still run. Import apply needs `expected_revisions: {team: current_revision}` from its dry run for every existing team it changes (428 `expected_revisions_required`, 412 `revision_stale`); team YAML may not use anchors/aliases and is capped at 1 MB; team names must match the pattern exactly (no trailing newline). New patterns follow [docs/pattern-onboarding.md](pattern-onboarding.md): start from `examples/pattern-template-azure` (primary; `-aws`/`-gcp` lighter), run `python -m app.pattern_check <dir|git-url> --cloud azure --terraform` (exit 1 on errors; prints the registration snippet), validate the registry with `--catalog patterns.yaml`, then `GET /patterns/{name}/check?version=` (capability `pattern_checks`; static checks at the pinned commit, cached per commit; 404 for patterns the caller may not use). Unknown `patterns.yaml` keys are now a clean catalog error. A pattern allowed in an environment whose `targets` lack its cloud is refused 422 `cloud_not_available` (validate, submit, promote, updates; audited); malformed targets and other platform misconfiguration stay 503. Discovery business units show `deployable_patterns` per environment and `GET /patterns?environment=` lists only those (capability `deployable_patterns`). `GET /patterns/{name}/changes?from=&to=` (capability `pattern_changes`; patterns the caller may use) returns commit subjects between the tags (no authors), the input-schema diff (added/removed/changed; default values never shown, only that they changed), and `new_required_inputs`; with `environment`, platform-injected inputs are hidden. It keeps a bare history clone per pattern under `data_dir/pattern-cache/<name>/history.git` (20 s git timeouts). `GET /budgets/history?business_unit=&environment=&days=` (capability `cost_history`, 1–366 days) reconstructs a daily reserved-cost series from the operation ledger (no new storage) with by-pattern totals and top movers; the last day equals budget discovery's `reserved`; legacy rows are flat; at most 5000 resources (`truncated`). `POST /resources/{id}/promote` (capability `promotion`, Idempotency-Key) `{"environment", "business_unit"?, "size"?, "inputs"?, "input_refs"?, "labels"?}` (size defaults to the source's; 422 `promotion_needs_size` naming the offered sizes when the target does not offer it) submits a deploy intent for a NEW resource in the target zone with the source's pattern, version and applied commit as `expected_commit` (a moved tag is 409 `revision_moved`), the source's caller inputs (never its injected placement; the target zone injects its own) plus overrides, and labels plus `promoted_from`; it goes through the normal policy/guardrail/budget path and only plans. Source refs cannot cross zones: 422 `promotion_needs_refs` unless the request supplies them. `POST /resources/{id}/upgrade` (capability `resource_upgrade`) `{"version", "inputs"?, "labels"?}` re-plans the same resource at another version reusing its stored caller inputs, labels, refs and size (409 `upgrade_noop` for the same version; optional `expected_commit` → 409 `revision_moved` if the tag moved); patterns that keep local state in the workspace still cannot change version (the plan fails). Resources show `promoted_from` (source id only). `POST /resources/{id}/drift-check` (capability `drift_checks`, Idempotency-Key; needs deploy rights in the resource's zone) accepts a `drift_check` operation for a `ready`, idle resource: `terraform plan -refresh-only -lock-timeout=0s` into a scratch `drift.tfplan` that is shown and deleted, never applied, state unchanged; it ends `succeeded` with `drift` or `failed` with `diagnostic` (a held lock fails at once, never force-unlocked; a worker without the workspace's local state reports `unknown`, never `in_sync`). The sweep keeps at most 20 checks outstanding and stamps resources it cannot place (`unknown`, one refused audit event per interval). Pattern files that are symlinks or resolve outside the checkout are never read (`symlinked_file` in `pattern_check`). Resources show `drift_status` (`in_sync`/`drifted`/`unknown`/null), `drift_checked_at`, `drift`; `GET /resources?drift_status=drifted`. A successful deploy resets to `in_sync`. Drift-check operations are hidden from `GET /operations` unless `?action=drift_check` or `?include_checks=true` (older clients expect only deploy/destroy actions). Resources also show `latest_version` and `upgrade_available` (capability `upgrade_detection`; highest semver tag the caller may use; null for pending/destroyed/local patterns or when git is unavailable); `GET /resources?upgrade_available=true` scans up to 500 rows per request, so a filtered page may hold fewer than `limit` items while `next_after` is set. `POST /apps` (capability `app_rollouts`, Idempotency-Key required) rolls out a multi-cloud HA app in one request: 2–4 replicas (accepted all-or-nothing in one transaction, budget-checked now) and a router whose `replica_refs` become `input_refs` once every replica has succeeded. A Temporal `AppRolloutWorkflow` (id `forgeapi-<app_id>-rollout`) waits for the replicas, accepts the router (idempotency key `<app_id>:router`; its budget is checked then) and starts its plan; it never applies. Two approval gates, each one `POST /apps/{id}/approve` with `{"plan_digests": {op_id: digest}}` naming exactly that gate's planned operations (all replicas, then the router); a wrong set is 409 `app_gate_mismatch`, a wrong digest 409 `plan_digest_mismatch`, all-or-none, audited (`app.create`, `app.approve`, `app.router`, `app.state`). App states: planning_replicas, awaiting_replica_approval, applying_replicas, planning_router, awaiting_router_approval, applying_router, ready, failed, uncertain; derived from member operations. A discarded replica fails the app; other planned replicas keep their reservation until discarded. `POST /apps/{id}/failover` (capability `app_failover`, Idempotency-Key) with `{"primary": <replica index>}` on a `ready` app rebuilds the router intent from its current `input_refs` (every `primary_*` input → the chosen replica, every `secondary_*` → the previous primary), plans it and returns the app to the router gate; the response `primary` changes only after that plan is approved and applied (409 `app_failover_noop`, `app_not_ready`, `app_replica_not_ready`, `app_failover_unsupported`; audit `app.failover`). `POST /apps/{id}/destroy` (capability `app_teardown`, Idempotency-Key) tears down in order through `AppTeardownWorkflow` (`forgeapi-<app_id>-teardown`, never applies): router destroy gate, then all replica destroys gate, then `destroyed`; guardrails apply (403 `policy_denied` with nothing accepted where `allow_destroy` is false); 409 `app_destroy_unavailable` while any member operation is unfinished; a failed teardown can be retried with a new key; any destroy request on an unfinished teardown (including after an operator reconciles an `uncertain` destroy) resumes it; any earlier destroy key replays its own teardown. Rollout and teardown workflows survive transient database failures, retry members that are briefly busy (e.g. a drift check), re-plan members whose dispatch was lost, stop once the app is ready or torn down, and re-check that the requester still has access before accepting the router or replica destroys (`requester_access_revoked`); a retried `POST /apps` revives a dead rollout. Members of an app (`app_role` label) cannot be changed through `POST /operations` or upgrade (409 `app_member`; use the app endpoints). `POST /apps/{id}/discard` (change rights, audited `app.discard`) discards a failed app's still-planned member operations and releases their budget; a failed app with planned members reports `next_action: discard_planned_operations`. Multi-cloud HA apps ([docs/ha-apps.md](ha-apps.md)) are replicas per cloud plus a failover router pattern wired with `input_refs`, grouped by labels `app` and `app_role`; pattern repos for this need replica outputs `endpoint` and `health_path` and router inputs `primary_endpoint`, `secondary_endpoint`, `primary_health_path`, `secondary_health_path`. `GET /console` (capability `web_console`, not in OpenAPI) serves a built-in, dependency-free developer portal (including an Apps view with topology, redundancy and failover review): Home (my resources, jobs in flight, items needing attention with diagnostics, budget burn per landing zone, clouds, recent activity), Infrastructure (Mine/All, grouped by project label `project`, landing zone, cloud or pattern, with cost and object counts), Landing zones (business unit × environment: clouds, regions, guardrails, budget, deployed resources), Jobs board, History, Catalog, resource pages (managed objects, outputs, uses/used-by, operation history) and operation pages. Earlier description: overview (capabilities, business units, budgets, guardrails, counts by state), operations with state filters, full detail (change table, drift, outputs by name, `error`/`diagnostic`, event timeline), resources with filters, labels and `input_refs`, and patterns with input schemas. On a `planned` operation it can apply the exact `plan_digest` or discard, each after an in-page second confirmation, through the ordinary audited endpoints. The page and its two assets carry no data and need no auth; every data call uses the caller's own credentials (with Entra, paste an access token; it is kept in page memory only). Strict CSP (`default-src 'none'`, same-origin scripts/styles/connections), `nosniff`, `no-referrer`, `no-store`; all API text is rendered as text. EasyAuth mode ignores pasted tokens. `/healthz` (liveness) and `/readyz` (readiness) are unversioned and unauthenticated. `/readyz` returns 200 only when a ledger read and a Temporal health call both succeed within about 2 s, else 503 naming the failing check (`ledger` or `temporal`) without error text. Use `/readyz` for the readiness probe and `/healthz` for liveness. Changes include attribute paths (`changed_attributes`, `replace_paths`, `action_reason`, `sensitive_attributes`), never values. Tenant environments can protect resource types from deletion/replacement and forbid destroy (`protected_resource_types`, `allow_destroy`; reason `policy_denied`). Planned operations include `drift` (objects changed outside the API, from Terraform's refresh); to check a resource, resubmit its definition, read `drift`, then discard. An update intent (`resource_id`) may name a different `version` of the same pattern: it plans against the existing state at the new pinned commit, and the caller reviews `changes` before executing. Deploy intents may carry `labels` (up to 16 `key: value` pairs; keys `^[a-z][a-z0-9_.-]{0,62}$`, values up to 128 characters): metadata for finding resources, never passed to Terraform and never for secrets. An update without `labels` keeps them, with `labels` replaces them; destroy with labels is 422. `GET /resources?label=key=value` filters on one label (capability `resource_labels`). Deploy intents may also set `input_refs` (capability `input_references`): `{"<input>": {"resource_id": "res_…", "output": "<name>"}}` copies a public output of a `ready` resource the caller can see, in the same business unit and environment, into that input at acceptance (409 `reference_not_ready` otherwise; withheld outputs cannot be referenced). It is a snapshot: later changes to the source are not followed, and an update must resend refs it still wants. A resource that a `ready` resource references cannot be destroyed (409 `resource_referenced`) until that consumer is updated without the ref or destroyed; a source with a destroy in flight cannot be referenced, and a planned consumer whose source was destroyed or is being destroyed cannot execute (409 `reference_not_ready`; capability `reference_protection`). `GET /patterns` lists the caller's patterns with their cloud and a describe link, without contacting git (capability `pattern_listing`). `GET /resources` accepts `pattern`, `environment` and `state` (`pending`, `ready`, `destroyed`) filters that compose with `after` (capability `resource_filters`). Discovery's `business_units[].guardrails` shows each deployable environment's `allow_destroy` and `protected_resource_types` (capability `guardrail_discovery`). Each business unit also shows `budgets` for deployable environments that have `budget_monthly`: `monthly_budget`, `reserved` (exactly what admission counts: non-destroyed resources plus legacy committed cost) and `available` (capability `budget_discovery`); corrupt stored amounts or an invalid budget give the same sanitized 503 as submission. A failed or discarded operation's saved plan file is deleted while its resource is still reserved; an `uncertain` operation's plan is kept for the operator; a successful destroy deletes the workspace's `.terraform` directory (state, inputs and logs stay). `GET /operations` accepts `resource_id` and `state` filters (capability `operation_filters`); `GET /operations/{id}?wait=N` (0 to 30 s, capability `operation_long_poll`) returns as soon as a pollable state changes. Every response carries `X-Request-ID` (a valid caller value is echoed, otherwise generated), error bodies include `request_id`, and the logger `forgeapi.access` writes one JSON line per request with request ID, method, route template, status, duration and caller ID only. [`deploy/aks/`](../deploy/aks/README.md) is the AKS starting point: one replica with API and worker on a managed disk, workload identity, readiness/liveness probes and a network policy; it has been rehearsed end to end on a local kind cluster (`deploy/aks-rehearsal/`: readiness, plan → approve → apply through the pod with read-only root filesystems, a saved plan surviving a pod restart) but never applied to AKS; its NetworkPolicy was shown to be enforced (it blocked non-443 egress until the rehearsal allowed the emulator ports), and Azure/AWS/GCP provider Terraform ran from the pod against the Floci emulators; Entra auth, workload identity and Azure Disk are proven only on the work cluster. `/openapi.json` retains root-route discovery;
`/v1/openapi.json` describes the canonical contract. Returned links, Location headers and
dispatch-error status URLs follow the requested route family. Discovery exposes supported
contract/capabilities and application release information. Pattern tags/commits are separate.

1. `GET /healthz` returns contract `agent-v1`. `GET /agent` lists only permitted patterns and placement choices.
2. `GET /patterns/{name}` returns a JSON input schema, versions, commit and example. With tenancy configured, supply `business_unit` and `environment` as needed.
3. `POST /intents/validate` checks schema and placement only. It returns the resolved version/commit, not a Terraform plan or budget reservation.
4. `POST /operations` requires `Idempotency-Key` and an intent body: `action` (`deploy` or `destroy`), `pattern`, optional `version`, `expected_commit`, `resource_id`, `business_unit`, `environment`, `size`, and `inputs`. Inputs are complete replacement inputs for updates. Unknown fields are refused. The API records acceptance then starts a Temporal plan workflow. `202` acknowledges dispatch without waiting for Terraform; it works even when no worker is available. A dispatch failure returns `503 dispatch_unconfirmed` with the operation ID: retry the same key/body to complete dispatch. Use the returned version and expected commit for git patterns.
5. `GET /operations/{operation_id}` returns `state`, `next_action`, `terminal`, `poll_after_seconds`, safe outputs and links. Wait for `planned`, inspect `changes` and `plan_digest`. Resource addresses/types/action lists are exposed; raw Terraform plan values are not.
6. `POST /operations/{operation_id}/execute` with `plan_digest` starts the apply phase for that exact plan and returns `202` without waiting for execution. An unconfirmed dispatch returns `503`; retry the same operation/digest. Repeating this request cannot apply twice. Poll to completion. `uncertain` requires operator reconciliation; never create replacement work automatically.
   To reject a reviewed plan instead, `POST /operations/{operation_id}/discard` (no body, capability `discard_planned_operation`). Only a `planned` operation can be discarded; it ends as `failed` with error `plan discarded before execution`, records an `operation.discard` audit event, releases the resource for a new intent and restores the budget reservation. The saved plan file is deleted. Repeating the call returns the same operation.
7. `GET /operations` retains `offset` and `limit` (max 100). If discovery advertises `stable_operation_pagination`, continue pages with `before=<next_before>` and stop at `next_before: null`; do not combine it with nonzero `offset`. Continuation uses visible operation IDs and returns `next_offset: null`, avoiding duplicates caused by new arrivals in offset mode. States remain live, not frozen snapshots. `GET /operations/{operation_id}/events` supports `after` sequence and `limit`. Verify accepted-before-execution audit events and tenant isolation.
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
8. For cleanup, submit a new intent with `action: destroy`, the `resource_id` and `pattern`; inspect and execute its new plan. This uses the stored definition. Only clean up resources created for the authorized verification.

Native/lab verification commands live in README. The Floci suite uses optional `compose.floci.yaml`, Azure resource groups, AWS S3 and GCP storage buckets, real Terraform and independent readback. It must not be represented as real-cloud evidence. Work-Mac and new-runtime cloud hosting remain unverified.

The checked-in image workflow now requires reusable checks before tag/manual publication.
Checks also run for pull requests and main pushes: Python 3.12, uv 0.12.21, Terraform 1.16.5,
locked dependency synchronization and frozen lint/default tests. A second job builds the
default and `engine` images and fails on fixable CRITICAL/HIGH findings from a pinned Trivy scan.

**Image.** Base images are pinned by digest and Debian packages are upgraded at build time.
The default target is the production runtime for API and worker against an external Temporal;
it contains no Temporal server binary. `docker build --target engine` adds the bundled Temporal
dev server for local/lab use (`compose.yaml` builds it as `forgeapi-engine:dev`;
`deploy/engine/Dockerfile` is now only a pointer). Publishing pushes `forgeapi` and
`forgeapi-engine` with SBOM and provenance attestations. Locally, Trivy found 0 fixable
CRITICAL/HIGH in the default image after the upgrade.

**Terraform 1.16.5.** The image moved from 1.15.9 because the 1.15.9 binary carried fixable
gRPC and Go standard-library CVEs. Saved plans cannot cross Terraform versions: executing a plan
made by another version is refused before any change and recorded as `failed` ("saved plan
was made by a different Terraform version; submit a new intent"). Before rolling out a new
toolchain, execute or discard pending `planned` operations, or expect them to fail and be
resubmitted. Optional Floci remains skipped.
Only publishing has package-write permission. Local lock/workflow checks passed; GitHub-hosted
execution, publication and deployment of these changes have not been performed. Use successful
checks for the exact intended revision before a future release; this does not authorize rollout.
Pushed image tags must exactly match `v` plus the project/runtime version; the workflow checks
this before registry login. Manual builds publish SHA tags only, even when dispatched on a tag.
External actions in both check and publishing workflows are pinned to verified full commit
references, with local regression coverage. Base-image tags remain mutable; this alone does
not establish reproducible image bytes or a verified hosted build.
Local boundary tests cover malformed root/v1 requests, redacted refusal events and no
acceptance/dispatch on rejection; validation/read failures leave audit/state unchanged.
Exception responses await refusal audit work in the existing threadpool. A contended SQLite
write therefore does not block the async event loop; the refused mutation still waits for its
audit event, and audit failure returns sanitized 503. A local real-lock test proves health
responds while one refusal waits. This does not establish throughput under threadpool saturation.
The current client also passed read-only discovery, baseline listing and retained status/event
reads against the older hosted `agent-v1` server without version/capability metadata. See the
[repeatable check](api-versioning.md#observed-older-server-check). This did not deploy the new API
or test mutations against the older server.

The bundled `uv run python -m app.client --url URL` client exposes `discover`, `describe`,
`patterns`, `validate`, `submit`, `operations`, `status`, `execute`, `discard`, `reconcile`, `events`, `resources` (with `--pattern`, `--environment`, `--state`, `--label`) and `resource`; see [README](../README.md#http-client).
Submission requires a body file and caller-owned idempotency key; execution requires the
operation ID and explicitly reviewed digest. Commands return JSON and never automatically
execute a plan or retry a mutation. After a transport failure, acceptance may already be durable:
retain the original request identity. The client uses discovery links and baseline fallback,
and its `operations` command fetches one page with `--limit`, `--offset` or `--before`.
Cursor mode requires the advertised capability even when older discovery omits metadata;
baseline listing still works. A cursor request never silently falls back to offset. It
rejects unfamiliar states/actions, cross-origin links and redirects, and disables inherited
proxies. Remote URLs require HTTPS; loopback HTTP supports local development and SSH tunnels.
Capability values must be literal JSON booleans; strings/numbers are not feature permission.
Malformed discovery is rejected before optional requests. Unknown correctly typed capability
names and missing old-server metadata retain their existing behavior.
Caller bearer tokens are supplied at runtime through the client-only `FORGEAPI_CLIENT_TOKEN`
environment variable and are never persisted or passed as CLI arguments. This is not a new
server setting or executor credential. Do not install local development at work to run it;
use the approved client environment. Local HTTP integration evidence is recorded in progress.
Real loopback TCP tests exercise the default client opener with dummy bearer credentials:
301/302/303/307/308 redirects and cross-origin discovery links are refused, and a second
server receives no request. This is local transport evidence, not hosted TLS/proxy verification.

The `events OPERATION_ID --after N --limit N` command fetches one explicit page, using
`next_after` for continuation. It checks operation identity and advancing event sequences,
while preserving unknown action/outcome strings and additive fields. Missing old capability
metadata still permits baseline events; explicit false refuses them. No automatic traversal.
The client rejects duplicate operation IDs, a returned cursor anchor, and continuation values
that do not match the returned page. Older pages without `next_before` and additive response
fields remain supported. This checks one page; it does not prove a complete snapshot.

For release verification, exercise an old request through both route families with the same
key and confirm the same operation/resource IDs and one accepted event. Read and execute a
pre-upgrade saved plan without replanning or duplicate apply. Contract fixtures check older
clients with newer responses and newer clients with older discovery; new features require
advertised support, while absent capabilities mean the original baseline only. Unknown request
fields remain errors; unknown execution states/actions must stop the client safely. Review the
OpenAPI baseline diff as well as running tests. Schema equality alone cannot prove behavior.

Coordinate API/worker upgrades on the single host and preserve all ledger, audit, history and
plan/state files. Keep Terraform/provider compatibility for pending plans. HTTP versioning does
not make binary-plan format changes, mixed-version workers or arbitrary downgrades safe. No
upgrade may automatically replan, retry an apply or unlock state. No data migration or live
deployment is part of the compatibility milestone; see progress for exact test evidence.

## Multi-cloud placement and storage patterns

The catalog may declare `cloud: azure|aws|gcp`. In the tenant mapping, each environment's
`targets` selects `azure: {subscription_id, region}`, `aws: {aws_account_id, region}`, and/or
`gcp: {project_id, region}`. These values are private, injected Terraform inputs. The pattern
must declare and correctly wire the two variables to its provider; AWS uses `allowed_account_ids`
as a guard on the executor's account. The caller supplies only pattern, BU/environment and
ordinary inputs. Target and region overrides fail validation. Pattern descriptions now include
`cloud`. An invalid/missing target fails closed. Existing subscription-only mappings remain
supported for catalog entries without `cloud`.

Operations pin their target. Execute/update/destroy reject a changed target or catalog cloud
with 409; restore the mapping or arrange an explicit migration. Work already dispatched retains
its accepted configuration. Output values containing subscription/account/project IDs are
withheld. These rules do not establish AWS role assumption or GCP workload identity; real
executor identity design and permission validation remain outstanding. See [tenancy](tenancy.md).

New plans also fail safely if their public change summaries contain a known placement ID,
including an ID embedded in a Terraform `for_each` address. No public changes or executable
digest are recorded, and execution remains refused. Pattern authors must use non-sensitive
instance keys; addresses are not silently rewritten. Private placement records and protected
plan/state files remain intact. Existing historical summaries are not migrated by this guard.

The placement-enabled storage validation deployment uses namespace `forgeapi-placement` on the
same guest, with `/var/lib/forgeapi-placement` and desktop ports 38000 (API), 38233 (Temporal UI).
The prior demo at 28000 stays intact, with separate resources/state. See
[deployment and tunnel](../deploy/emulator-placement/README.md). Azure provisions a Storage
account and private blob container; AWS/GCP provision buckets. Floci-AZ 0.13.0's ARM container
read is not reliable for absence, so this Azure fixture uses the supported blob data path. A worker-only loopback HTTP router
redirects provider-generated Storage hostnames into Floci; it rejects other destinations.

The opt-in desktop command `deploy/emulator/verify.py --consume-objects` is documented in the
[placement guide](../deploy/emulator-placement/README.md#verify-object-consumption). It requires
the placement loopback ports, a placement environment and a new evidence filename; `--keep`
is refused. Object traffic goes directly to Floci using no real credentials. It verifies
1,068 mixed text/binary bytes per cloud and destroys only its newly created resources after
object absence is confirmed. Safe request bodies/keys, IDs, statuses and hashes are saved
incrementally. Ambiguous API calls retry the same request; uncertainty or failed readback
stops mutation and records recovery instructions. Retained examples were independently
verified unchanged. Evidence and operation IDs are in [progress](progress.md).

## Settings

Settings retain their previous names so the catalog, policy and identity code can be reused. Do not copy the old environment blindly.

| Settings | Current treatment |
| --- | --- |
| `FORGEAPI_DATA_DIR` | Same durable local directory for API and worker. Holds `operations.sqlite`, audit, plan/state workspaces and command receipts. Preserve existing state. |
| `FORGEAPI_CATALOG_PATH`, `FORGEAPI_TERRAFORM_BIN` | Catalog path and Terraform binary. Patterns remain external root-module repos, versioned by tag and pinned at acceptance. Bundled `examples/` are local verification only. |
| `FORGEAPI_DB_BACKEND` | Must be `sqlite` for agent-v1. `table` explicitly fails. |
| `FORGEAPI_AUTH_MODE`, `FORGEAPI_ENTRA_TENANT_ID`, `FORGEAPI_ENTRA_AUDIENCE` | `none` for loopback development; `entra` validates bearer tokens; `easyauth` only behind a trusted proxy that strips caller-supplied principal headers. |
| `FORGEAPI_TEMPORAL_TLS`, `FORGEAPI_TEMPORAL_TLS_CA_PATH`, `FORGEAPI_TEMPORAL_TLS_CERT_PATH`, `FORGEAPI_TEMPORAL_TLS_KEY_PATH`, `FORGEAPI_TEMPORAL_TLS_SERVER_NAME` | Opt-in TLS to an external Temporal frontend (for example on AKS), used by the API, worker and readiness check. A CA path alone verifies the server; a certificate and key together add mTLS (one without the other refuses to start). Set the server name when it differs from the address. Unset: plain gRPC. Proven in a real handshake (2026-10-04, `tests/test_temporal_tls_live.py`, default suite): API readiness, API dispatch and `build_worker` connected through a client-certificate-requiring TLS terminator (ALPN h2) in front of a real Temporal server and ran a plan → exact-digest apply; a missing client cert, an unrelated CA, a missing server name and plaintext were each refused. Not yet run against the work cluster's own Temporal TLS. |
| `FORGEAPI_AZURE_USE_AKS_WORKLOAD_IDENTITY` | `true` on AKS with workload identity: Terraform uses `ARM_USE_AKS_WORKLOAD_IDENTITY` and the azurerm backend `use_aks_workload_identity`, with the client and tenant IDs and token file injected by the workload-identity webhook. Cannot be combined with the federated, managed-identity or certificate modes. Proven in the lab on 2026-10-04 (`tests/test_floci_workload_identity.py`, `--floci`, Terraform 1.15.9 and 1.16.5): with only the webhook's `AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_FEDERATED_TOKEN_FILE` set, the worker drove azurerm 4.65.0 through the federated-token exchange (Floci's `/{tenant}/oauth2/v2.0/token`) to create and destroy a resource group through the API, and a plan with the token file missing ended `failed` with nothing changed. The azurerm backend on 1.16.5 accepts `use_aks_workload_identity` and authenticated the same way (by hand; blob state itself was not reachable on the emulator). Real Entra federated credentials, the AKS webhook and Azure RBAC remain unverified until the work cluster. |
| `FORGEAPI_ALLOW_UNAUTHENTICATED_REMOTE` | Leave unset at work. With `FORGEAPI_AUTH_MODE=none` the API answers only loopback callers and returns 401 to everyone else; `true` removes that guard and exists for the lab emulator demos only. It has no effect in `entra` or `easyauth` mode. |
| `FORGEAPI_TENANTS_SOURCE` | `file` (default): business units come from `FORGEAPI_TENANTS_PATH`/`FORGEAPI_TENANTS_YAML`, unchanged. `db`: from the `teams` table in the operations database, managed through the operator-only `/admin/teams` API and the portal's Teams screens; the tenants file is ignored. Move with `POST /admin/teams/import` (dry run, then `apply: true`). |
| `FORGEAPI_OPERATOR_GROUPS` | Comma-separated Entra group IDs of operators in `db` mode (team admin, reconcile). Never stored in or editable through the database, so nobody can grant themselves operator rights through the API. In `file` mode operators still come from the mapping's `operators` list. |
| `FORGEAPI_AUDITOR_GROUPS` | Comma-separated group IDs allowed to read the audit trail in `db` mode (file mode keeps the mapping's auditors). |
| `FORGEAPI_LIVE_GROUP_CHECKS` | Default false. With `entra` auth, before an app rollout accepts its router or an app teardown accepts its replica destroys (work accepted later in the requester's name), the worker asks Microsoft Graph (`getMemberGroups`) for the requester's current groups and keeps only snapshot groups they are still in: removal from a group takes effect (`requester_access_revoked`), membership added later is never gained. Uses the worker's Azure identity (`DefaultAzureCredential`; on AKS the workload identity), which needs Graph `GroupMember.Read.All` (application permission, admin consent). If Graph cannot be reached the app waits and retries each cycle (`group_check_unavailable`) instead of failing; a missing permission therefore stalls rollouts at that step until fixed (7-day rollout limit). A requester with no `oid` (the `sub` fallback) resolves to no groups and is refused. Requests themselves always use the groups in the caller's current token. Proven against a real Entra tenant from the home lab (2026-10-04). |
| `FORGEAPI_DRIFT_SWEEP_MINUTES` | Unset or 0 (default) = no scheduled drift checks. When > 0, the worker starts `DriftSweepWorkflow` (`forgeapi-drift-sweep`): each cycle it checks up to 20 idle `ready` resources not checked within the interval, as actor `system:drift-sweep` using each resource's stored placement (under tenancy only if its business unit/environment is still configured). The value is re-read every cycle; 0 ends the workflow. Suggested at work: `360`. |
| `FORGEAPI_VERSION_CACHE_SECONDS` | Default 60; 0 disables. Per-process cache of each pattern repo's newest tag used for `latest_version`/`upgrade_available` (5 s git timeout; failures cached as unknown for the same time). |
| `FORGEAPI_PLAN_MAX_AGE_HOURS` | Set to `24` at work (the AKS ConfigMap does). A `planned` operation older than this cannot execute: execute returns 409 `plan_expired` and the operation becomes `failed` (`operation.expire` event, plan file deleted, resource and budget reservation released), and a new intent on a resource held only by an expired plan expires it and proceeds. Planned operations show `plan_expires_at`. Unset keeps plans executable indefinitely. Must be greater than 0. |
| `FORGEAPI_TENANTS_PATH`, `FORGEAPI_TENANTS_YAML`, `FORGEAPI_DEV_GROUPS` | Tenant mapping and local development groups. Configure tenant mapping on the API. With no mapping, operations are visible only to their creating identity. |
| `FORGEAPI_GITHUB_TOKEN` | Runtime `gh auth token` only, never persisted. Catalog reads occur in the API; module fetches in the worker. |
| `FORGEAPI_GITHUB_APP_ID`, `FORGEAPI_GITHUB_APP_INSTALLATION_ID`, `FORGEAPI_GITHUB_APP_PRIVATE_KEY`, `FORGEAPI_GITHUB_HOST`, `FORGEAPI_GITHUB_API_URL` | Existing GitHub App/private repo support, including GHES. Private key through approved secret references only. |
| `FORGEAPI_AZURE_TENANT_ID`, `FORGEAPI_AZURE_SUBSCRIPTION_ID`, `FORGEAPI_AZURE_CLIENT_ID`, `FORGEAPI_AZURE_CLIENT_CERTIFICATE_PATH` | Worker-only Azure identity. Approved short-lived local certificate, no client secret. Tenant placement selects target subscription; caller cannot choose it. |
| `FORGEAPI_AZURE_USE_MANAGED_IDENTITY`, `FORGEAPI_AZURE_MANAGED_IDENTITY_CLIENT_ID`, `FORGEAPI_AZURE_FEDERATED_CLIENT_ID` | Existing managed-identity/federation path retained; new runtime hosting with these identities is not proven. |
| `FORGEAPI_STATE_RESOURCE_GROUP`, `FORGEAPI_STATE_STORAGE_ACCOUNT`, `FORGEAPI_STATE_CONTAINER` | Optional existing azurerm Terraform backend. Backend key uses the stable resource ID. Remote Terraform state does not remove the need for a local operation ledger and saved plan. |
| `FORGEAPI_TEMPORAL_ADDRESS`, `FORGEAPI_TEMPORAL_NAMESPACE`, `FORGEAPI_TASK_QUEUE` | Active on API and worker; must agree. Defaults: `localhost:7233`, `default`, `forgeapi`. All workers on this queue must see the same ledger and saved plans. |
| `FORGEAPI_STALE_AFTER_SECONDS` | Legacy deployment sweeper only. New operations use Temporal activity timeouts. |
| `FORGEAPI_TABLE_STORAGE_ACCOUNT`, `FORGEAPI_TABLE_NAME`, `FORGEAPI_TABLE_CONNECTION_STRING` | Legacy-only; not an agent operation store. |

Agents use the same HTTP authentication as other API callers. No MCP server, API key or LLM dependency is installed.

Authenticated callers must have a nonempty string identity. JWT uses `oid`, with `sub` fallback
when `oid` is absent, null or empty; malformed identities and group claims receive generic 401.
Easy Auth requires a valid principal envelope and object-ID claim from the trusted proxy.
Unknown additive claims remain allowed. Malformed callers cannot share a fallback identity;
operation mutations record a sanitized refusal before any acceptance or dispatch. This shared
authentication fix also applies to the legacy API. Local `none` mode is unchanged. Local signed
tokens and proxy-header fixtures do not establish hosted Entra/proxy integration.

## Pattern and data requirements

Patterns must be trusted, executable Terraform root modules in their own repositories. Required variables, types, validation, sizing and cost estimates remain in the pattern repo. Tenant-injected inputs are hidden and cannot be overridden. Terraform stays deterministic with respect to its accepted source/inputs; no model-generated code is run by the control plane.
Both lightweight and annotated nonversion tags (for example `stable`) are ignored. Annotated
version tags resolve to their peeled commit, preserving the existing supported version syntax
and latest-version ordering. This is verified with a real temporary Git repository.
Optional pattern `config.yaml` must be a YAML mapping or empty/null. Malformed YAML and
nonmapping roots return sanitized catalog 502 before acceptance or dispatch. Validation and
description remain audit-free; a refused submission is audited. Valid mapping contents and
missing-file behavior are unchanged; this is not comprehensive nested configuration validation.
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
Catalog Git commands have a fixed 60-second deadline. On timeout, the API terminates the owned
process group and allows up to five seconds for cleanup, then returns sanitized 502 without
accepting or dispatching the intent. No automatic retry occurs. Local tests cover stalled
listing/fetch and a child retaining stdout, including a subsequent successful checkout.
This bounds catalog subprocesses only: credential acquisition, cache-lock waiting, total
request duration and Terraform execution have separate behavior. It does not repair arbitrary
Git lock files left by an interrupted fetch.

Use references for secrets. Caller-visible sensitive inputs are refused. Sensitive output values and outputs containing the configured subscription ID are withheld; only their names appear in `withheld_outputs`. New activity logs retain command/exit receipts, not raw provider or auth diagnostics. Failed and uncertain operations caused by a Terraform command carry `diagnostic` (capability `failure_diagnostics`): Terraform's first error with its command (`terraform plan failed: ...`), with placement IDs, GUIDs, 12-digit account numbers, URLs and `/subscriptions/` paths replaced by `[redacted]`, any credential/permission error replaced by a fixed sentence, and at most 400 characters. Other failures, successes and older operations have `null`. Terraform state and plans may contain sensitive material and require protected storage. Do not expose their filesystem over HTTP.

The old deployment database and existing Terraform state remain intact. There is no automatic import into the new resource ledger. Do not run old and new mutations concurrently for shared resources or budgets. Old applications remain at their previous release until migration is explicitly designed and verified.

## Known limits and report

API/worker local disk on one host; no distributed fencing, remote plan store, automated reconciliation of uncertain operations, cancellation of queued or running work, or production cloud hosting evidence. Pattern version upgrades work only for patterns whose state lives outside the workspace (azurerm backend, or a local backend path outside `work/`); a pattern keeping `terraform.tfstate` in its workspace fails at planning with state untouched. Budget estimates are author-provided, not provider billing. Azure Table and the previous separate-ACA deployment shape are unsupported by the new ledger.

Terraform activities run once (plan timeout 10 minutes, apply 30 minutes). Worker loss does not replay an apply; the workflow records `uncertain` after timeout when a worker is available. Each phase gives Terraform a deadline inside that timeout (8 minutes for plan, 28 for apply): the worker interrupts the Terraform process group, waits 60 seconds, then kills it. A plan that hits the deadline is `failed`; an apply is `uncertain`. If the worker itself dies, its Terraform child is not killed. An apply that Terraform refuses before changing anything (stale saved plan, or state lock held by another run) is `failed`, not `uncertain`; the lock is never force-released. Provider installation (`init`) is serialized; a running plan or apply no longer delays another resource's `init`. Late completion cannot clear `uncertain`. Queued work and plans survive worker restarts. There is no transactional outbox: an API crash between ledger acceptance and Temporal dispatch requires the caller to retry the original request. Do not discard operation IDs or create replacement work to recover transport errors.

`tests/test_dispatch_crash.py` verifies actual API SIGKILL at that boundary. Acceptance and
the resource reservation survive restart; a different-key intent cannot bypass the reservation.
No workflow starts until the caller repeats the original request, which returns the same IDs
and completes one plan. This local test stops before apply and does not establish hosted recovery.

Local interruption evidence now includes actual worker SIGKILL during real Terraform apply,
replacement-worker observation of uncertainty, one plan/apply, refused replacement work and a
surviving worker's late success write being fenced without a success audit event. The test-only
worker shortens the checked production 30-minute timeout to five seconds. These are local
process/Temporal tests, not real-cloud or 30-minute wall-clock evidence. Run
`uv run pytest -q tests/test_worker_interruption.py` in the development verification environment.
The fixture creates only temporary local files; it is not registered in the deployed catalog.

`tests/test_workflow_replay.py` replays captured plan, successful apply and controlled activity-
failure histories with normal timeout/retry settings. The apply used real local Terraform;
the failure uses a test-only activity and the production uncertainty writer. These current
baselines check determinism without rerunning Terraform. They do not prove a pre-versioning
upgrade, actual timeout, hosted recovery or arbitrary mixed-worker compatibility.

For an uncertain operation, preserve its operation/resource IDs, plan digest and audit events,
then inspect the worker and Terraform processes before interpreting the result. A dead worker
does not establish that its Terraform child stopped, and a local side effect does not establish
that Terraform committed state. Compare the protected Terraform state with independent provider
readback; do not print state or raw provider diagnostics into a ticket or agent transcript.
Preserve the ledger, Temporal history and workspace together. A consistent backup requires
quiescing all writers, including surviving Terraform children, not merely stopping the API.
There is no supported endpoint to clear `uncertain`: do not edit the ledger, unlock state,
reapply the saved plan or submit replacement work as an automatic recovery procedure.

Report the exact revision/image, target/storage feasibility, endpoints tested, operation IDs, independent provider/emulator readback, and every unverified boundary. Do not claim Floci results as Azure/AWS/GCP production proof. Storage consumption, the local HTTP client, interruption/dispatch-crash, plan/apply/failure replay and quiesced restore checks are complete. Stable pagination, client listing and local release checks are complete; release-tag consistency is verified locally; client event pagination passed local HTTP checks; malformed-request boundary proof passed; publishing action pins are verified locally. Operator reconciliation remains manual; real work-cloud identity is still unverified. Transactional remote operation storage, durable exact-plan storage and explicit state migration become necessary if distributed hosting is selected.
