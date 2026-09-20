# M0 design review response — revision 0.3.0

**Date:** 2026-09-07. **Scope:** authorized design corrections, not M1 implementation or signoff. [Review](m0-design-review-2026-09-07.md), [revised decision page](../design/README.md), [validation evidence](../design/validation.md).

`Fixed` means the document/contract gap was corrected, not that runtime behavior has passed. `Decision needed` means the recommendation, alternatives and gate are now explicit but a human/environment choice remains. `Disagree` rejects the stated premise or requested expansion; any useful clarification is still recorded. All ADRs remain Proposed, both source documents are preserved, and all environment tests remain not run.

## Finding dispositions

| Finding | Disposition | Revision / remaining condition |
| --- | --- | --- |
| RF-01 | Fixed | [Domain roles](../design/03-domain.md#ownership-and-permission-model) enumerate nine permissions, classification-bounded developer/owner data/catalog access and read-without-data denial. Routes use the same names |
| RF-02 | Fixed | [Registry](../design/10-registry-decisions.md#grant-evaluation-and-target-selection) defines typed scopes, referential integrity, inheritance/intersection and deny rules instead of app-environment-only grants |
| RF-03 | Decision needed | D04/ADR-0004 recommend supervisor-run Compose with no untrusted Docker API. If the real workload requires direct calls, approve a scoped broker/nested-runtime comparison and M2 estimate first; a bespoke broker is no longer assumed small or mandatory |
| RF-04 | Fixed | [Delivery](../design/11-delivery.md#two-engineer-split-and-m1-stop-condition) schedules M2.0 ACA roles, identities, mTLS and hosting evidence; Terraform owns revisions/images |
| RF-05 | Fixed | Explicit local/connected/live test matrix. M1 retains ambiguous submit, timeout, cancellation, restart and late-effect safety; F17 cross-store restore is explicitly M3 |
| RF-06 | Fixed | [Recovery](../design/05-temporal-recovery.md) names Fail/RejectDuplicate, open-run recovery signal and closed/absent ticket-only recovery. Continue-As-New in the original chain remains valid; no blanket ban on all second runs |
| RF-07 | Fixed | Bounded cancellation attempts end with platform-origin failed/cancellation_failed and current infrastructure confidence, never unproven cancelled or an invented workload exit. Cleanup/tickets remain independent |
| RF-08 | Fixed | Control reconciler owns never-started expiry/cancellation, atomic outbox closure/fence and no-allocation proof; pending dispatch flag alone is insufficient. Backlog count/age limits fail closed |
| RF-09 | Decision needed | Exact management/data-plane and finite identity assign scopes are specified. V06 must approve the finite one-use inventory/retirement and assignment proof; reusable UAMIs are not adopted without stale-token/minting/reassignment evidence |
| RF-10 | Fixed | Exactly one eligible primary target per context/profile; draining/migration candidates cannot receive new admissions. Zero/multiple-primary outcomes and pinned in-flight target are defined |
| RF-11 | Fixed | [README](../design/README.md#m0-decisions-requested) gives recommendations, alternatives, owners and blocking gates; ADR register records pending human disposition; reading path includes the Docker boundary |
| RF-12 | Fixed | Five required caller fields, immutable server defaults, fixed-value echo rules, whole-map/array semantics and complete resolved spec. Minimal and full input schema examples checked; real policy expansion remains M1 work |
| RF-13 | Fixed | Quoted the flow-mapping description. Reproduced the original Redocly structure error; added an exact-schema regression and pinned lint command. Original YAML parsed successfully but produced the wrong structure |
| RF-14 | Fixed | [Status glossary](../design/03-domain.md#status-glossary-and-client-completion) defines every facet, client completion/exception predicates and cancellation semantics; renamed caller reason to superseded_by_new_request |
| RF-15 | Fixed | Named extensible event vocabulary, required status-facet snapshot, cancellation/error fields, sequence/revision rules and one cursor/next contract |
| RF-16 | Fixed | Logs always return a resumable cursor, including empty/end-of-current-page; timestamp/source fields added. Truncation means omitted content, not ordinary pagination |
| RF-17 | Fixed | Acceptance-relative deadline remains an explicit D03 proposal; observed started_at and timed_out_from distinguish queue/start delays from observed runtime without fabricated timestamps |
| RF-18 | Fixed | [Input intake](../design/03-domain.md#caller-inputs-and-template-expansion) specifies importer permission/manifest/verification, fixture and live milestones, publication and retention. No public upload endpoint or arbitrary repository fetcher added |
| RF-19 | Fixed | Actual pool/job/core/IP quota inventory and deleting/unknown-pool accounting; cold-first baseline; any optional warm pool bound to a reserved execution. Shared fresh-node pools remain a separate isolation decision |
| RF-20 | Decision needed | User-subscription service privileges, separate Batch vault and quota consequences are explicit; compare Batch-service mode. V05/platform owner chooses the actual allocation mode, not resource visibility alone |
| RF-21 | Fixed | M2.1 includes the one-way prelaunch claim before any untrusted process. Node-loss/requeue detection supplements prevention; polling and terminating after a requeue cannot establish at-most-one launch |
| RF-22 | Fixed | [Architecture](../design/02-architecture.md#component-and-data-ownership) separates public API, private runtime role and optional per-app secret role by deployment/identity/DB rights, not merely listeners |
| RF-23 | Decision needed | Root-owned UID/network/cgroup enforcement candidate, protected rootless path, assignment proof and agent-credential tests are named. Actual bypass proof remains V06; retired Batch task-authentication tokens are not introduced |
| RF-24 | Decision needed | Enterprise verifier preferred, Cosign candidate; publisher/supervisor enforcement, signer/root policy, rotation/revocation and fail-closed tests specified. V09 must provide actual approved versions/roots |
| RF-25 | Fixed | [Experiment spend register](../design/08-cost-approvals.md#m1m3-scope) reserves conservative exposure, counts unknown cleanup, blocks stale/unset monitoring and defines operator stop/re-authorization. Budget alerts are supplementary, not a hard spend guarantee |
| RF-26 | Decision needed | Per-app/environment secret delivery identity has explicit vault scope, separate from public/transfer roles. V06/V10 owner acceptance remains required; secret-free demo stays enforced until S11 passes |
| RF-27 | Fixed | Measured history/Continue-As-New guard, Describe/Memo plus trusted DB conflict checks, closed-signal terminal rules and absolute-deadline ordering added |
| RF-28 | Fixed | Explicit transient DB/projection/result exhaustion branches with stable IDs, timers and recovery; permanent authorization/schema/invariant errors do not retry forever. Independent cleanup covers unavailable persistence |
| RF-29 | Fixed | Rechecked latest SDK release as candidate 1.48.0 with dated permalink and Worker Versioning floors. Reject the inference that an older inspected candidate proves reliance on prior knowledge; no untested pin is labeled compatible |
| RF-30 | Fixed | A01 fake/local/connected and A02 live positive acceptance IDs now identify the full success path separately from failures |
| RF-31 | Fixed | Representative contract explicitly requires observed exit, logs and named sbom from sbom.spdx.json; M2.1 pins exact producer/tool/workload fixture |
| RF-32 | Fixed | Cache accepted 202 only; all pre-admission denials/transient errors reevaluate. Same key/exact canonical caller payload discovers ambiguous committed acceptance; conflicts cannot overwrite it. Broad 4xx caching not adopted |
| RF-33 | Fixed | Artifact.name is a required unique logical output key; CI selects sbom without guessing media type or array position |
| RF-34 | Decision needed | PKCE helper/client/audience/token handling and curl onboarding are M1.1 work, not last-mile M1.4. V02 supplies actual tenant-approved values; no fictional working token command or unapproved device-code flow |
| RF-35 | Fixed | Consolidated evidence rule and decision-first reading path; reduced repeated status text in changed sections. Security-critical fail-closed conditions remain adjacent to their controls |
| RF-36 | Fixed | M1/M2 work-package person-day ranges, assumptions, access-wait exclusions, critical path and re-estimation gates are recorded; custom broker work excluded pending explicit scope |
| RF-37 | Fixed | M1 authorization to begin independent work is distinct from contract freeze and milestone acceptance. One completion definition requires both local core and connected subset; no silent waiver of Entra/Temporal evidence |
| RF-38 | Disagree | Working agreement/agents/tests were explicitly requested in the conversation. Retained it; accepted the useful split between required V20 repository/tests and optional V21 agent preferences. No agent configuration activated |
| RF-39 | Fixed | [Repository-relative checker](../design/tools/validate_design.py), pinned Python requirements and Redocly profile/command replace reliance on /tmp; portable path and regression checks recorded |
| RF-40 | Fixed | M3 explicitly requires all twelve source scenarios plus foundation cases; missing safe injection/evidence leaves acceptance pending rather than “inapplicable” |
| RF-41 | Fixed | Correlation headers on every declared response, trace/flow inputs on every operation, explicit negotiation/throttle/unavailable errors and alias response parity. Existing default responses were legal; declarations now aid clients |
| RF-42 | Fixed | OpenAPI 3.1 bearer requirement roles match registry permissions; dot-separated naming and non-OAuth registry authority are explicit V01 review choices. Problem examples include policy_denied; 400 invalid versus 403 policy denial remains clear |
| RF-43 | Fixed | Catalog description/version mapping/revocation semantics added. List-only small catalog and explicit immutable versions retained; extra single-template/latest routes are optional scope, not missing requirements |
| RF-44 | Fixed | Cancellation body optional with {} normalization; preserve reason and key. CI guidance distinguishes terminal/no-new-cancel from workload success and verified cleanup |
| RF-45 | Disagree | No execution-list endpoint required for the first slice. Documented exact-payload idempotent replay as lost-ID recovery; extra listing remains a later product choice |
| RF-46 | Fixed | Every JSON success operation now has a validated example, including both submission aliases through a shared response. Binary download is described by headers and the named artifact manifest, not fake embedded SBOM bytes |
| RF-47 | Fixed | Added Content-Length and Accept-Ranges:none; retained checksum in authenticated manifest rather than redundant digest header; cancellation echoes supplied reason |
| RF-48 | Fixed | Retained completed execution-derived Batch job is the concrete provider marker alongside DB submission_closed; pool/task ID scope and safe marker retirement rules stated |
| RF-49 | Fixed | Threat model adds privileged template publication, Batch service/agent trust and forged telemetry/logs; trusted correlation stamping and separate authenticated ingestion with S07 attacks |
| RF-50 | Fixed | M1 builds/tests locally without Azure publishing identity; M2.0 adds reviewed federated image publication and Terraform-owned deployment |
| RF-51 | Fixed | V gates/owners/evidence/fallbacks aligned with delivery; ACA V14/V15 assigned M2.0; optional agents/scaler split to V21/V22; ADR verification references synchronized |
| RF-52 | Fixed | Published inputs, per-VM workspace and events have separate retention; shared input leases prevent premature deletion and staged-upload expiry is not publication expiry |
| RF-53 | Fixed | Lint-profile ID unified; Azure Activity Log restored as management-plane evidence; S11 covers conditional secrets; executor bounds and Entra-only M2 foundation backend/human plan approval stated |
| RF-54 | Fixed | Later design clarifies charged BU authority, target recreate/move/import, re-plan trigger/comparison and reservation-to-forecast conversion. ACA revision/image single writer is settled as a proposal now, not left until M4 |
| RF-55 | Fixed | Added VM trust-boundary diagram; existing success/cancel sequences, full transition table and failure-timeline table remain the compact state/crash references. No extra duplicate diagrams or rendering claim |
| RF-56 | Fixed | A/F/S/V/D legend on initial reading path; process/component glossary distinguishes supervisor, runtime transfer/claim, optional Docker broker, dispatcher and sweeper |

## Remaining human decisions

Use D01–D12 in the index for scope/architecture signoff. Tenant identities, enterprise standards, exact workload features, image trust roots, allocation mode, numerical budgets and security/data evidence cannot be invented by editing documents. This response does not accept an ADR or authorize M1/M2. The changes preserve the durable outbox/attempt/fencing design, provider-neutral public contract, no-workload-retry policy and independent cleanup evidence.
