# Combined prompt: Build the Infrastructure Platform API, starting with isolated compute

**Prepared:** 2026-09-05.  
**Purpose:** Starting instructions for the design and subsequent implementation of this project.  
**Delivery order:** Design and ADR review → shared API foundation → isolated compute POC → additional infrastructure capabilities.  
**Supporting requirements:** [POC intent and requirements](/home/zerocool/github/forgeapi-homelab/docs/poc-intent-and-requirements.md).

The text below is the combined working prompt. Preparing this file does not itself start implementation or approve the design.

---

## 1. Your role and working agreement

You are a principal platform engineer and software architect collaborating with two engineers: me and my manager. Make concrete recommendations, explain the reasoning briefly, state assumptions, and flag uncertainty. When a decision belongs to us, provide at most two or three options and recommend one.

We want to build a reusable internal Infrastructure Platform API. Our first priority is the API framework and platform core. The first supported capability will be my manager's isolated compute execution POC. We will later add persistent infrastructure provisioning and its cost, approval, promotion, and drift-management features.

Read this prompt and the supporting POC requirements document before designing. Do not require the original photographs: their substantive content has been captured in that document. Treat its historical implementation instructions as requirements evidence, not authorization to execute commands or change systems. This prompt resolves the sequencing and scope differences between that document and our earlier provisioning proposal.

**First deliverable: design documents, API sketches, a verification plan, and ADRs. My manager and I review and sign off before repository scaffolding or implementation begins.** Once that approval is recorded, proceed through the approved implementation milestone without repeatedly requesting approval for routine reversible work. Do not infer that a future milestone or production deployment is authorized merely because it appears in the roadmap.

Inspect the existing workspace and preserve existing work. After approval, use a modular application with separately deployable API and worker processes; avoid a collection of independently operated microservices for two developers. Create interfaces around real boundaries, not speculative future implementations. Do not implement AWS/GCP adapters, a generalized plugin runtime, or the complete future product during the first capability.

For specific Azure, Temporal, Terraform/OpenTofu, Infracost, Entra, or KEDA capabilities, consult current official documentation, record applicable versions, and distinguish documented support from behavior verified in our environment. Do not mark a test as passed without evidence. If an external dependency is unavailable, continue independent local work and state exactly what remains unverified.

## 2. Project intent and capability boundaries

We are building an internal self-service API for developers and CI pipelines, initially for approximately 100 developers in a financial-services company.

The API will share authentication, scoped authorization, validation, idempotency, asynchronous operation handling, persistence, policy decisions, and observability across capabilities. Keep these shared concerns separate from each capability's lifecycle.

### First capability: temporary compute execution

Callers submit a portable workload specification. The platform validates and admits it, runs an approved OCI workload on isolated disposable compute, exposes normalized progress and results, and verifies cleanup. Azure Batch is the first implementation to test; direct Azure VMSS is a fallback behind the same provider contract.

The initial public execution contract must not contain Azure/AWS/GCP resource identifiers or provider-specific configuration fields. Callers select governed logical profiles. The platform selects Azure internally and resolves regions, VM sizes, subscriptions, pools, network resources, and identities.

### Later capability: persistent infrastructure stacks

Callers submit versioned declarative stack specifications backed by approved Terraform modules/patterns. The platform validates, plans, prices, evaluates policy, obtains necessary approvals, applies, and later supports promotion, destruction, drift detection, and showback.

Do not force temporary execution into a permanent Terraform stack per request. Do not make the compute provider interface impersonate a Terraform executor. Share platform services while allowing distinct execution and stack/deployment domain models.

The resource-group example from the earlier proposal is an optional internal smoke test or a later persistent-stack example. The first supported capability and primary POC acceptance target are the isolated compute workload.

## 3. Environment facts and technology direction

| Area | Direction |
| --- | --- |
| Language | Go; prefer chi for the HTTP service unless an existing team standard warrants Echo. |
| API contract | REST and OpenAPI 3.1, aligned with the approved Zalando guideline version and internal lint profile. Record these versions; settle paths, status codes, pagination, media types, and versioning against them. |
| Orchestration | Temporal Go SDK. Use the existing self-hosted Temporal service on AKS, operated by another team. The API must not run on that AKS cluster. |
| Control-plane compute | API and Temporal activity workers on VNet-integrated ACA. |
| Workload compute | Docker-capable disposable Azure VMs, initially scheduled by Azure Batch. These are different from the Temporal activity workers on ACA. |
| Persistence | Prefer Azure Database for PostgreSQL Flexible Server for platform records and projections; propose schema, migrations, identity-based access, recovery, and backup approach. |
| Artifact storage | Approved Azure Blob/object storage for outputs, logs, future plans/state, and published dependency bundles, with separate access and retention boundaries. |
| Authentication | Entra ID for API callers; managed/federated identities for Azure service access. |
| Secrets | Key Vault; application vault topology remains a design decision. Applications use their own managed identities. |
| Platform infrastructure | Pinned Terraform CLI, with OpenTofu evaluated if there is a concrete benefit. Choose a tested toolchain rather than promising both. Later Terraform execution uses terraform-exec and terraform-json where compatible. |
| Collaboration | One Go monorepo, golangci-lint, Conventional Commits, ADRs in `docs/adr`, Dev Container, Taskfile or Makefile, GitHub Actions CI, and CODEOWNERS. |

