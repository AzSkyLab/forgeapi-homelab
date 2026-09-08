# ADR-0009 — Separate data retention and recover across stores

**Status:** Proposed; data-owner approval pending. **Date:** 2026-09-05. [Design](../design/09-security-data.md).

## Context

**Implementation note, 2026-09-07:** forward migrations, DB audit and sanitized local OTLP are implemented, not immutable cloud evidence storage. Retention pruning, hosted Entra DB roles and cross-store restore remain open; local tracestate is discarded. Restricted retained lab plans/state are an explicit exception to the future backend design. [Security implementation boundary](../design/09-security-data.md).

Mutable operations, workflow histories, source/log/output artifacts, future state/plans and audit evidence have different authority, access and retention. The prompt requires a minimum 30-day audit floor without inventing universal financial-services retention.

## Decision

Use PostgreSQL accepted records/projections, Temporal orchestration history, provider actual state and approved object stores for artifacts/evidence. Propose 90-day sanitized immutable audit and execution/idempotency retention, shorter diagnostic/source/output tiers and separate future state/plan rules. Use identity-based PostgreSQL access, private networking, schema-role separation and tested restore. Redact before OTLP/audit export; use one correlation ID and bounded metric labels.

Published input archives use reference/expiry leases separate from per-VM workspace and staging-upload expiry; public events follow execution retention. Workload logs cannot forge authoritative lifecycle/audit evidence; authenticated ingestion stamps identity and separates trust levels. Include Azure Activity Log as management-plane evidence. Full cross-store restore proof is explicitly M3 V13, while M1 retains restart/late-effect safety.

## Alternatives

Git-only audit is neither transactional authority nor proof of immutability. Retaining raw secret-bearing artifacts as long as audit increases exposure. Assuming an atomic cross-store restore can cause duplicate workload execution.

## Consequences

Immutability and retention need configured storage controls and owner-approved evidence, not an application claim. Restore freezes dispatch, reconciles surviving workflow/provider/object evidence and quarantines uncertainty. Proposed RPO/RTO are planning targets to approve and measure; backup retention includes deletion consequences.

## Verification dependencies

V10/V12/V13, F16/F17/S07/S10. Production collector HA, SIEM onboarding and long-term evidence engineering remain later work; minimum diagnostic/security acceptance is required in the POC.
