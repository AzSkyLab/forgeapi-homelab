# ADR-0005 — Entra and explicit object authority

**Status:** Proposed. **Date:** 2026-09-05. [Design](../design/02-architecture.md).

## Context

**Implementation note, 2026-09-07:** normal local startup requires Entra; real human PKCE/API evidence exists. Local grants are narrower than the full registry below. [ADR-0013](0013-local-terraform-lab-exception.md) records the approved lab certificate SP/RG role and verified ARM/Terraform reads. The UAMI exists but is not used locally; hosted MI/WIF and production developer isolation remain unimplemented/unverified.

API callers, CI, platform workers, bootstrap and untrusted workloads need different privileges. Subscription RBAC, possession of an execution ID, queue names and editable tags cannot establish application-level ownership.

## Decision

Validate API-audience Entra access tokens; use PKCE for humans and federation/app roles for CI. Resolve explicit current grants against versioned app/environment/BU/target relationships. Derive owners and provider identities server-side. Enforce object and classification-based access on every status/cancel/events/log/result/artifact request. Missing/overage claims never grant authority. Give the API no compute provisioning rights; separate control/provider/node/workload identities and production Temporal access.

Use the nine-permission role vocabulary and typed grant scopes in sections 3/10, with classification-bounded developer data/catalog grants and no implied operator data access. Scope Batch management-plane pool operations and identity assign/action to a finite pre-created one-use inventory; identity reuse is not approved. Deploy the runtime transfer/claim role separately from public API and any per-application secret-delivery identity. Secret-free work still requires assignment/launch-claim/IMDS proof before M2 experiments.

## Alternatives

API keys or custom token/password issuance contradict the credential policy. Azure management tokens are for another audience. Broad group/Contributor/Owner shortcuts enlarge authority and do not satisfy object or role-assignment controls.

## Consequences

Identity onboarding/rotation/freshness are explicit dependencies. A secret-free source-artifact demo avoids requiring GitHub credentials on workload VMs. Enabling application secret delivery requires proven scoped identity and vault boundaries, not a generic reference resolver with all-app rights.

## Verification dependencies

V02/V03/V05/V06/V10/V16 and S01–S05/S11. Scope production clients and test direct Temporal bypass; verify exact Docker-granted authority cannot acquire platform credentials and optional secret delivery preserves app scope. No tenant or cloud-security test has run during M0.