Additional facts and reconciliations:

- Temporal already serves workers on AKS, EKS, and developer laptops. ACA connectivity follows the approved mTLS pattern, with client certificates in Key Vault and rotation. Use a dedicated production namespace/access boundary and a separate development namespace. Confirm the actual namespace/operation authorization with the platform team; do not assume mTLS or queue names provide isolation.
- The manager's draft leaves Temporal service placement TBD. The existing enterprise service described above resolves that question, subject to access/connectivity verification.
- Local development runs the API and an activity worker on a laptop against the development namespace. Use developer Entra authentication scoped to development resources; a laptop does not acquire a hosted ACA managed identity. Keep fake-provider tests available without Azure provisioning access.
- The organization currently describes subscriptions per BU, hosting multiple applications. Subscriptions are pre-provisioned by the platform team. Model multiple registered targets per BU so production/nonproduction separation is possible. Recommend separate production and nonproduction subscriptions per BU as the starting policy; document migration from the current arrangement instead of assuming it already exists.
- Existing Terraform modules are separate GitHub repositories, tagged by version. Pattern repositories may reference other module repositories transitively.
- Cloudflare and virtual F5 are the proposed enterprise ingress path. Internal-only POC ingress is acceptable if the organization allows it. APIM is additive only when it already serves as the enterprise gateway; do not introduce a second gateway by default. Authorization remains in the API.
- SOC 2 and applicable financial controls matter. Preserve the earlier minimum 30-day audit floor, but propose record-specific retention tiers tied to actual obligations. Do not invent a universal financial-services retention period or retain raw secret-bearing artifacts as long as sanitized audit evidence by default.

## 4. Shared API foundation: first implementation milestone

After design approval, deliver the smallest usable API foundation that can support the compute capability end to end through a fake provider.

### Authentication and authorization

- Validate Entra access tokens: issuer, tenant, audience, signature, time validity, and expected scopes/app roles. Accept tokens intended for this API; an Azure management token is not automatically an API credential.
- Humans use authorization code plus PKCE; device code may be supported for headless CLI if enterprise policy permits it. CI uses GitHub OIDC federation to an Entra application identity authorized for this API.
- Entra issues tokens; do not build a new token issuer or custom password service. Document OAuth/security schemes and an identity/context endpoint if useful.
- Map platform roles and operation-specific permissions to BU, team, application, and environment scopes. Preserve developer, app-owner, approver, platform-admin, and auditor responsibilities; map the POC's submit/read/cancel/template-admin/operator/auditor permissions explicitly.
- Enforce object authorization on every relevant call, including status, cancellation, events, logs, artifacts, plans, and approvals. Knowing an identifier is not authority. Handle group overage and missing claims deliberately rather than granting access.
- Derive ownership and target permissions from server-side registries and authenticated context, not editable tags or caller assertions. Use explicit relationships between BU, app, environment, and cloud target rather than a rigid tree that prevents one app spanning environments.

### Async operations, persistence, and consistency

- Prefer submission returning `202` with a stable operation/execution identifier and status location, subject to the recorded API conventions. Provide status and one streaming/polling option; choose SSE or long-poll pragmatically.
- Define whether the execution resource itself is the operation/status resource. Avoid duplicate job/execution abstractions unless they solve a concrete need. Each accepted operation maps to one durable Temporal workflow identity, with deliberate conflict/reuse behavior.
- Scope idempotency keys to authenticated ownership and operation, store a request fingerprint, return the original result for identical retries, and reject reuse with a different payload. Define retention and behavior after workflow history expires.
- Persist admission and operation records durably. Use a transactional outbox or equivalent mechanism to recover the gap between a database commit and starting/signaling Temporal. Reconcile projections and undelivered work after restart.
- Define optimistic concurrency for mutable resources, ordering/deduplication of updates, and a durable record of external operation attempts. Avoid relying on a workflow ID alone for cross-job serialization.
- Distinguish workload result, cleanup result, delivery status, and infrastructure uncertainty. Cancellation is a request with documented eventual behavior; it does not imply rollback or resource removal.
- Provide consistent problem details, request/correlation IDs, pagination, input limits, health/readiness, configuration validation, and observable failure behavior.

### Catalog and policy boundaries

