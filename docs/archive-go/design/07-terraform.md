# 7. Terraform design

**Status:** proposed. M2 foundations and M4+ stack executor are separately authorized milestones. [Index](README.md).

## Implemented local spike

The engineer separately approved one empty Key Vault before the general stack milestone. That create is complete; no additional provisioning follows from this document. [ADR-0013](../adr/0013-local-terraform-lab-exception.md) records the scope/identity exception; [runbook](../key-vault-demo.md) and [lab OpenAPI](openapi-deployments.yaml) describe the actual interface.

- API admission/approval persist deployment records and outboxes atomically. A native worker executes the embedded content-digest-pinned pattern, with **Terraform 1.15.9 / AzAPI 2.11.0 / vault ARM API 2025-05-01**; provider locks cover Linux amd64 and both Mac architectures. No AzureRM, terraform-exec/json library, remote module fetch or existing-repo execution is implemented.
- One configured RG/name/region/owner, exactly one retained empty Standard vault, ARM-only, RBAC enabled and public access disabled. Saved-plan digest/expiry, current grant, target, pattern and executor bindings are checked before apply. No update/delete/import, raw caller Terraform, automatic retry of an uncertain apply, or second mutation owner.
- Current ARM/provider auth is certificate-only under a dedicated lab SP with RG-scoped Key Vault Contributor. The API caller has no forwarded ARM credential. The UAMI is retained inventory, not current execution auth. No hosted MI endpoint/adapter or federation is configured. See [identity limits](../terraform-identity.md).
- Backend is restricted local state in persistent host workspaces, not the future Entra Blob backend. Plans/state and rejected attempts remain retained as recovery evidence. Approval is same-owner lab approval, not independent production approval or a pricing/budget gate.
- The original create succeeded under human CLI auth; the replacement identity passed actual ARM/Terraform reads only. The original rejected attempt was marked `rejected_no_effect` only after exact no-effect proof. Generic partial-apply/import/restore recovery remains unimplemented. Do not reapply old plans, erase evidence or create another resource to establish a test result.

The sections below remain future foundation/general-stack requirements, not a claim that their backend, isolation, pricing or recovery controls exist in this spike.

## Immediate foundation ownership

