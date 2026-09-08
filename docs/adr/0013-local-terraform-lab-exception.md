# ADR-0013 — Local-first core and bounded Terraform executor exception

**Status:** Proposed decision record; the scoped operations below were explicitly authorized by the requesting engineer and implemented. Human review of this ADR and enterprise/milestone acceptance remain pending. **Date:** 2026-09-07. Updates the implementation context of ADR-0001/0005/0007/0008; it does not accept their future architecture wholesale.

## Context

The engineer prioritized a runnable local API before hosting, then separately requested one empty Key Vault through API/Temporal/Terraform, a UAMI with RG-scoped RBAC, and a dedicated home-lab service principal with a seven-day certificate. Ordinary local Docker has no Azure managed-identity endpoint. The original vault create used the engineer's CLI identity; that runtime path has since been removed.

## Decision recorded

- Keep normal API, synthetic compute worker, PostgreSQL, Temporal development server/UI and local OTLP collector in Docker Compose. Entra is mandatory; humans use browser PKCE. Fixture authentication is confined to automated tests. No Azure credentials or Terraform executable enter API/compute containers.
- Keep the infrastructure spike separate: four [lab deployment operations](../design/openapi-deployments.yaml), additive deployment records/outboxes, separate plan/apply workflow IDs and one trusted **native host** worker. Run only the embedded, digest-pinned create-only pattern; retain state and the created vault. This is not arbitrary existing-repository execution or full M4.
- Separate API caller authority from executor authority. The approved lab service principal has **Key Vault Contributor on the test RG only**, no data-plane role, role-assignment-write grant, Graph/API permission or client password. This role is broader than create-only; the pattern guard is an application constraint, not equivalent Azure RBAC.
- Use only the explicitly approved seven-day certificate in restricted ignored host files. Bind its public executor identity/fingerprint to accepted intent and recheck before plan/apply. No human CLI, managed-identity, OIDC or secret fallback in the current runner. Do not rebind historical plans to the new identity.
- Preserve the UAMI and its RG role as bootstrap inventory, not proof of managed-identity execution. ACA worker identity (system-assigned if selected, or UAMI), credential adapter and hosted connectivity require separately approved work. Prefer managed identities for Azure hosting and WIF for trusted external workloads; the certificate is not a work default.

## Alternatives

Continuing with the engineer's CLI identity was rejected for the runtime path. Creating paid hosting merely to obtain managed-identity tokens was not authorized. Local WIF requires a trusted issuer and deliberately configured trust; none was configured. A client password/API key or automatic fallback was not selected.

## Consequences

The lab key holder can exercise the service principal's Azure rights directly. This does **not** establish production developer isolation, and existing human Azure role assignments were not revoked. Work isolation requires a privileged worker developers cannot commandeer, plus independently verified Azure/Temporal/DB boundaries.

State/plans remain in restricted local directories; there is no remote encrypted state backend, automatic rotation, general import/update/destroy or full partial-apply recovery. The Key Vault worker remains stopped after verification. Certificate expiry is **2026-09-15 01:18:36 UTC**; expiry does not delete the app or RBAC. Rotation/revocation needs administrator review. No additional resources, paid dependencies, credential fallback, commit or push is authorized by this record.

## Verification dependencies

Recorded evidence in [progress](../progress.md): actual API → Temporal → Terraform creation and independent Azure readback passed under the original human identity; subsequent certificate-only ARM and Terraform **read-only** verification passed under the dedicated executor. A create/apply under that executor is **not** claimed. Original failed/corrected attempts, plans, state and receipts are retained.

[Identity runbook](../terraform-identity.md) and [Key Vault runbook](../key-vault-demo.md) contain setup, expiry, replay and remaining risks. These are narrow lab results, not closure of V03/V06/V19 or full M1/M4. Next authorized coding work remains local cancellation/retry/history-budget acceptance in [handoff](../handoff.md).