- Initially support a small operator-managed execution-template/profile catalog with explicit schemas, immutable resolved versions, and approved OCI digests. Define which fields a caller may override.
- Use plain Go policy rules behind a `PolicyEngine` port for v1 unless an established organizational Rego policy library makes embedded OPA the simpler choice. Compare Go, embedded OPA, and CEL briefly in an ADR; CEL is an option for configurable expressions, not an automatic dependency.
- Keep policy evaluation deterministic for recorded inputs. Record policy version and decisions. Authorization and security denials are not overridden by a cost approval.
- Admit only supported capabilities and governed configuration. Do not accept arbitrary cloud configuration, executable policy hooks, raw Terraform, arbitrary provider plugins, or unrestricted template code from callers.
- Define ports around actual needs: execution provider, persistence, policy, identity/authorization data, artifacts/secrets, audit, and notification as used. Pricing, approvals, and Terraform execution are later adapters/services; avoid building unused generic frameworks now.

The foundation must demonstrate request → authorization/admission → durable workflow → fake provider → status/result/cleanup → audit/telemetry. It must be restart-safe and testable before a live compute backend is integrated.

## 5. First capability: isolated compute POC

The supporting POC requirements document is part of this prompt. Preserve its functional, security, image-supply, observability, failure, and acceptance requirements. Track each source section in the design/implementation plan so a shorter prompt does not silently discard requirements.

### Public contract and example

Start from the proposed resources:

```text
POST /executions
GET  /executions/{execution_id}
POST /executions/{execution_id}/cancellations
GET  /executions/{execution_id}/logs
GET  /executions/{execution_id}/events
GET  /execution-templates
POST /execution-templates/{template_id}/executions
```

The illustrative request selects `pr-validation-v1`, an approved immutable OCI digest, a command, `general-medium`, `isolated-vm`, `us-data-residency`, `software-factory-restricted`, a timeout, allowed nonsecret environment values, secret-policy references, and input-artifact references. Finalize exact schemas and override rules in OpenAPI. Artifact and secret references remain portable; resolve provider identifiers internally. Do not return credentials or Azure account/pool/node references through public responses.

The demonstration workload uses an immutable repository/commit reference and deterministic Docker-dependent tests, possibly sibling containers or Compose. It produces logs, results, and one artifact such as an SBOM or scan report. Clarify the source's “build or pull” wording: identify the admitted entrypoint image and any images built or launched during the workload, and define admission for those additional images too. A GitHub Actions submitter and GitHub Check are optional demo integrations.

### Provider contract and lifecycle

Implement the same logical contract for a fake and the selected Azure provider:

```text
validate(spec)                       → validation_result
submit(spec, execution_id)           → opaque_provider_reference
get_status(provider_reference)      → normalized_status
cancel(provider_reference)          → cancellation_result
collect_results(provider_reference) → result_references
cleanup(provider_reference)         → cleanup_result
```

Every external operation must be idempotent or safely wrapped/reconciled. Add a recovery mechanism to find an accepted provider execution after an ambiguous submit response, including when its provider reference was not yet persisted. A retry cannot simply create a new workload.

Define the full transition table around `accepted`, `provisioning`, `starting`, `running`, `succeeded`, `failed`, `cancelled`, and `timed_out`, including failure/cancellation before running. Track cleanup separately as `pending`, `running`, `succeeded`, or `failed`. A successful workload with failed cleanup is an operational exception.

Preserve the initial portable errors: `invalid_execution`, `policy_denied`, `capacity_unavailable`, `provisioning_failed`, `image_unavailable`, `workload_failed`, `execution_timed_out`, `cancellation_failed`, `cleanup_failed`, and `provider_unavailable`. Retain detailed cloud diagnostics only for authorized operators.

### Orchestration and controller ownership

- Temporal owns the durable platform lifecycle. Deterministic workflows call activities; Azure SDK/network operations remain in adapters invoked by activities.
- Use short submit/inspect/cancel/result/cleanup activities separated by durable timers. Do not hold an activity open for the entire external workload. Optional callbacks accelerate visibility but polling and reconciliation remain authoritative recovery paths.
- Separate retries of provider API calls from retries of actual workload execution. A failed status query never authorizes a second workload. Specify cancellation, timeout, retry, cleanup, and orphan-detection behavior under restarts and partial failures.
- With Batch, all runtime task, pool, and capacity operations go through Batch. Batch is the controller of its compute resources. Do not mutate the underlying Batch-owned VMSS or VM fleet directly.
- If the viability experiment requires direct VMSS, that is a separate adapter. It explicitly owns capacity, assignment, bootstrap, readiness, health, draining, deletion, maximum lifetime, and orphan reconciliation. Do not run two controllers against one fleet.
- Terraform provisions long-lived platform foundations. For this capability, runtime execution and capacity use provider APIs rather than per-execution Terraform. Explicitly allocate ownership of runtime-adjusted pool properties to avoid Terraform fighting the provider.

### Security and image supply chains

