# Infrastructure Platform API — M0 review package

**Revision:** 0.3.0, 2026-09-07. **Status:** proposed; awaiting both engineers' review. **Authorized scope:** design and review corrections only.

Build a shared Go API foundation, prove it through a durable fake-provider execution, then test Azure Batch before implementing the live capability. The principal unresolved risk is combining the required Docker functionality with enforceable credential, image, and host isolation.

Start with the decision table below, then the [executive summary](01-context.md), [Docker/VM boundary](06-compute-images.md#exact-demo-and-docker-authority), and [M1 completion/tests](11-delivery.md#two-engineer-split-and-m1-stop-condition). Review the [working agreement](working-agreement.md) alongside task ownership and tests; you explicitly requested it. Supporting files provide arguments, not separate file-by-file signoffs. Changes and disagreements are recorded against all 56 findings in the [review response](../review/m0-design-review-response-2026-09-07.md).

Package-wide evidence rule: recommendations and numerical defaults below are proposals; environment experiments remain not run. “Documented support,” “local check passed,” and “owner-approved environment evidence” are distinct. The [verification register](12-verification.md) records each unresolved gate; repeated caveats in individual sections do not add extra approvals. `A` = positive acceptance, `F` = failure case, `S` = security/quality case, `V` = external decision/verification, `D` = decision below. Component names are explained in [architecture](02-architecture.md#component-and-data-ownership).

## M0 decisions requested

The requesting engineer and manager approve the bounded M1 scope together; named standards/security/platform owners decide within their authority. Every row is **pending**. “Blocks” names when the decision must be settled, not permission to start that milestone.

| ID | Recommendation / alternative | Decision owner | Blocks / argument |
| --- | --- | --- | --- |
| D01 | Complete nine-permission vocabulary; classification-bounded developer/app-owner data/catalog grants. Alternative: explicit separate reader grants | Both engineers + identity | M1 contract; [roles](03-domain.md#ownership-and-permission-model), ADR-0005 |
| D02 | Typed registry scopes, intersect mandatory permissions/policy, exactly one eligible primary target. Alternative: separate scope-specific grant tables | Both engineers | M1 schema; [registry](10-registry-decisions.md#grant-evaluation-and-target-selection), ADR-0010 |
| D03 | 202 execution-as-operation; five required caller fields with template expansion; accepted-only idempotency; acceptance-relative timeout. Alternative: full explicit inputs / separate queue-runtime budgets | Both engineers + API standards owner | M1 contract freeze; [API](04-api.md), ADR-0002 |
| D04 | Trusted supervisor runs pinned Compose; tests need network, not Docker API. If direct calls are required, evaluate constrained broker or stronger nested isolation before building | Both engineers + security | M2 runtime scope; [Docker](06-compute-images.md#exact-demo-and-docker-authority), ADR-0004 |
| D05 | Stable workflow chain; closed-run ticket-only recovery; bounded platform-origin cancellation failure, never unconfirmed cancelled. Alternative: revise outcome vocabulary explicitly | Both engineers | M1 lifecycle; [recovery](05-temporal-recovery.md), ADR-0003/0008 |
| D06 | M1 requires local durable fake tests **and** connected Entra/dev-Temporal subset; cross-store restore proof at M3. Independent local work may start after scoped signoff, not claim M1 complete | Both engineers + identity/Temporal owners | M1 authorization/acceptance; [gates](11-delivery.md#test-tiers-and-positive-acceptance) |
| D07 | M2.0 explicitly deploys ACA roles/mTLS; Terraform pipeline is sole image/revision writer. Alternative: separately reviewed release ownership contract | Both engineers + ACA/platform | M2 provisioning; [foundation](07-terraform.md#immediate-foundation-ownership), ADR-0001/0007 |
| D08 | Single-execution pools, cold first; user-subscription only with approved service privileges. Alternatives: Batch-service mode; separately evaluated fleet topology | Platform/network + both engineers | M2; [Batch](06-compute-images.md#batch-experiment-topology-and-ownership), ADR-0004 |
| D09 | Finite one-use identities and prelaunch claim; separate runtime identity. Alternative: proven assignment-scoped sessions, not unproven identity reuse | Security/identity + provider engineer | Before any untrusted live run; V06, ADR-0005 |
| D10 | Synthetic/internal-source data, explicit retention leases, approved verifier/trust roots; secret delivery disabled until separate per-app proof | Security/data/image owners | M2 live data/image; V09/V10, ADR-0009/0010 |
| D11 | Agree numerical lifetime/concurrency/spend bounds and fail-closed experiment register before paid tests; no budget-alert-only enforcement | Manager + FinOps/platform | M2 paid work; [cost](08-cost-approvals.md#m1m3-scope), V11 |
| D12 | Keep requested working agreement and layered Go tests; optional agents remain separately approved preferences | Both engineers | Required tooling in M1.1; agent preferences do not block core; V20/V21, ADR-0012 |

Accepting M1 records D01–D03/D05/D06 and the necessary tooling responsibilities; D04/D07–D11 may remain explicit M2 conditions. Approval to begin independent local M1 work is not waiver of connected completion checks. Unknown enterprise contract rules keep contract-dependent work draft; proceed only on independently approved work, without claiming an interface freeze.

## Review order

| Section | Artifact |
| --- | --- |
| 1 | [Executive summary and context](01-context.md) |
| 2 | [Architecture, identities, and sequences](02-architecture.md) |
| 3 | [Domain, lifecycle, and worked specification](03-domain.md) |
| 4 | [API decisions](04-api.md) and [OpenAPI 3.1.1 sketch](openapi.yaml) |
| 5 | [Temporal, persistence, and recovery](05-temporal-recovery.md) |
| 6 | [Compute, images, and Batch experiment](06-compute-images.md) |
| 7 | [Terraform foundations and future stack execution](07-terraform.md) |
| 8 | [Cost, budgets, and future approvals](08-cost-approvals.md) |
| 9 | [Security, data, audit, and observability](09-security-data.md) |
| 10 | [Registry and ADR index](10-registry-decisions.md) |
| 11 | [Delivery, requirements mapping, and acceptance](11-delivery.md), with [working agreement and test framework](working-agreement.md) |
| 12 | [Ranked questions and verification register](12-verification.md) |

[Official references and version register](references.md) distinguish published capabilities, proposed version pins, and environment evidence. [Package validation](validation.md) records only checks actually performed on these documents. All implementation and cloud tests are **not run**.

## Review gate

The [combined prompt §12](../combined-build-prompt.md#12-first-response-produce-the-design-package) says: “Stop at the design-review gate until our signoff is recorded.” No application scaffolding, dependencies, migrations, Terraform resources, or deployments have been created. The OpenAPI file is a design artifact, not an implemented API.

| Reviewer / decision | Status | Evidence / date |
| --- | --- | --- |
| Requesting engineer: design and M1 scope | Pending | Not supplied |
| Manager: design and M1 scope | Pending | Not supplied |
| Approved milestone | None | M1 proposed below |
| Conditions / accepted API conventions | Pending | Record any changes and applicable ADR revisions |
| Working agreement / agent and test approach | Requested, retained in revision 0.3.0; approval pending | Required repository/tests V20; optional agent preferences V21; nothing activated |

Recommended next approval: **M1 only**, the fake-provider vertical slice in section 11, subject to the API-convention and development identity/Temporal decisions in section 12. Cloud prerequisites may remain open while independent M1 work proceeds after signoff. M1 approval would not authorize M2 provisioning, production deployment, or future stack operations.

To record review, add each reviewer's name, date, decision, reviewed package revision or commit, and conditions to this table or link an equivalent durable review record. An outstanding technical question does not imply a decision has been approved.

Use the [ADR decision register](10-registry-decisions.md#adr-index) to record accept/conditional/defer per decision as part of the same package review. All ADRs remain Proposed until that human record exists. This revision does not supply signoff, and no implementation milestone is authorized.

## Source baseline and workspace observations

Both source documents were read in full. The combined prompt governs sequencing and overrides unresolved historical directions; the POC document supplies detailed capability requirements. Their original contents were preserved.

| Source | SHA-256 of reviewed file |
| --- | --- |
| [Combined build prompt](../combined-build-prompt.md) | `cdf49def46bc139117d6837008720ed587317145cf401d261d490b44f2c00017` |
| [POC intent and requirements](../poc-intent-and-requirements.md) | `431a8a0b2d7334b33801396569ae4ba8ca0b9ea4cee173ab2a2be033adc621e6` |

No applicable `AGENTS.md` was found in the visible workspace or parent directories. The visible workspace initially contained only these two documents and restricted metadata directories. `git status` could not recognize a Git repository; therefore no branch, commit, clean-worktree, or remote-state claim is made. Restore usable Git metadata before M1 collaboration. The POC's historical OCR link points to a file absent from this workspace; its substantive requirements are available in the reconstruction, so this does not block M0.
