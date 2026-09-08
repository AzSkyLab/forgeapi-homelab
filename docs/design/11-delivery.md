# 11. Monorepo and delivery plan

**Status:** current local layout plus target delivery plan; local core implemented, full M1 acceptance pending. [Index](README.md). The completed one-vault/identity spike is recorded separately in [ADR-0013](../adr/0013-local-terraform-lab-exception.md), not completion of M2/M4.

The [working agreement](working-agreement.md) defines how the requesting engineer, manager and Codex share task ownership, use bounded specialist agents, preserve session state, and review changes. It also names the proposed test frameworks and developer commands. Read it alongside the M1 work packages below; it supplements the runtime architecture with the engineering workflow.

## Current layout and dependencies

```text
cmd/forgeapi/                    API, synthetic worker, migration and health roles
cmd/demo/                        browser PKCE and local/core or bounded vault walkthrough
cmd/keyvault-worker/             native lab Terraform worker and read-only identity check
internal/execution/              portable input/spec/lifecycle and bounded template defaults
internal/auth/                   Entra verification, current grants and dispatch policy
internal/httpapi/                compute and separate lab deployment HTTP handlers/contracts
internal/local/                  explicit local/Entra configuration boundaries
internal/orchestration/          Temporal workflow, dispatch, recovery and simulated provider
internal/store/                  pgx repositories, audit, durable effects and integration tests
internal/store/migrations/       numbered, checksum-verified forward SQL migrations
internal/deployment/             bounded deployment/target/plan model; no Azure SDK
internal/keyvaultrunner/          native certificate ARM/Terraform activities and recovery
internal/telemetry/              local OTLP and sanitized correlation
patterns/key-vault/              embedded digest-pinned AzAPI create-only pattern
scripts/                        local setup/build/verification helpers
config/                         checked-in collector config; ignored tenant/runtime config
compose.yaml, compose.test.yaml  normal local stack and credentials-free test fixtures
Dockerfile, .github/workflows/   pinned build/test environment and local-only CI definition
docs/design/openapi*.yaml        portable compute and lab deployment contracts
docs/design/, docs/adr/          target design, implementation notes and decision records
AGENTS.md, CONTRIBUTING.md       active machine/human working instructions
Makefile                        actual local/CI commands
```

Keep interfaces with their consuming packages around actual provider, storage, policy, identity/authorization data, artifact/secret and audit boundaries. A notification port is added when an optional integration or later milestone uses it. No generic bus/plugin/approval/pricing/Terraform executor framework in M1. Avoid dependency direction from domain/workflows into Azure packages. Activity implementations load internal target bindings and invoke adapters.

Current Go/chi, pgx, Temporal and OpenTelemetry versions are pinned in `go.mod`; Dockerfile/Compose pin build and dependency images. [Version register](references.md) separates these from future choices. Tests live beside packages, including tagged process/DB integration tests. No Dev Container, Batch adapter, hosted runtime-transfer role, foundation Terraform or general stack executor is present. Enterprise Temporal connectivity still requires implementation/configuration and verified access; local Temporal does not replace the enterprise service decision.

## Two-engineer split and M1 stop condition

| Work package | Engineer A — API/data | Engineer B — orchestration/provider | Shared review / proof |
| --- | --- | --- | --- |
| M0 close | Own API/ownership/data decisions | Own lifecycle/provider/identity feasibility | Both approve contract/ADRs and M1 scope |
| M1.1 contract and dev shell | chi route/schema design, error/Entra boundaries, input-import manifest and token/curl onboarding | Workflow/activity contracts, durable fake contract fixtures | Freeze only after V01; pin tooling; create approved AGENTS.md/contributing guide and commands; independent drafts do not imply freeze |
| M1.2 durable acceptance | SQL migrations, idempotency, admission reservation, outbox | Stable workflow start and dispatch acknowledgement recovery | Kill dispatcher at commit/start boundaries |
| M1.3 full fake lifecycle | Authorized status/cancel/events/log/result/artifact read | Short activities, durable timers, cancellation, timeout and cleanup | Shared conformance and failure tests |
| M1.4 evidence/handoff | Object-denial tests, redaction, curl guide, health/readiness | Replay/restart/late-effect tests, cleanup tickets, OTLP spans | Acceptance record, reviewed CI/dev workflow |
| M2.0 preflight and hosting | First establish V11 experiment register/monitor and foundation cost reservation; then approved Terraform backend/foundations and ACA API/worker/runtime boundaries | API/worker image publishing; development Temporal mTLS, revision/shutdown checks | V11 before paid apply; platform/ACA plan approval; V05/V14/V15 and hosted V03 evidence |
| M2.1 experiment safety slice | Trusted source importer, minimal private runtime assignment/transfer/claim role integrated with the M2.0 spend register | Approved supervisor/worker image, exact Compose and SBOM fixture, one-use identity inventory and prelaunch claim | V06/V09/V10/V11 prerequisites; no untrusted execution before required controls pass |
| M2.2 Batch experiment | Capture authorized source/artifact/telemetry, latency and spend evidence | Exact workload, requeue/termination/isolation/cleanup probes under failure injection | V07/V08; both engineers + security record Batch accept/reject/pending before M3 |
| M3 later | Live artifact/result/data authorization and demo | Selected adapter, supervisor, cleanup/maximum lifetime | Shared fake/Azure conformance and complete failure/security evidence |