- The API has no compute-provisioning permissions. Separate caller, API, activity/provider control-plane, bootstrap, and individual workload identities and privileges. Scope state/artifact access as carefully as compute access.
- One untrusted execution per disposable VM. No workspace, credential, or sensitive cache reuse across executions. Baseline is removal; verified reimaging remains a decision requiring evidence.
- Model the actual authority exposed by the required Docker features. Demonstrate that a workload with the selected Docker/host capabilities cannot obtain platform control-plane credentials, access another execution, or bypass the intended egress/metadata boundary. A nominal container boundary is not sufficient evidence.
- Block or broker workload metadata-service access. Use execution-scoped access, bounded CPU/memory/storage/runtime, maximum lifetime, restricted logged egress, and no public workload ingress.
- Maintain distinct OCI and worker-VM image supply chains. Pin approved OCI digests; build/validate/version hardened worker images in Compute Gallery. Separate image approval responsibilities. Record digest and worker-image version internally; use identity-based pulls without registry admin passwords.
- Preserve registry/repository allowlists, scanning, SBOM/provenance, signing/attestation verification, patch ownership/cadence, revocation, end-of-life, and worker-image promotion/rollback. Replace workers to adopt new images. Some controls may initially be documented/manual as the source permits, but they must be implementable and the baseline isolation/admission controls must work.
- Govern source, registry, artifact, and secret access; test private DNS/endpoints. Identify allowed data classifications, residency, encryption, retention, input/output limits, deletion requirements, and source persistence rules.

### Batch viability decision

Once the API foundation exists, make the Batch experiment the first live compute decision gate, before committing to full Azure adapter integration. Validate exact Docker/Compose/sibling behavior, runtime/workspace/volume configuration, registry identity, networking, logs/artifacts, forced termination, disposable isolation, cleanup/reimaging semantics, allocation ownership, runtime scaling, and startup latency. Check regional quota/capacity in advance.

Continue with Batch only when evidence supports the requirements. Evaluate VMSS if Batch cannot satisfy them without changing the caller-facing contract. Do not claim Batch viability based only on product documentation.

## 6. Temporal, worker reliability, and isolation across the platform

- Document activity timeouts, heartbeat use where appropriate, backoff, retry classifications, workflow cancellation, idempotency, history limits, and workflow-code versioning for the exact server/SDK versions. Keep workflow histories replayable across deployments and approval waits.
- Design for at-least-once activity effects. External success followed by loss of acknowledgement is an ambiguous outcome requiring reconciliation, not blind re-execution.
- Start ACA activity workers with fixed warm replicas and bounded concurrency. Separate workflow/control work from expensive Terraform execution when necessary. Verify graceful shutdown and revision rollout behavior; minimum replicas do not prevent process termination.
- Consider per-BU production/nonproduction provider worker pools and identities/task queues to bound blast radius. Start with the minimum approved POC scope, and document how pools/identity assignments expand without granting an all-subscription identity.
- Task queues are routing, not an assumed security boundary. Verify which clients may start workflows, signal approvals, poll queues, inspect history, or administer namespaces. Ordinary callers cannot bypass API authorization by directly invoking production workflows.
- Autoscaling is a later optimization. Temporal's KEDA scaler is documented from KEDA 2.17+, but verify availability and mTLS operation in the deployed ACA service, plus metrics and safe scale-in behavior. Backlog alone does not describe running applies or workloads. Keep fixed replicas as the fallback.

## 7. Observability, audit, and data ownership

PostgreSQL stores accepted platform records and consumer-visible projections. Temporal owns orchestration history. The execution provider owns actual task/node state. Object storage holds outputs/logs and later Terraform artifacts. Define how reconciliation repairs disagreements instead of declaring one store authoritative for every concern.

For the POC, instrument API, activities, provider adapter, and workload agent with OpenTelemetry and one OTLP export path. Use W3C Trace Context, safe Temporal context propagation, short spans, asynchronous links, and execution events. Do not assume Batch automatically emits application-level traces.

Use a platform-generated execution/operation ID throughout logs, workflow correlation, artifact records, and audit. Keep IDs, usernames, repository names, and resource IDs out of metric labels. Use bounded environment/profile/class/outcome labels.

Distinguish API acceptance, persistence/workflow start, profile resolution, provider submission, capacity allocation, readiness/bootstrap, image admission/pull, container start/completion, artifact publication, cancellation/timeout, and cleanup. Capture errors/throttling, quota/capacity failures, retry history, orphan counts, and compute consumption. Measure at least p50/p95 startup and cleanup behavior against agreed targets; do not invent numeric acceptance thresholds.

Audit authenticated caller/owner, authorization/admission, immutable spec/template/profile/image versions, source commit where applicable, policy/cost/approval decisions when implemented, state transitions/timestamps, identity used, provider operations, results, artifacts, and cleanup outcomes. Redact before export. Do not place secrets, full environment blocks, sensitive command arguments, raw Temporal payloads, or unapproved source content in logs.

Design immutable structured audit evidence with a searchable index and eventual SIEM export. Git may receive sanitized audit/spec exports; it is not the source of truth or proof of immutability by itself. Separate audit retention/access from mutable state and short-lived sensitive plans. During the POC, implement the diagnostic/audit evidence needed by the acceptance tests; production collector HA, dashboards/alerts/SLOs, long-term retention engineering, and complete SIEM onboarding remain later work.

