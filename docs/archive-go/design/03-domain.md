# 3. Domain and specifications

**Status:** proposed. [Index](README.md).

**Current implementation:** the local core implements the bounded template/owner model and separate outcome/delivery/cleanup projections, not the full BU/team/classification registry below. Local deployment records form a separate aggregate in `internal/deployment` and migration 005; their [lab contract](openapi-deployments.yaml) binds a configured target/executor and saved-plan approval. They are not the full future Stack/Revision model. See [progress](../progress.md) for evidence and remaining acceptance.

## Ownership and permission model

`Principal(tid, oid, kind)` has explicit `Grant(permission, scope, validity, provenance)` relationships. `Application` belongs to registered owner teams and a BU; `ApplicationEnvironment` binds an app to an environment and permitted target registrations. A team may serve several applications and an application may span environments. An execution permanently records its admission-time owning app/BU/team binding; current grants control reads and cancellation after ownership changes.

The request selects `application_id` and `environment` as routing intent. The API verifies that relationship and derives owning team, BU, target, policies, cost center, and identities from its registry. No editable tag confers authority. Transfers preserve historical ownership evidence and require a reviewed registry change.

| Platform role | Initial permissions and scope |
| --- | --- |
| Developer | `execution.submit`, `execution.read`, `execution.cancel`, `execution.data.read`, `catalog.read` on explicitly granted app/environment; execution/data access defaults to own submissions and approved classifications; team-wide access requires an explicit grant |
| App owner | Same five permissions across owned app/environment where granted, with explicit classification limits; future production promotion independent of requester |
| Approver | Future approval decisions only for assigned authority/binding; no automatic execution/data access |
| Template administrator | `template.admin` and `catalog.read` on assigned catalog; publication through reviewed operator import in M1; no execution/data access by implication |
| Input publisher | `artifact.publish` on assigned app/environment and classifications; reviewed fixture/CI import only, not execution submission or output mutation |
| Platform administrator / operator | `platform.operate` in registered targets; explicit grant required for execution metadata/data read or cancellation; emergency actions audited |
| Auditor | `audit.read` and `execution.read` for sanitized metadata in assigned ownership scope; no submit/cancel or raw log/artifact access by implication |

The POC's six permission categories map to `execution.submit`, `execution.read`, `execution.cancel`, `template.admin`, `platform.operate`, and `audit.read`. Three supporting permissions complete the initial set: `execution.data.read`, `catalog.read`, and `artifact.publish`. A role is a proposed grant bundle, not authority inferred from a job title. Log/result/artifact routes require both execution access and data-read permission for the object's classification. Submission checks input ownership/classification too; `artifact.publish` alone cannot authorize consuming an input. All queries filter by current authorized scope, including collection pagination. Known-but-forbidden IDs return the same 404 representation as missing IDs; capability-level denials use 403. S02 includes metadata-read allowed but logs/results/artifacts denied, and unrelated catalog denial.