M1 is done only when an authorized caller can submit the fixture PR-validation template, receive durable 202, observe Temporal-driven fake completion/results/cleanup, cancel a second run, fail cross-object reads, and inspect correlated sanitized audit/OTLP evidence; restarts do not lose admission or duplicate external work. A memory-only fake or Temporal unit test alone does not establish this. Entra and enterprise development Temporal validation are required before claiming the connected M1 flow; local tests may proceed independently but leave those checks explicitly unverified.

### Test tiers and positive acceptance

| Tier / gate | Exact required evidence | Boundary |
| --- | --- | --- |
| M1 local core | A01; F01–F16 and F18; local S01/S02/S03 configuration/S06 admission/S07/S09, API/schema/default/idempotency/cursor fixtures and selected workflow replay | Real PostgreSQL/Temporal fixture and durable fake; includes ambiguous submit, cancellation, timeout, late effects and artifact publication gap—not only happy-path mocks |
| M1 connected completion | A01 repeated with real development Entra token and enterprise dev Temporal; F01/F02/F07/F09/F11/F13; S01/S02/S07; V02/V03 development matrix | Laptop-hosted API/worker/fake; no workload VM or ACA deployment claim. M1 is not complete while this subset is unverified |
| M2 hosting | V05/V14/V15 and ACA-to-dev-Temporal connectivity/rotation from V03; hosted S03 | Actual approved ACA deployment, not laptop SIGTERM evidence |
| M2 viability | V06–V11; S03–S06; provider probes F03/F08–F12/F15 and prelaunch-claim replay prevention | Minimal experiment harness/runtime, before full Azure adapter integration |
| M3 acceptance | A02; **all twelve source scenarios F01–F12**, plus F13–F18; S01–S10 and S11 if secrets enabled; V12/V13 | Selected live provider; source scenario omissions are not “inapplicable.” If safe fault injection cannot be arranged, acceptance stays pending with the missing case named |

F17 cross-store disaster-restore proof is M3, not an implicit M1 gate. M1 still implements durable records, fencing/tickets and restart/late-effect tests needed for that recovery design. Connected tests require approval of test identities/namespaces, not production credentials. The only M1 completion definition is local core **plus** connected completion; “local core ready” is an intermediate status.

| ID | Positive acceptance scenario | Evidence |
| --- | --- | --- |
| A01 | Authorized caller discovers template, selects fixture source already published by an authorized importer, submits minimal or equivalent fully specified request with a fresh key, receives durable 202, observes fake workflow completion, gets logs/results/named `sbom`, and sees verified cleanup; then cancels a second execution | Commands, input/spec digests, execution/workflow IDs, single launch count, authorization denials, named artifact digest, status facets and correlated audit/OTLP; M1 local and connected |
| A02 | Same caller flow through selected Azure adapter with exact Docker-dependent tests, observed exit, published SBOM, independent node/access removal and all 14 source demo steps | M3 source/entrypoint/helper/worker versions, F/S evidence, measurements/spend basis and remaining operational exceptions; no fake evidence substituted |

### Planning estimate and critical path

Rough engineering effort, not a delivery commitment: M1.1 3–5 person-days, M1.2 5–8, M1.3 6–10, M1.4 4–6 (M1 total 18–29); M2.0 3–5, M2.1 6–10, M2.2 4–7 (M2 total 13–22). These assume two experienced engineers, reusable approved foundations/images and supervisor-run Compose without a custom Docker broker; include implementation/tests/review, exclude enterprise access queues and major failed-viability redesign. Re-estimate after M1.1 and D04/V05/V06; selecting a broker requires its own security scope/estimate rather than hiding it here. Calendar duration depends on actual allocated engineer time.