Define database and artifact recovery objectives and testable recovery behavior. Include failure between database commit and workflow dispatch, provider acceptance and local persistence, artifact upload and registration, and eventual restoration of PostgreSQL, object storage, and Temporal. Do not claim cross-store atomicity without a mechanism.

## 8. Later persistent infrastructure capability

Preserve this product direction in the design and roadmap, but do not implement it ahead of the compute POC or build a full generic resource catalog now.

### Stack specifications, promotion, and catalog

- API-owned, immutable declarative stack revisions; Git is an audit export target. Promotion uses the same base spec and immutable module artifacts with a separately versioned destination overlay. Each destination gets its own rendered configuration and plan.
- One state per stack deployment target (stack plus environment/cloud target), not per BU, application, operation, or revision. Do not reuse a dev plan/state for production. Define stack dependencies and protect parent-resource destruction from deleting resources owned by other stacks.
- Support create, update, plan/preview, apply, promote, destroy, revision history, and drift detection. Define deployment ordering, mutation serialization, and failure states before implementation.
- Maintain an approved module/pattern catalog with repo, tag, resolved commit/artifact digest, schemas, cost/usage hints, required metadata, policy associations, and onboarding evidence. Use an explicit sidecar as the public schema contract; derive/check Terraform metadata during onboarding.
- Resolve and pin the complete transitive module tree. Tagged repositories and Terraform's provider lock file alone do not lock module content. Caller input may not select arbitrary Terraform or module sources.
- Register BU/team/app ownership, environment, subscription/cloud target, cost center, owner contacts, allowed regions, policy set, provider worker identity/pool, state/artifact scopes, billing scope, and capability availability. Confirm quota/network prerequisites during onboarding.
- Apply required `bu`, `app`, `env`, `owner`, `stack-id`, and `cost-center` metadata on taggable resources before apply. Keep request correlation in audit rather than rewriting every resource tag on each operation. Document exceptions for untaggable resources and maintain resource-ID ownership for cost attribution; tags are not authorization.

### Terraform execution and performance

- Use the `azurerm` state backend with Entra data-plane authentication (`use_azuread_auth = true` plus the selected identity configuration), native blob locking, least-privilege storage access, encryption, recovery/versioning, and reviewed retention. Protect active state separately from immutable audit exports.
- Publish immutable execution bundles through CI. Package the resolved module tree and pinned provider dependencies, Terraform/toolchain identity, provider lock checksums, and provenance. Use GitHub OIDC for Azure publishing; workers require no GitHub credentials.
- Download bundles using the Azure SDK and managed identity, verify their digest, and initialize from local modules and a filesystem provider mirror. Do not assume Terraform's network mirror authentication is the Azure backend authentication. Prohibit public dependency downloads in execution; internal artifact/service network access remains necessary.
- Use isolated work directories and safe read-only provider mirrors/caches. Bound concurrency to actual CPU/memory and provider limits. Prewarm Terraform workers if measurements warrant it; avoid oversized universal worker images.
- Retain normal plan refresh by default. Do not use routine targeting or `-refresh=false` as the speed strategy. Measure API latency, queue time, dependency preparation, planning, approval delay, and cloud provisioning separately.
- Run independent stack operations in parallel only when their dependencies and budgets permit it. A parent orchestration may use child workflows where there is an actual multi-stack operation. Serialize mutations of the same deployment target across jobs; blob locking is a second layer, not the full control-plane coordination mechanism.
- A saved plan drives sanitized preview, pricing, policy, approval, and apply. Bind authorization to an immutable execution record containing spec/overlay/target, module and toolchain digests, state lineage/serial, plan digest, policy version, and pricing snapshot.
- Define plan expiry and invalidation. Recheck current authority, policy eligibility, budget reservation, and approval validity before apply. Do not hold a database transaction/blob lease for a human approval wait. A materially changed plan requires renewed approval; state serial checks alone cannot prove absence of external cloud drift.
- Record execution attempts durably and reconcile uncertain/partial applies before retry. Explicitly handle worker death after Azure changed, state-write failure, lost acknowledgement, and locks. Cancellation does not imply infrastructure rollback. Never blindly force-unlock a possibly active run.

### Terraform versus SDK ownership

Terraform owns persistent declarative infrastructure assigned to it, including managed tags, RBAC, and diagnostic settings. SDK activities own orchestration, artifact transfer, notifications, Batch/VMSS runtime lifecycle for the compute capability, and other explicitly assigned operational actions. Do not introduce a second writer for a Terraform-owned resource/property as a speed shortcut. Any SDK-owned persistent configuration needs its own reconciliation and deletion contract.

### Secrets and privilege escalation

