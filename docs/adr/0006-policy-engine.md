# ADR-0006 — Deterministic Go policy for the first slice

**Status:** Proposed. **Date:** 2026-09-05. [Design](../design/03-domain.md).

## Context

**Implementation note, 2026-09-07:** bounded Go input/grant/dispatch rules exist in `internal/execution` and `internal/auth`; this is not the complete registry or a generalized PolicyEngine plugin framework. OPA/CEL remain unselected alternatives. [Current layout](../design/11-delivery.md#current-layout-and-dependencies).

The first slice has a small governed catalog, fixed profile limits and object admission rules. No existing organizational Rego library has been supplied. Rules must be reproducible for recorded inputs and distinguish authorization from later cost exceptions.

## Decision and alternatives

| Option | Fit and tradeoff |
| --- | --- |
| Go rules behind `PolicyEngine` — recommended | Small deterministic typed rule set, existing language/test toolchain; changes require reviewed application release |
| Embedded OPA/Rego | Prefer if a maintained enterprise policy library makes integration simpler; adds policy/bundle versioning and another authoring language |
| CEL expressions | Consider when operators need bounded configurable expressions; does not itself supply authorization context, governance, bundle distribution or audit |

Integration options are documented by [OPA](https://www.openpolicyagent.org/docs/integration); CEL describes its expression model at [cel.dev](https://cel.dev/). No runtime is installed or selected beyond the Go proposal.

Policy input is an immutable snapshot of caller/grants/owner/spec/profile/catalog versions plus an explicit evaluation time when required. External directory/pricing/network lookups happen before evaluation and become recorded inputs. Record rule-set version/digest, input digest, allow/deny/reasons. Recheck current security revocation before dispatch; keep both admission and later decisions.

## Consequences

No arbitrary executable policy hooks, raw Terraform or caller-provided cloud config. Authorization/security denials cannot be overridden by cost approval. The narrow port allows replacing an implementation when an actual need appears without building a generalized plugin system.

## Verification dependencies

V01/V09: reviewed override schemas, table-driven allow/deny/revocation fixtures and deterministic repeated decisions; evaluate a supplied Rego library before revisiting this ADR.