Critical path: M0 contract/recovery choices → M1.1 → durable acceptance → full fake lifecycle → connected M1 acceptance → M2 hosting/identity/image prerequisites → minimal safety slice → Batch experiment/decision. Identity/Temporal access requests can proceed alongside approved local M1 work; M2 paid provisioning cannot.

## CI, development, and release

Use the actual commands in the [working agreement](working-agreement.md): native Go testing/httptest, Temporal testsuite/replayer, Compose PostgreSQL/Temporal fixtures and JSON Schema checks of actual HTTP responses. `make check` covers format/vet/unit/race/build; `make test-core` adds DB/process/replay and `make vuln-docker` scans dependencies. No golangci-lint or Testcontainers integration is installed. Keep Python/Redocly documentation checks separate. Do not require an Azure subscription for routine CI; document-only changes receive proportional checks, with HTTP contract tests when schemas change.

The checked-in GitHub Actions definition uses minimal permissions and commit-pinned actions for local tests, build and Go vulnerability checks; it neither publishes nor deploys. Remote CI execution, image scanning/SBOM/provenance and Dev Container setup are not claimed. Future M2.0 release federation and Terraform-owned revisions require separate approval. VM-image/runtime-OCI approval and private-module packaging remain separate work; no PAT/client secret is implied. Dockerfile pins current developer tooling; `.env.example` contains local setup fields, not tenant credentials.

Conventional Commits and CODEOWNERS remain proposed; no team handles are invented. Git is usable, and existing/uncommitted changes must be preserved. Do not commit or push without an explicit request. Human review and remote CI evidence remain distinct from local assistant-run checks.

## Failure acceptance catalog