Use one pinned Terraform toolchain for platform foundations. Candidate **Terraform 1.16.0** is published; it has not been installed or tested for this project. Pin exact CLI checksum, AzureRM provider version/checksums and module commits after the M2 compatibility spike. Do not label a candidate “tested.” OpenTofu remains an evaluation only if licensing, organizational support, or a required feature presents a concrete benefit; there is no dual-tool promise. [Terraform 1.16.0 release](https://releases.hashicorp.com/terraform/1.16.0/).

| Foundation object/property | Owner | Runtime change rule |
| --- | --- | --- |
| ACA environment, applications, identities, networking, fixed replica/concurrency configuration, image/revision settings | Terraform through reviewed foundation/release pipeline | Pipeline supplies reviewed immutable API/worker/runtime image digests to Terraform; no separate CLI image/revision writer or blanket ignore_changes workaround |
| PostgreSQL server/network/backup/Entra configuration | Terraform; separately reviewed DB bootstrap/migrations | Application identities have no schema ownership |
| Batch account, network, foundation identities and RBAC | Terraform/platform team | Batch service/controller privileges scoped and reviewed |
| Runtime pool/job/task/node and pool capacity | Batch adapter using Batch APIs | Not represented as per-execution Terraform resources |
| Approved pool blueprint | Versioned platform config with Terraform-managed dependencies | Immutable spec references, runtime instance created by adapter |
| Gallery/registry/vault/storage and protection settings | Terraform | Image publishing pipeline owns image versions/artifacts; no mutable “latest” execution selection |
| Application stack resources later | Stack Terraform executor | SDK cannot rewrite tags/RBAC/diagnostics owned by Terraform |

Pre-provisioned BU subscriptions stay external platform-team ownership. M2.0 includes approved POC ACA environment/apps/revisions, API/control/provider/runtime identities, PostgreSQL access, Batch, image, network, artifact and mTLS dependencies. Reuse approved existing foundations through explicit references and ownership; do not redeploy enterprise Temporal or ingress by assumption. Verify the API/worker development deployment before claiming V14/V15. M2.1 adds the minimal runtime-role image/configuration before untrusted Batch testing.

Foundation state is separate from future application stack state. Bootstrap backend storage and deployment identity via the organization's established process; never leave bootstrap state containing secrets in Git. Plans/reviews precede live changes within separately approved scope.

The M2 foundation backend uses the same Entra-only `azurerm` authentication and container-scoped locking rules below, with its own state container and federated deployment identity; no storage-account key lookup fallback. Both engineers review the platform plan/image digest change, with platform/security approval for privileged scopes, before the authorized pipeline applies it. Application-stack approvals and future manager-routing services are not prerequisites for this human-reviewed foundation path.

## Future stack executor — M4 and later

Store API-owned immutable stack revisions and overlays in PostgreSQL with digested bundles in approved object storage. One state blob belongs to `(stack_id, environment_id, target_registration_id)`, stable across revisions. A parent dependency graph tracks which stack owns shared resources; reject destruction that would remove another stack's resources until dependency changes are reviewed. Cross-target state is never reused.

Moving an existing deployment to a new subscription target requires a reviewed recreate/cutover or supported resource-move/import procedure and new state ownership; changing the registry binding is not migration. Preserve the old target/state until resource and dependency reconciliation supports retirement.

The approved catalog uses a sidecar public schema with module repo/tag, resolved commit/content hash, transitive dependencies, policy set, required metadata, usage hints, owner and onboarding tests. CI verifies sidecar versus Terraform inputs/outputs; it does not expose raw Terraform provider schemas or accept arbitrary source URLs. Tags alone are mutable and the Terraform dependency lock file locks providers, not the module tree. [Dependency lock file](https://developer.hashicorp.com/terraform/language/files/dependency-lock).

Bundle pipeline: resolve all tagged module repositories recursively; pin every commit and content digest; vendor local module paths; include platform-specific provider mirror archives and lock checksums, CLI/toolchain identity, sidecars, SBOM/provenance/signature and manifest. Publish immutable blobs through GitHub-to-Azure OIDC federation. Approved CI accesses private repositories through scoped GitHub installation tokens or an approved packaging feed; do not pass GitHub credentials to runtime Terraform workers. Azure federation does not grant GitHub repository permission. [GitHub Azure OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-azure).

Workers download and verify the bundle through Azure SDK/managed identity, extract safely, and run pinned `terraform-exec`/`terraform-json` only after compatibility testing against the selected CLI/provider/schema versions. Configure local module paths and a **filesystem** provider mirror with explicit provider-installation rules that omit direct network fallback. Treat shared mirror content read-only; use a fresh execution directory and writable local data directory per attempt. Prove initialization succeeds with public network denied and fails if a required dependency is missing. Terraform network-mirror credentials and backend credentials are separate mechanisms; downloading a bundle first avoids assuming they share Entra authentication. [Mirror protocol](https://developer.hashicorp.com/terraform/internals/provider-network-mirror-protocol).

Executor workers have explicit CPU/memory/disk/process/concurrency/time ceilings, protected per-attempt directories and target-scoped identities. Bound diagnostic/output bytes and retain encrypted restricted recovery evidence before removing a failed workspace. V19 selects the worker shape using measured init/plan/apply behavior; no unbounded general shell worker is implied.

Backend configuration uses `azurerm`, `use_azuread_auth=true`, and selected managed/federated identity settings. Grant data-plane access at the state container boundary and only required endpoint discovery permission. Use native blob locking as a second layer, encryption, reviewed soft-delete/versioning and recovery. Avoid account keys/access-key lookup fallback. Backend parameters, `.terraform` data and saved plans can capture sensitive configuration; credential handling must be reviewed. [AzureRM backend](https://developer.hashicorp.com/terraform/language/backend/azurerm).

## Plans, ordering, and approval binding

Serialize mutations of the same deployment target using a durable operation owner/generation and recoverable lease. Different targets can execute concurrently if dependency and budget rules permit. The active external attempt remains the owner through ambiguous completion; lease expiry alone must not enable a competing apply. A higher-level multi-stack operation may use child workflows when actual dependencies justify it.

Generate a refreshed saved plan in an isolated worker directory. That exact plan produces the sanitized preview, estimate, policy input and eventual apply. Do not optimize through routine targeting or disabled refresh. Measure queue, bundle/init, refresh/plan, approval and Azure provisioning independently. [Plan command](https://developer.hashicorp.com/terraform/cli/commands/plan).

Persist an immutable execution binding: stack revision, overlay/target registration versions, module bundle/CLI/provider digests, state lineage/serial, plan hash, policy version, pricing snapshot and estimate assumptions, budget reservation and approval decisions. Expire a plan after its approved freshness window (V19), and invalidate it on spec/target/module/toolchain/policy-relevant changes, state mutation, approval revocation or reservation expiry. State serial alone cannot prove absence of external drift. If a fresh plan differs materially, require new review/approval.

Invalidation or expired freshness queues a new plan attempt, never an apply of the old binary plan. Compare the sanitized resource-action set, sensitive-change indicators, ownership/target, policy, estimate and authority binding under a versioned materiality rule; unknown differences count as material. Preserve the old plan/decision reference for audit. V19 decides any safe reuse of approval for an equivalent new plan; default is new approval for changed binding.

Before apply, recheck current caller/sponsor authority, policy eligibility, budget and approval validity, then acquire mutation ownership and native state lock for execution. Do not retain an SQL transaction or blob lease while waiting for a person. The approval record is authorization evidence; a Temporal signal only wakes the workflow. Production promotion uses the same base spec/module artifacts with a new destination overlay/state/plan and independent destination authorization. No dev plan is applied to production.

## Secrets, delegated RBAC, and uncertain apply

Prefer patterns without generated secrets; application secrets use application-specific Key Vault references and managed identities. `sensitive` redacts output but does not guarantee omission from state/plan. Terraform documents ephemeral support beginning in 1.10 and write-only resource arguments in 1.11; each chosen AzureRM resource must independently support the needed feature. OpenTofu equivalence is unverified. Inspect synthetic secret-bearing plans/state in a controlled test before enabling a pattern. [Sensitive data](https://developer.hashicorp.com/terraform/language/manage-sensitive-data).

Raw state/plans/JSON/stdout/work directories/backups are restricted; expose sanitized summaries and remove raw ephemeral artifacts on a separate schedule. Never put raw plans, secret values, or work directories into Temporal/Git. Provider worker Contributor does not confer role-assignment rights; a constrained deployment path must enumerate allowable roles/principals/scopes and deny self-escalation. Unknown planned values affecting RBAC/network/data exposure fail closed or require an explicit governed assessment, never automatic safety. [Privileged Azure roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged).

| Failure | Recovery obligation before any retry |
| --- | --- |
| Worker dies after Azure mutation | Inspect attempt, Terraform process/lock ownership, state and cloud resources; reconcile/import only under reviewed procedure |
| Azure mutation succeeds, state write fails | Preserve restricted diagnostics and ownership; recover state from evidence, do not repeat apply blindly |
| Apply acknowledgement lost | Same execution binding; inspect result/state/actual resources |
| Lock survives worker loss | Prove no active owner/process before a separately governed unlock; never automatic force-unlock |
| Cancellation during apply | Stop safely where supported, reconcile partial infrastructure and billable resources; cancellation is not rollback |
| Bundle/provider digest mismatch | Reject before init/apply, quarantine artifact and suspend affected pattern |

Maintain explicit failure states such as `apply_uncertain`, `recovery_required`, and `partially_applied` in the later stack domain. These are not new compute execution states. M4 exposure remains constrained to patterns/scopes whose required pricing, security and approval gates exist.