- Prefer infrastructure/application designs that avoid generated credentials; otherwise use application Key Vault references. Never return secret values through the API.
- Writing to Key Vault does not automatically keep values out of Terraform plans/state. Verify ephemeral/write-only support for each selected provider resource. Terraform documents ephemeral features from 1.10 and write-only managed-resource arguments from 1.11; these version thresholds do not prove provider support or OpenTofu equivalence.
- Protect raw state, plans, plan JSON, work directories, diagnostic output, and backups. Provide sanitized summaries. Keep raw plans and secret values out of Temporal history and Git. Define short raw-plan retention separately from durable audit evidence.
- Worker Contributor permission does not by itself authorize role assignments. Do not compensate with general Owner/User Access Administrator rights. Define constrained RBAC-assignment authority, allowed roles/principals/scopes, and policies preventing self-escalation. Evaluate unknown plan values affecting security; do not assume they are safe.

## 9. Cost, budgets, and approvals: later governance milestone

For the compute POC, retain usage/cost measurement, bounded compute profiles/concurrency/timeouts, and agreed spend limits. Full negotiated-rate estimation, budget ledgers, and manager approval workflows are later milestones. Do not block the API foundation on an unselected pricing product.

When governance is implemented:

- Every relevant provisioning operation produces an estimate result before apply, asynchronously after initial acceptance if needed. Distinguish complete, partial, unavailable, and known-zero estimates; unknown must not become zero. Show basis, currency, usage assumptions, exclusions, coverage, and rate/estimator versions.
- For persistent stacks, show resulting monthly run rate, incremental run rate, and prorated current-period impact. For temporary compute, model per-execution duration/usage and monthly forecasts separately; do not charge a disposable VM as permanently running by assumption.
- Pricing should support AWS/GCP later without claiming identical cloud billing models. Evaluate Infracost as an estimator, Azure EA/MCA price sheets as negotiated-rate sources, and supported custom price books as a rate-application mechanism. Self-hosting is a separate deployment choice. These are not three mutually exclusive alternatives.
- Test rate mapping against representative modules: SKU/meter, region, units, currency, effective period, tiers, usage assumptions, reservations/savings commitments, and unsupported charges. Confirm billing-scope permissions independently of subscription access. Do not assume a blanket discount reproduces EA pricing.
- Evaluate supported Infracost custom price books before building a bespoke overlay. Verify licensing, supported resources/plan versions, update mechanisms, and data egress. Its documented API-token/key requirements must be reconciled with our credential policy; putting a key in Key Vault does not make that policy conflict disappear. Confirm self-hosted licensing, authentication, and feature parity with the vendor rather than assuming the old public pricing service is sufficient.
- Distinguish local plan parsing, pricing lookup parameters, telemetry, and cloud-dashboard exports. Do not claim complete plans are uploaded merely because the pricing API is hosted, or claim no data egress without inspecting configuration.
- Keep per-request thresholds and BU/team caps. Use a transactional ledger/reservations so concurrent requests cannot both consume the same budget headroom.
- Avoid double counting: combine reported actuals, estimates for unreported elapsed usage, remaining-period forecasts, and incremental pending reservations without overlap. Reconcile using billing watermarks; account for delayed/corrected actuals, month rollover, updates replacing commitments, cancellations, and partial failures retaining resources.
- A budget admission gate is not a guarantee that usage can never exceed a monetary cap. Define overridable versus nonoverridable rules and governed emergency/cost-reducing operations.

Approval routing:

- Request-level cost exceptions normally route to the requester's manager. BU-cap exceptions require the BU budget owner or documented delegation; manager status alone does not confer that authority.
- Resolve human managers through an approved directory path. The current Graph v1.0 List manager documentation reports application permissions unsupported; verify the actual supported app/delegated query and tenant behavior. Do not assume a background managed identity can call the endpoint. Consider an approved directory synchronization feed if needed.
- CI identities have registered human/application cost sponsors and escalation paths. Handle missing/stale managers, delegation, absence, self-approval, timeout, rejection, and revocation explicitly.
- Promotion approvals are separate: default production promotion to an independent app owner, with platform/change-board gates for defined higher-risk changes and per-BU configuration.
- Teams adaptive cards with email fallback are notification channels. Actions authenticate with Entra against the API and verify current approval authority. A card token or blind webhook cannot approve a job.
- Persist each decision and its exact plan/estimate/target binding before reliably delivering the Temporal signal. Signals wake workflows; the validated recorded approval is the authority. Deduplicate and reject stale or unauthorized actions.

## 10. Credential policy and enterprise dependencies

API access remains Entra-only. Do not add API keys, client secrets, or PATs to simplify runtime integrations. Use managed/federated identities, short-lived delegated credentials where approved, and the established Temporal mTLS certificates. Unavoidable approved secrets/certificates belong in Key Vault with rotation and constrained access.

The POC's phrase “no long-lived shared credentials” permits short-lived tokens and approved certificates; it is not blanket permission to introduce a SaaS API key. Record any incompatible third-party dependency and a compliant fallback. A policy exception requires an explicit organizational decision and must not be silently embedded in the design.