Grant scope kinds, inheritance and intersection rules are defined in [section 10](10-registry-decisions.md#grant-evaluation-and-target-selection); migrations and authorization code use that same model.

## Aggregates and storage sketch

| Record | Essential fields / constraints |
| --- | --- |
| Execution | `id`, principal/owner binding, accepted time/deadline, immutable spec digest/reference, state, cleanup, delivery, infrastructure status, revision, active lifecycle generation |
| TemplateVersion | `(template_id, version)` immutable; public input schema, approved image digests, command grammar, allowed overrides, profile constraints, output contract, revocation state |
| ProfileVersion | Logical ID/version/schema; internal target mappings, finite CPU/memory/disk/time/concurrency limits; mappings never returned publicly |
| PolicyDecision | Execution or denied-request ID, input digest, rule version/digest, decisions, sanitized reasons, authority snapshot |
| Artifact | Opaque ID, app/execution owner, input/output purpose, digest, bytes, media type, classification, retention/deletion, publication state; private storage locator separately |
| Idempotency | Unique `(tenant, principal, app, environment, operation_kind, parent_id, key_hash)`; canonical request hash, original response/Location, execution/cancellation ID, expiry |
| Cancellation | Unique execution cancellation intent; ID, authenticated requester, requested time, status/reason code; multiple keys may resolve to the same intent |
| ExecutionEvent | Unique `(execution_id, sequence)` and event ID; safe type/time/revision/data, no raw provider payload |
| ExternalAttempt | Execution/generation, logical step, stable external identity, request hash, dispatch state/times, attempts, observation reference, fencing generation, uncertainty |
| Outbox | Stable message ID, kind, record ID, created time, lease/version/next attempt, delivered time; unique logical delivery key |
| ReconciliationTicket | Execution, reason, next attempt, lease/fence, operator escalation, resolution evidence; survives workflow closure |
| LaunchClaim / RuntimeAssignment | Unique execution launch claim; pinned spec/node assignment, claim nonce hash, issued/consumed/closed status and expiry; consumption never resets after crash or reassignment |

The execution **is** the operation/status resource. There is no separate public job object. Workload result, artifact delivery, cleanup, and infrastructure confidence are separate fields:

- `state`: `accepted`, `provisioning`, `starting`, `running`, `succeeded`, `failed`, `cancelled`, `timed_out`.
- `cleanup_state`: `pending`, `running`, `succeeded`, `failed`.
- `delivery_status`: `pending`, `complete`, `partial`, `failed`.
- `infrastructure_status`: `not_allocated`, `present`, `unknown`, `absent`.
- `dispatch_status`: `pending`, `started`, `attention_required`.

These describe different facts. `succeeded + cleanup failed` is a visible operational exception. `timed_out + infrastructure unknown` means the platform deadline expired and termination/removal remains unverified. A failed required artifact does not erase the observed exit code; `result_complete=false` tells clients the promised result package is incomplete.

`error` describes the execution/workload failure or current provider uncertainty; `cleanup_error` and `delivery_error` preserve independent failure explanations. A cleanup failure cannot overwrite a nonzero workload result. A cancellation has its own error/status. Clear a transient observation error only with a new authoritative observation/event, retaining its history in audit.

### Status glossary and client completion

| Field | Meaning / completion rule |
| --- | --- |
| `state` | Last committed lifecycle/outcome, not a live heartbeat. Terminal: succeeded/failed/cancelled/timed_out. `failed` can describe a platform-control failure; `error.origin` distinguishes platform/provider/workload and an exit code is never invented |
| `cleanup_state` | pending = not begun; running = checking/removing; succeeded = absence/access/late-attempt proof complete; failed = bounded attempt exhausted and a ticket remains |
| `delivery_status` | pending = collection open; complete = required artifacts published; partial = some required artifacts missing but some usable evidence published; failed = required delivery could not be fulfilled. An unobserved exit can still keep result_complete false |
| `infrastructure_status` | not_allocated = no known allocation, not proof no late submit exists; present = observed resources; unknown = current presence cannot be established; absent = verified absence |
| `dispatch_status` | pending = no confirmed workflow start; started = historical start confirmed, even after workflow closure; attention_required = dispatch/recovery needs operator action. It is not a workload outcome |
| `result_complete` | All template-required result fields/artifacts are verified and published; independent of successful exit and cleanup |
| Cancellation `status` | requested = durable intent awaiting stop proof; acknowledged = stop/no-dispatch proven and cancellation won; superseded = another terminal outcome won; failed = cancellation budget/permanent error exhausted. Late stop evidence after a terminal outcome does not rewrite that outcome |

Clients can read each published artifact immediately; a partial result is not a complete result package. Stop ordinary result polling when state is terminal and delivery is not pending. Treat the operation as successfully finished only when `state=succeeded`, `result_complete=true`, `delivery_status=complete`, `cleanup_state=succeeded`, and `infrastructure_status=absent`. Surface failed cleanup/unknown infrastructure as an operational exception with ongoing recovery; do not poll forever waiting to hide that exception. Unknown future status strings require refreshing/updated client handling, not assumed success.

`started_at` is the observed container start and is absent if never observed; `observed_at` dates the latest authoritative observation; `completed_at` dates the committed terminal decision. `timed_out_from` records the previous lifecycle state at the accepted-to-finish deadline. Cancellation reason `superseded_by_new_request` describes caller intent; cancellation status `superseded` describes a completion race.

## Full execution transition rules

Only the following transitions are allowed. Same-state observations are no-ops for lifecycle state but may append distinct stage events. All unlisted transitions are rejected and retained for operator reconciliation.

| From | To | Required observation or decision |
| --- | --- | --- |
| accepted | provisioning | Workflow validated persisted admission and began reconcilable provider submission |
| accepted | failed | Persisted admission cannot execute (for example revoked image/current security denial) before allocation, or bounded cancellation control fails; record origin/reason |
| accepted | cancelled | Cancellation wins before submission; prove no submit attempt can still dispatch |
| accepted | timed_out | Accepted-to-finish deadline exceeded before dispatch |
| provisioning | starting | Assigned node/bootstrap or container preparation observed |
| provisioning | failed | Authoritative allocation/provisioning/image failure, or bounded cancellation control fails with platform-origin error; ambiguous submit alone is not provisioning failure |
| provisioning | cancelled | Requested stop confirmed or no dispatch/resources proven |
| provisioning | timed_out | Platform deadline expires; infrastructure confidence may remain unknown |
| starting | running | Container start observed |
| starting | failed | Bootstrap/image/container-start failure confirmed, or bounded cancellation control fails with platform-origin error |
| starting | cancelled | Stop confirmed before run or after an unobserved short run |
| starting | timed_out | Platform deadline expires |
| running | succeeded | Authoritative successful workload completion observed |
| running | failed | Nonzero exit or lost-worker termination confirmed, or bounded cancellation control fails with platform-origin error and no invented workload result |
| running | cancelled | Stop confirmed with cancellation as winning outcome |
| running | timed_out | Deadline expires before a terminal observation has been committed |
| any terminal state | none | Immutable outcome; later evidence updates infrastructure/delivery/cleanup and records discrepancies |

Fast completion may skip observations. An adapter returns an ordered batch of **observed** milestones where available; projection can traverse missing intermediate states in one transaction with `inferred=true` events and no invented timestamps. If a running observation was missed, it can advance through `starting/running` to the observed terminal outcome; startup metrics exclude inferred times. Never regress from running to provisioning on an old read.

Arbitration: before deciding timeout or sending cancellation, inspect when feasible within the remaining deadline. A terminal observation already committed wins over a subsequent cancellation. Otherwise the workflow commits one outcome using revision/generation checks; a timeout can commit without provider contact, while cancellation requires stop/no-dispatch evidence. Later evidence cannot silently rewrite the result. A cancellation arriving after committed terminal state returns 409. Concurrent completion can turn an accepted cancellation into `superseded`; record the race. A cancel API failure keeps cancellation `requested` and records `cancellation_failed`, with cleanup/reconciliation continuing.

Cancellation has an explicit profile-bound deadline (initial proposal: requested time + 120 seconds, capped by the execution deadline). Reconcile and re-request stop on each bounded inspection until stop is proven, a competing terminal outcome wins, or that deadline expires. On cancellation-budget exhaustion, commit `state=failed`, `error.code=cancellation_failed`, `error.origin=platform`, cancellation `failed`, and current infrastructure confidence; keep unobserved exit code absent. If the execution deadline expires first, commit `timed_out` instead. Never commit `cancelled` with presence/stop unknown. Cleanup has its own deadline/ticket and continues in either case. These default budgets require V08/V11 acceptance, not assumed cloud timing.

## Cleanup and result transitions

| From | To | Condition |
| --- | --- | --- |
| pending | running | Terminal decision or stop intent requires reconciliation and cleanup |
| running | succeeded | No workload compute remains, scoped access revoked/expired, workspace removal evidenced, and outstanding external attempts resolved |
| running | failed | Bounded cleanup deadline exceeded or permanent error; persist exception and reconcile ticket |
| failed | running | Durable retry generation acquired by workflow/reconciler |
| succeeded | failed | Later verified orphan/access finding invalidates earlier evidence; security event and ticket required |

All other cleanup transitions are forbidden; repeats do not duplicate effects. No-allocation paths still traverse `running → succeeded` with evidence that no external submission can arrive late. Artifact collection is bounded and cannot postpone forced termination or cleanup indefinitely. Delivery progresses `pending → complete/partial/failed`; later verified recovery may promote partial/failed to complete with a new event and revision. Published objects are immutable; malicious/inconsistent output is quarantined.

## Worked PR-validation request

The values below are **synthetic fixtures**, not approved image/source identities. The source artifact registry binds `source-01` to an immutable repository ID, full commit, archive digest and classification. In M1 use fixtures; in M2 replace with reviewed artifacts.

```json
{
  "application_id": "software-factory",
  "environment": "development",
  "template_id": "pr-validation-v1",
  "template_version": "1.0.0",
  "image": "registry.example.com/software-factory/reviewer@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "command": ["review", "--source-ref", "artifact:source-01"],
  "compute_profile": "general-medium",
  "execution_class": "isolated-vm",
  "placement_policy": "us-data-residency",
  "network_profile": "software-factory-restricted",
  "timeout_seconds": 1800,
  "environment_variables": {"RUN_MODE": "pull-request"},
  "secret_refs": [],
  "input_artifact_refs": ["source-01"]
}
```

`environment` identifies the governed deployment environment; rename the source example's environment-variable map to `environment_variables` to avoid ambiguity. Timeout runs from acceptance through workload finish, including queue/allocation; cleanup has an independent bounded deadline. Additional profile startup/runtime ceilings may shorten the admissible maximum. The proposed demo has no GitHub secret because source is packaged upstream.

Template requires one source artifact, fixed entrypoint digest, exact command grammar, and `RUN_MODE=pull-request`; permits a timeout at or below its ceiling and only listed compute/profile choices. It freezes helper-image digests and a reviewed Compose definition supplied by the template; caller source supplies tests, not arbitrary Compose host/mount settings. Unknown fields, mutable tags, arbitrary commands, unrestricted environment keys, provider fields, and unregistered artifact/secret references fail admission. General OpenAPI schemas provide structural limits; template schemas impose these narrower rules. Resolve logical profile versions once, persist their immutable digest, and recheck revocation/security eligibility before any external effect.

### Caller inputs and template expansion

Only `application_id`, `environment`, `template_id`, `template_version`, and `input_artifact_refs` are structurally required from the caller. The full request above remains valid as an explicit echo of the resolved settings. Immutable template versions supply omitted defaults; the server persists a complete fourteen-field execution specification before admission. JSON Schema `default` annotations alone do not perform expansion.

For `pr-validation-v1`, `allowed_overrides=[compute_profile, timeout_seconds]`. The recommended default profile is `general-medium`, timeout 1800 seconds; the template's approved profile list and timeout ceiling constrain overrides. Image, execution class, placement/network profiles, `RUN_MODE` map and empty secret list are fixed. The server derives the exact command `[review, --source-ref, artifact:<sole input ID>]` from the single source binding; no shell substitution or caller-provided command template is evaluated. A caller may omit a fixed/derived field or echo the exact value; a different value is rejected. Maps and arrays replace whole values, never implicitly merge. Null is not omission. Identity/context fields and source references are caller inputs, not overridable template defaults. The catalog input schema describes these rules; template publication rejects missing defaults/bindings or invalid fully expanded fixtures.

Input intake in M1 is a reviewed fixture import; in the live demo an approved CI/operator publishing path uploads and verifies the source archive before registering it. No public upload endpoint is needed for this slice. Artifact state is `staging → verified → published`, with quarantine/deletion branches. Consumption requires `execution.submit` for the selected context/classification intersected with the input's explicit allowed-consumer bindings; pin its digest at admission. Shared inputs need an approved app/environment consumer binding, not an implied grant from knowing the ID. Another execution's private output is not automatically a reusable input. There is no public input-download endpoint; output/log reads require `execution.data.read`. The output contract permits at most 16 registered artifacts and enforces profile byte limits before publication.

The import manifest contains `schema_version`, opaque artifact ID, app/environment, allowed-consumer bindings, immutable repository ID/full commit, SHA-256 archive digest, bytes, media type, classification, expiry and publisher provenance. Storage locators are internal. The authenticated importer requires `artifact.publish`; it verifies bytes, safe archive paths, source provenance and duplicate-ID consistency before atomically registering publication. A publisher cannot authorize consumers outside its own approved publication scope. M1.1 defines/tests the manifest, M1.3 imports fixtures, M2.1 delivers the trusted live importer with scoped staging-write/verification privileges, and M3 exercises the same path end to end. No arbitrary Git URL fetcher or additional product endpoint is introduced.

The representative output contract requires observed exit code, sanitized test logs and one named artifact `sbom` from `sbom.spdx.json` (`application/spdx+json`, SHA-256, profile-bound byte limit). Names are unique template output keys, not paths. The pinned workload tooling emits an SPDX JSON SBOM for the packaged source; its exact tool/version and test fixture are fixed in M2.1. Missing SBOM makes `result_complete=false` and delivery partial/failed even when tests exit zero. Admission acquires input-retention leases through the maximum execution/cleanup window; deleting one execution never deletes a shared input still leased by another.

## Later stack model — no implementation in M1–M3

`Stack → StackRevision` is immutable desired intent. `DeploymentTarget(stack_id, environment_id, registered_target_id)` owns one state lineage and serialization lease/fence. `OverlayVersion` renders a destination configuration; `PlanAttempt` binds rendered spec, toolchain/module digests, state snapshot, plan hash, estimate and policy. `ApprovalDecision` binds that exact attempt. Promotion references the base revision/module bundle plus a new destination overlay and plan. An execution can finish and disappear; a deployment persists across revisions. Share identity/outbox/artifacts/audit, not aggregate lifecycle or provider executor.