Rows below are **acceptance requirements, not blanket pass/fail results**. Selected local cases have evidence in [progress](../progress.md) and the [verification overlay](12-verification.md#current-evidence-overlay); the complete F01–F16/F18 local matrix remains open. The tier matrix above is authoritative; live compute cases are unrun. Evidence must identify the fault window, fixture/version, execution IDs, state/events/attempt counts and result/cleanup proof. Keep partial coverage visible rather than marking an entire gate passed from one test.

| ID | Source §16 scenario | Expected convergence / proof |
| --- | --- | --- |
| F01 | 1. Duplicate submit | Same key/payload returns same execution; one admission reservation, workflow and workload |
| F02 | 2. Invalid/policy denial | 400/403 before acceptance, sanitized denial audit, no provider allocation |
| F03 | 3. Ambiguous submit timeout | Provider accepts then drops response; recovery finds same external identity, exactly one launched workload |
| F04 | 4. Capacity/allocation failure | Failed with capacity/provisioning code when confirmed; cleanup proven or explicit exception |
| F05 | 5. Image pull failure | Failed/image_unavailable, no automatic workload rerun; node cleaned |
| F06 | 6. Nonzero exit | Failed/workload_failed and observed exit code; bounded available artifacts then cleanup |
| F07 | 7. Workload timeout | timed_out with termination attempts; uncertain infrastructure remains visible until reconciled |
| F08 | 8. Cancel during provisioning | Persisted intent; late allocations/submissions reconciled and removed; cancellation only confirmed with stop/no-dispatch evidence |
| F09 | 9. Cancel while running | Stop requested/observed, terminal-race semantics stable, workspace/access removed |
| F10 | 10. Workload worker failure without callback | Polling discovers loss; no scheduler replay of workload; portable failure/timeout and cleanup |
| F11 | 11. Activity worker restart | Kill at before/after effect and acknowledgement; same provider execution, monotonic projection |
| F12 | 12. Cleanup failure/orphan | Successful workload may retain failed cleanup; ticket/sweep resolves or documents outstanding exception |
| F13 | Additional DB/workflow gap | Commit acceptance, kill dispatcher; Temporal unavailable and start-ack loss both recover without lost/duplicate execution |
| F14 | Additional payload conflict | Concurrent identical retries converge; same key/different payload gets 409; cross-principal scope never leaks response |
| F15 | Additional late effect/deletion race | Delay submit past cancellation and cleanup; retained marker/fencing prevents duplicate or falsely clean result |
| F16 | Additional artifact gap | Upload then kill before registration; immutable digest upsert recovers; partial/stale uploads expire |
| F17 | Additional restoration | Restore unequal DB/object/Temporal points; freeze, inventory, quarantine and reconcile before resume |
| F18 | Additional concurrency/event ordering | Competing admissions honor capacity caps; stale activities cannot regress state; pages/events do not omit authorized committed records |

## Security and quality acceptance catalog

| ID | Required evidence | Milestone |
| --- | --- | --- |
| S01 | Invalid issuer/tenant/audience/signature/expiry/scopes/roles, ARM/ID-token rejection and signing-key rotation | M1 |
| S02 | Cross-user/app/env status, events, logs, results, artifact and cancellation denial; cursor and revoked grant tests | M1, repeat M3 |
| S03 | Public API has no provisioning rights; every identity/role scope reviewed; direct Temporal bypass denied | M1 local configuration/namespace checks; actual hosted rights M2.0/M3 |
| S04 | Host/daemon/agent credential/IMDS/network bypass attempts fail under exact Docker features | M2/M3 |
| S05 | Cross-execution artifact/input/workspace/session access blocked; old VM never reused | M2/M3 |
| S06 | Entrypoint/helper digests and worker image approved; mutable/revoked/unapproved images denied; identity pull demonstrated | M2/M3, fake admission M1 |
| S07 | Start from execution ID and find admission → persistence → workflow → adapter → workload → cleanup evidence; no secret canaries exported | M1 subset, M3 all |
| S08 | Cold/warm sample counts and startup/cleanup p50/p95 against agreed thresholds; usage/cost basis with uncertainty | M2/M3 |
| S09 | API contract/worker has no provider fields/branches; fake/Azure same mandatory conformance; directional other-cloud review | M1/M3 |
| S10 | Database/object restoration, Entra reconnection, retention/deletion including shared-input leases and backup copies | M3 live, F17/V13; M1 restart/replay obligations are separately enumerated |
| S11 | Optional secret delivery: allowed app/version succeeds; wrong app, revoked/expired assignment and post-cleanup access denied; no secret in history/logs | Before enabling any nonempty secret_refs in M2/M3; otherwise capability remains disabled |

## Requirements-to-design-and-milestone mapping

This table tracks every numbered section of the detailed source, including conditional and deferred requirements. Links refer to design artifacts; F/S/V IDs refer to this catalog and section 12. M1 fake evidence does not close a live-security requirement.

| POC source section | Preserved requirement / destination | Milestone / evidence |
| --- | --- | --- |
| 1 Executive summary | API-first isolated portable execution; [context](01-context.md), [architecture](02-architecture.md) | M0; M1/M3 S09 |
| 2 Goal/hypothesis | Same spec, portable workflow, real Docker/security | M1 fake + M3 F/S conformance; V07 |
| 3 Principles | Intent/profiles/identity/recovery/measurement | [domain](03-domain.md), [recovery](05-temporal-recovery.md), S03/S09 |
| 4 Scope including 4.1–4.3 | Azure-only, all execution features, explicit exclusions | M1–M3; exclusions below |
| 5 Demo use case | Source archive/commit, immutable entrypoint, exact Docker, results/SBOM, optional CI | [compute](06-compute-images.md), M2/M3 V07/V16 |
| 6 Conceptual architecture | ACA versus workload VMs, existing Temporal, one controller | [architecture](02-architecture.md), V03/V05 |
| 7 Component responsibilities | API/workflow/adapter/scheduler/image ownership | Sections 2/6; S03/S09 |
| 8 API including 8.1–8.4 | All proposed routes, idempotency, portable profile mapping | [API](04-api.md), [OpenAPI](openapi.yaml); V01/F01/F14 |
| 9 Lifecycle/sources of truth | Full transitions and separate cleanup | [domain](03-domain.md), F04–F12 |
| 10 Temporal | Deterministic short activities, timers, retry ownership, references | [recovery](05-temporal-recovery.md), V03/V04/F03/F11 |
| 11 Provider | Six operations plus explicit discover/reconcile, ten errors | [compute](06-compute-images.md), F01–F12/S09 |
| 12 Azure including 12.1–12.4 | Batch/VMSS ownership, viability and both image chains | Sections 6/7, V05–V09; M2 gate/M3 adapter |
| 13 Security including 13.1–13.6 | All identity/authorization/credentials/VM/Temporal/supply-chain controls | Sections 2/6/9; S01–S06 |
| 14 Network/data including 14.1–14.3 | Ingress choices, required egress/private DNS, classification/residency/limits | Sections 6/9, V05/V06/V10/V14 |
| 15 Observability including 15.1–15.7 | All 11 stages, W3C/OTLP, safe logs/metrics, audit and acceptance | [security/data](09-security-data.md), V12/S07/S08 |
| 16 Failure scenarios | All twelve enumerated scenarios retained above | F01–F12; M1 fake/M3 live |
| 17 Source phases 0–6 | Remapped below without treating them as product releases | M0–M3 |
| 18 Success criteria | Abstraction, scope, workflow/provider, execution/lifecycle, identity/isolation, cleanup/telemetry/performance/portability | F01–F18, S01–S10; M3 acceptance |
| 19 Demo script | All 14 demonstration steps mapped below | M3 demonstration record |
| 20 Decision gates | Batch/VMSS, cold/unused-warm, directional portability | Section 6; V07/V11/S09 |
| 21 Risks | Docker, startup, retries, identity, contract, history, telemetry, cleanup, quota, scope | Sections 5/6/9/12; named owners |
| 22 Prerequisites | Subscription/RG/region, rights, network/DNS/PE, registry/image, vault/store, identities, ACA, Temporal, Entra, SIEM, conditional ingress, repository | V02/V03/V05/V06/V09/V10/V12/V14/V16 |
| 23 Questions 1–15 | Each mapped individually in section 12 | V01–V16 plus supplied-context resolutions |
| 24 Future providers | Directional mappings only, explicit lack of equivalence proof | Section 6; M0/M3 S09 |
| 25 Expected outputs | OpenAPI, fake/selected provider, conformance/workflow/API, Terraform/image, security, performance/cost, OTLP/demo, recommendation/portability/next scope | M0 documents; M1 fake/API; M2 foundations/gate; M3 all remaining artifacts |
| Appendix A | Digest placeholder, worker terminology, image build/reuse/hosting/gate ambiguities resolved or logged | Sections 1/3/6/12 |
| Appendix B | Preserve future provisioning product and strict credentials; separate lifecycle | Sections 7/8/10; M4–M6 |
| Appendix C | Photo provenance remains in source, no photographs needed | Source preserved, no fabricated image evidence |
| Appendix D | API foundation first, compute first capability | M1 → M2 → M3 explicitly |

| Combined prompt section | Design coverage |
| --- | --- |
| 1 Working agreement | Index signoff, M0-only changes and preserved sources |
| 2 Intent | Sections 1–3 and distinct future stack aggregate |
| 3 Environment/technology | Sections 2/7/9/10, references/version register |
| 4 Shared foundation | Sections 2–5, M1 acceptance and F13/F14/F18 |
| 5 Compute | Sections 3/6, all source mapping above |
| 6 Temporal/reliability | Section 5 and ADR-0008, V03/V04/V15 |
| 7 Observability/data | Section 9 and V10/V12/V13 |
| 8 Persistent stacks | Section 7; future domain section 3 |
| 9 Cost/approvals | Section 8; V17–V19 |
| 10 Credential/dependencies | Sections 2/6/8; V02/V06/V16–V18 |
| 11 Milestones/acceptance | This document and review gate |
| 12 Design package | Twelve numbered artifacts, OpenAPI and ADR index |
| 13 References | Official references register, documented versus unverified distinctions |

Source phase mapping: Phase 0 → M0 constraints plus unresolved M2 prerequisite closure; Phases 1–2 → M1 foundation; Phase 3 → M2 Batch gate; Phases 4–6 → M3 integration, security/failure validation and findings. Some source Phase 2 live deployment evidence belongs to later approved cloud setup; local fake tests do not satisfy it.

Demo coverage: steps 1–4 contract/auth/submit/progress/workflow boundary → M1 and repeated M3; steps 5–6 exact Docker + images/identity → V07/S04/S06; step 7 logs/artifact → F16/S02; step 8 correlation → S07; steps 9 and 12 result/removal/no-static-credentials → F12/S03/S05; step 10 running cancellation → F09; step 11 unauthorized access → S02; step 13 fake conformance → S09; step 14 cloud mappings/performance/cost → section 6/S08/S09. Record all demo steps; GitHub caller/Check remains optional.

## Roadmap and exclusions

M4 adds stacks, state/bundles/refreshed plans/safe apply in controlled scopes with necessary pricing/security gates. M5 adds budget ledgers, manager/budget-owner decisions, notifications, module onboarding and delegated admin before use requiring those controls. M6 adds bound destination promotion, drift/attribution/showback, then CLI/portal ergonomics. Production readiness and any AWS/GCP implementation need separate scope/acceptance.

Compute POC exclusions remain: live AWS/GCP, cross-cloud failover/arbitrage, generalized workflow authoring, complete software factory, interactive shell/VM service, public workload ingress, long-lived web hosting, persistent volumes, GPUs, Fly.io compatibility, ServiceNow workflow implementation, large catalogs, broad onboarding, advanced chargeback/forecasting, production collector HA/operations/SLO/retention engineering. Neither missing prerequisites nor an optional demo integration authorizes expanding these boundaries.