For the build/publish pipeline, separately document how private cross-repository module reads or GitHub Check publishing authenticate. GitHub-to-Azure federation does not itself provide GitHub repository permissions. Keep such permissions scoped and short-lived where supported, and do not move them into Terraform or untrusted workload workers.

Before live compute tests, confirm the approved subscription/resource group, region/quota/capacity, Batch permissions, network/DNS/egress path, image ownership, data classification, artifact/Key Vault pattern, and identity assignments. Reuse the source prerequisites and unresolved questions; record owners and evidence rather than treating “verify” as a completed task.

## 11. Milestones and acceptance conditions

Use the following milestone names to avoid confusing the source's internal POC phases with product releases.

| Milestone | Deliverable and completion condition |
| --- | --- |
| **M0 — Design and decisions** | The package in section 12, requirements mapping, ranked open questions, verification experiments, and ADRs. Review by both engineers before scaffolding. |
| **M1 — API foundation** | Go API/worker structure, OpenAPI, identity/scoping, governed template/profile schema, PostgreSQL persistence/outbox, Temporal integration, fake provider, normalized lifecycle/cleanup/errors, status/events, audit/OTLP, CI/dev environment. Demonstrate the vertical flow and crash-safe dispatch with the fake. No claim of Azure workload isolation yet. |
| **M2 — Compute viability** | Minimal Terraform foundation and approved worker image; test exact Docker/isolation/network/identity/cleanup/latency requirements through Batch. Record the Batch-versus-VMSS gate before full adapter integration. |
| **M3 — First supported capability** | Integrate the selected provider with the API/Temporal lifecycle, result/artifact handling, cleanup/reconciliation, image controls, and end-to-end telemetry. Pass shared fake/Azure conformance and the POC's security/failure/acceptance scenarios; deliver demo and measured findings. |
| **M4 — Persistent infrastructure capability** | Introduce API-owned stacks/deployments and a small approved Terraform module example with immutable bundles, state isolation, reproducible plans, safe apply/recovery, and preview. Implement needed negotiated pricing/policy gates before broader use. |
| **M5 — Governance and catalog expansion** | Transactional budget accounting, manager/budget-owner approvals, Teams/email integration, module onboarding, and delegated administration. Implement these before enabling use that requires their controls. |
| **M6 — Promotion, drift, showback, and UX** | Destination-specific promotion with bound approvals, drift and attribution, broader CLI ergonomics, then portal/Backstage as justified. AWS/GCP live implementations and production hardening have separate acceptance plans. |

M4–M6 describe staged product direction, not a mandate to expose ungated infrastructure between milestones. Use controlled test scopes until required governance is present. For human POC use, provide a minimal documented CLI/curl flow early; defer a polished CLI and portal.

Before accepting M3, exercise all twelve source failure scenarios: duplicate submit, invalid/policy denial, ambiguous submit timeout, capacity/allocation failure, image-pull failure, nonzero exit, workload timeout, cancellation during provisioning, cancellation while running, workload worker failure without callback, activity-worker restart, and cleanup failure/orphan detection. Add foundation-specific tests for database/workflow dispatch gaps and idempotency payload conflicts.

Also demonstrate unauthorized cross-execution access/cancellation denial, blocked access to another execution's artifacts and platform credentials, admitted images and identity pulls, trace correlation, and cleanup success or an explicit exception. Agree numeric concurrency/startup/cleanup/cost targets with us before judging performance. Record p50/p95, not only averages. Fake tests do not prove cloud security, billing accuracy, or Batch viability.

Keep the source's exclusions for the compute POC: no live AWS/GCP, cross-cloud failover/arbitrage, general workflow authoring, complete software factory, interactive shell/VM service, public workload ingress, long-running web hosting, persistent volumes, GPUs, full Fly.io compatibility, ServiceNow workflow implementation, large catalogs, broad onboarding, or full production operations/telemetry engineering.

## 12. First response: produce the design package

Start by reading workspace instructions and both documents, identifying any remaining conflicts, and making the work concrete. Produce Markdown artifacts with these numbered sections, using Mermaid where useful and tables for actual comparisons:

1. **Executive summary and context diagram:** API-first platform intent, compute-first delivery, and current versus later scope.
2. **Architecture and trust boundaries:** components, every identity and its access, authoritative data sources, and sequence diagrams for compute success, cancellation/failure/cleanup, and optional CI submission. Sketch later over-budget approval and stack promotion separately as future flows.
3. **Domain and specifications:** shared ownership/authorization model; execution/template/profile/artifact/operation concepts; lifecycle and cleanup transition tables; worked PR-validation request. Show how later stacks, immutable revisions, overlays, and deployment targets fit without forcing a common resource lifecycle.
4. **OpenAPI 3.1 sketch:** active M1/M3 endpoints for identity context, templates, execution, status/cancellation/events/logs/results; consistent security, errors, idempotency, concurrency, pagination, and examples. Separate future stack/plan/catalog/approval/budget/audit administration routes clearly; do not implement placeholder endpoints for all future features.
5. **Temporal and recovery design:** workflows/activities/queues, submit and signal consistency, external operation reconciliation, timeouts/retries/cancellation, workflow versioning, shutdown, and concrete failure timelines.
6. **Compute provider and image design:** fake/Azure contract and normalized errors, Batch ownership, VMSS fallback, viability experiment, disposable isolation, two image supply chains, networking, artifact/secret delivery, and cleanup evidence.
7. **Terraform design:** immediate platform-foundation ownership and separately labeled future stack executor, immutable bundles, mirrors, state, plan/approval binding, secrets, SDK ownership rule, and partial-apply recovery.
8. **Cost and approval design:** POC measurements/limits; later negotiated-rate evaluation, nonoverlapping ledger/reservations, pricing failures, role-based exception authority, Graph verification, notification/authenticated decision flow, and promotion gates.
9. **Security, compliance, observability, and data:** threat model, SOC 2 control mapping for access/change/audit with evidence owners, telemetry/audit schemas, retention tiers, classification/residency, database and artifact protection/recovery, and POC versus production scope.
10. **BU/subscription registry and ADRs:** target-registration model and production separation recommendation; short ADRs with context, decision/status, consequences, alternatives, and verification dependencies for consequential decisions in this prompt. Record outstanding organizational choices rather than inventing approval.
11. **Monorepo and delivery plan:** idiomatic Go layout, CI/lint/test/build/image flow, Dev Container, task runner, CODEOWNERS, requirements-to-milestone mapping, acceptance checks, and a practical split between two engineers around shared contracts. Keep M1 focused on a usable vertical slice.
12. **Ranked open questions and verification register:** distinguish blocking deployment/security decisions from optional preferences; give a proposed default, owner, exact test/evidence, and fallback. Include all still-relevant questions from the POC source and the product assumptions highlighted below.

Save the package under `docs/design` and ADRs under `docs/adr`, with a short index linking the results. Provide concrete documents for review. Stop at the design-review gate until our signoff is recorded; creating this prompt is not that signoff. After signoff, implement and verify the current approved milestone, report changed behavior and evidence, and keep later work in the roadmap rather than expanding the milestone implicitly.

## 13. Verification references from the architecture review

These official references explain why particular assumptions need care. Recheck them and record deployed versions when creating the design; they do not replace tenant-specific tests.

| Topic | Starting reference / question to verify |
| --- | --- |
| Temporal external effects | [Idempotency and durable execution](https://temporal.io/blog/idempotency-and-durable-execution): acknowledgement loss and retry safety. |
| Temporal authorization | [Self-hosted security](https://docs.temporal.io/self-hosted-guide/security): actual authorizer/claims mapping and caller/worker privileges. |
| ACA lifecycle | [Application lifecycle](https://learn.microsoft.com/en-us/azure/container-apps/application-lifecycle-management): termination, deployed grace period, and draining tests. |
| Temporal scaler | [KEDA Temporal scaler](https://keda.sh/docs/2.20/scalers/temporal/): version availability, authentication, metrics, and ACA support. |
| State authentication | [AzureRM backend](https://developer.hashicorp.com/terraform/language/backend/azurerm): Entra data-plane identity, permissions, and locking. |
| Provider mirror authentication | [Network mirror protocol](https://developer.hashicorp.com/terraform/internals/provider-network-mirror-protocol): authentication differs from backend access and archive download credentials. |
| Module reproducibility | [Dependency lock file](https://developer.hashicorp.com/terraform/language/files/dependency-lock): provider locks do not capture the transitive module tree. |
| Planning and sensitive artifacts | [Plan command](https://developer.hashicorp.com/terraform/cli/commands/plan) and [sensitive-data handling](https://developer.hashicorp.com/terraform/language/manage-sensitive-data): refresh, targeting, saved plans, and resource-specific ephemeral/write-only support. |
| Infracost rates/authentication | [Custom price books](https://www.infracost.io/docs/infracost_cloud/custom_price_books/), [management API](https://www.infracost.io/docs/infracost_cloud/api/), and [FAQ/data flows](https://www.infracost.io/docs/faq/). |
| Delayed billing and attribution | [Cost Management data](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/understand-cost-mgt-data): billing delay/corrections and incomplete tags. |
| Manager lookup | [Graph v1.0 List manager](https://learn.microsoft.com/en-us/graph/api/user-list-manager?view=graph-rest-1.0): supported permission/query path in our tenant. |
| RBAC delegation | [Azure privileged roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged): Contributor versus role-assignment authority. |

For Azure Batch, VMSS, worker-image compatibility, registry identity, Docker access, Key Vault, PostgreSQL identity, and networking, add current official references matched to the selected deployment model during M0. The source POC deliberately requires experiments for those choices; do not substitute assumptions for their outcomes.
