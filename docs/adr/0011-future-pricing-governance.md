# ADR-0011 — POC measurements first; bound future pricing and approvals

**Status:** Proposed. **Date:** 2026-09-05. [Design](../design/08-cost-approvals.md).

## Context

**Current applicability, 2026-09-07:** pricing, budget ledger, manager decisions and notifications remain future. The [lab exception](0013-local-terraform-lab-exception.md) has same-owner saved-plan approval only, no independent approver or cost gate; it does not prove zero subscription cost or authorize paid experiments.

Compute viability needs measured consumption and finite limits. Persistent-stack use later needs negotiated-rate estimates, concurrent budget admission and independently authorized approvals. Pricing-vendor authentication and background manager resolution are unverified dependencies.

## Decision

M1–M3 measure bounded compute and cleanup lifetimes with stated cost basis. Later evaluate Infracost estimation, approved EA/MCA rate sources and supported custom price books as complementary concerns. Require policy-compatible authentication/licensing/egress evidence before integrating. Build transactional nonoverlapping reservations/actuals/forecasts when required. Persist approval decisions bound to exact plan/estimate/target and recheck authority/freshness before apply; signals/notifications confer no authority.

Before paid M2 work, a bounded experiment register reserves conservative exposure, accounts for deleting/unknown capacity and blocks stale/unset monitoring; manager/FinOps authorization controls any ceiling change. This is distinct from the later product ledger. Later cross-BU permits name the charged owner, and confirmed apply converts reservation into nonoverlapping elapsed/remaining resource exposure atomically.

## Alternatives

Full pricing integration before the fake slice delays the first usable capability. A blanket discount cannot establish negotiated rate fidelity. Request-local budget reads permit oversubscription; manager status alone cannot approve BU caps. Storing a prohibited SaaS token in Key Vault does not resolve its policy conflict.

## Consequences

M4 exposure stays controlled until its required gates exist. Approved rate snapshots/manual bound FinOps review can support a narrow fallback. Use an approved directory feed or verified delegated Graph path for manager relationships, with explicit CI sponsors and independent production promotion approvers.

## Verification dependencies

V11/V17–V19: spend/measurement targets, representative rate/coverage tests, vendor credentials and data flow, ledger concurrency/corrections/rollover, manager/delegation/self-approval cases and stale-plan rejection.
