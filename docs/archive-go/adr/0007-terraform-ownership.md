# ADR-0007 — Separate foundations, runtime compute, and future stack execution

**Status:** Proposed. **Date:** 2026-09-05. [Design](../design/07-terraform.md).

## Context

**Implementation note, 2026-09-07:** the original M2/M4 sequence has a separately approved one-vault exception in [ADR-0013](0013-local-terraform-lab-exception.md). It uses Terraform **1.15.9**, AzAPI **2.11.0**, restricted local state and a certificate-only native worker. General repo/bundle execution, Entra remote backend, foundations and hosted executor are still future; Terraform 1.16.0 below remains an untested foundation candidate.

Runtime disposable workloads differ from persistent declarative infrastructure. Competing Terraform and SDK writers create drift and recovery ambiguity. Later reproducible stack plans need immutable dependencies, isolated state and bound approvals.

## Decision

Use one tested-and-pinned Terraform toolchain after the compatibility gate; candidate 1.16.0 is untested. Terraform owns approved long-lived foundations and their declared properties. Provider APIs own runtime Batch/VMSS compute; no per-execution Terraform. Later stacks get one state per deployment target, fully resolved immutable module/provider bundles, Entra backend data-plane auth, local dependency preparation, refreshed saved plans and explicit mutation/recovery ownership.

M2.0 includes explicit ACA API/worker/runtime hosting, dev-Temporal mTLS dependencies and an Entra-only foundation state backend. The reviewed Terraform pipeline owns image/revision settings as well as infrastructure; publishing supplies immutable digests but does not create a second revision writer. Future target migration, bounded executor resources and plan-invalidation/re-plan rules are documented in section 7.

## Alternatives

OpenTofu is evaluated only for a concrete organizational/technical benefit, with separate compatibility evidence. Supporting both without tests creates a false guarantee. SDK shortcuts for Terraform-owned tags/RBAC/diagnostics create a second writer and are rejected.

## Consequences

Runtime pool instances are absent from foundation Terraform state. Future stack approval binds a plan/toolchain/module/target/policy/rate snapshot; state serial alone cannot prove cloud freshness. Raw plan/state confidentiality and constrained RBAC delegation remain mandatory even when secrets are written to Key Vault.

## Verification dependencies

V05/V14/V15 for approved foundations/ACA ownership and hosting; V19 for exact executor pins, transitive reproducibility, offline init, locking, secret-state inspection, unknown security values, partial apply and lost-acknowledgement recovery. The lab spike does not satisfy the general M4 executor gate; its distinct proof/limits are in ADR-0013.
