# ADR-0010 — Multiple registered BU targets and separated vault boundaries

**Status:** Proposed; organizational choices pending. **Date:** 2026-09-05. [Design](../design/10-registry-decisions.md).

## Context

**Current applicability, 2026-09-07:** enterprise registry/vault topology remains proposed. The single configured lab target, empty vault and RG-scoped executor roles in [ADR-0013](0013-local-terraform-lab-exception.md) are not a deployed BU registry, app-secret service or approval of this topology.

Current subscriptions are pre-provisioned per BU and host multiple applications. Apps can span environments; production/nonproduction separation does not yet follow automatically. Shared vault/worker permissions can expand blast radius across apps.

## Decision

Model multiple versioned target registrations per BU and explicit app/environment/target permissions. Recommend separate production and nonproduction subscriptions for new targets, with an explicit migration from the existing arrangement. Keep historical state/resource ownership when registries change. Recommend app/region/environment vaults and application identities, separate from platform certificate/configuration vaults and target-scoped provider workers.

Grants reference typed registered ownership/target/catalog scopes with mandatory permission/classification intersections. Admission selects exactly one eligible primary target; draining/migration candidates receive no new work. Pin existing executions to their admitted target. Optional secret-delivery roles are per application/environment boundary; the public API and transfer role do not gain all-vault access.

## Alternatives

A single subscription/vault per BU can reduce setup count but requires equivalent explicit security/operational evidence and constrains isolation. A rigid BU/team/app/environment tree cannot represent shared teams and multiple app environments cleanly.

## Consequences

Target onboarding records identity, policy, quota, network, state/artifact, billing, ownership and classification facts. Tags derive from this registry and do not authorize operations. Splitting subscriptions or moving persistent resources is a later reviewed migration, not a side effect of this design.

## Verification dependencies

V05/V06/V10: platform ownership approval, current-target inventory and migration plan, least-privilege app/provider identity and vault tests. No topology approval or resource move is claimed.
