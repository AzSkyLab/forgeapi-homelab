# 9. Security, compliance, observability, and data

**Status:** proposed control design, not a compliance attestation. [Index](README.md).

**Current evidence/limits:** mandatory Entra human sign-in, current-grant denial tests, local audit/OTLP redaction and certificate-only executor reads have recorded evidence. Local PostgreSQL uses development credentials; Temporal dev/UI has no enterprise TLS/authorization boundary; audit is not WORM. Hosted identity/DB roles, cross-store restore, retention pruning, full S07 metrics/security and production developer isolation remain open. The approved seven-day lab certificate is a private-key credential, not secretless production isolation; its holder can directly use its RG rights. [Identity ADR](../adr/0013-local-terraform-lab-exception.md), [verification status](12-verification.md#current-evidence-overlay).

## Threat model and evidence owners

Treat all submitted repository code, including internal code, as untrusted. Control-plane administrators, image publishers, data readers and approvers are distinct responsibilities even when two engineers initially operate several roles. Review separation with security before live use.

| Threat / boundary | Control and test | Evidence owner |
| --- | --- | --- |
| Forged token / confused deputy | Tenant/issuer/audience/signature/time/scopes; negative token corpus including ARM and ID tokens | API engineer + identity team |
| ID guessing / editable ownership | Registry relationships and object/data authorization on every route; cross-user/app/env status/cancel/log/artifact denial | API engineer |
| Group overage / stale grants | No allow-on-missing behavior; approved membership feed with freshness and deny tests | Identity team |
| Privileged Docker / IMDS / Batch agent access | Constrain exact Docker authority, protect supervisor/agent credentials, metadata/network isolation and bypass tests | Provider engineer + security |
| Cross-execution data/cache leakage | Fresh VM, scoped inputs/outputs/session, no reused workspace, verified deletion | Provider engineer + data owner |
| Image/source tampering | Digests, signatures/provenance, source archive digest, approved templates/helper images and revocation | Image owners |
| Duplicate/late provider effects | Persisted attempts, stable IDs, lookup/reconciliation, deletion-race tests | Provider engineer |
| Authorization bypass via Temporal | Operation/namespace authorization, production credential boundary, denied direct start/signal/admin/history | Temporal team |
| Secret exfiltration in history/logs/artifacts | Reference-only history, attribute allowlist/redaction before export, output classification/quarantine | Both engineers + data owner |
| Unbounded compute/data spend | Transactional concurrency, governed finite resources/time, maximum lifetime and cleanup sweep | Platform + FinOps |
| Audit tampering | Immutable object evidence with distinct append/read/admin identities, digest manifests and search index | Security/audit team |
| Workload telemetry/log injection | Separate untrusted log ingestion from trusted lifecycle/audit export; authenticate supervisor assignment, overwrite claimed execution/owner attributes server-side, encode control characters and test forged IDs/stages/success/cleanup claims | Both engineers + observability/security |
| Malicious or compromised template publisher / Batch service | Review template/image publication separately from approval; inventory Batch service/agent privileges as trusted third-party authority, not workload credentials | Security + platform/image owners |
| Restore causes duplicate work or resurrected access | Dispatch freeze and cross-store reconciliation; replay same IDs; restore token/identity authorization checks | DB/Temporal/platform owners |

### SOC 2 evidence mapping

Use the organization's selected audit scope/control wording; this is a proposed engineering mapping to the AICPA Trust Services Criteria, not a claim of certification or a universal legal retention requirement. [AICPA criteria, 2017 with 2022 revised points of focus](https://www.aicpa-cima.com/resources/download/2017-trust-services-criteria-with-revised-points-of-focus-2022).

| Control theme | Candidate criteria family | Evidence and accountable owner |
| --- | --- | --- |
| Logical access and least privilege | CC6 | Token/RBAC/registry grant tests, privileged-access reviews, revocation and isolation results; identity/security owner |
| Authorized, tested change | CC8 | Reviewed ADRs/PRs, immutable build and image provenance, deployment approval, replay/rollback results; engineering manager |
| Monitoring and incident response | CC7 | Redacted correlated events, cleanup exceptions/orphan response, review records; platform operations/security |
| Risk assessment and suppliers | CC3/CC9 | Threat model, provider viability, pricing/vendor credential decisions; security/vendor owner |

Production evidence review cadence and controls not exercised by the POC remain open; Azure service certifications do not certify the application.

## Data classes, retention, and deletion

Recommend synthetic/nonproduction test data and approved internal source only for the initial live experiment; regulated/customer data is disabled until the data owner approves classifications and paths. Region/residency policy covers PostgreSQL replicas/backups, object versions, Temporal history, telemetry and image replication, not just VM region. Transit uses TLS/mTLS, database connections verify server identity, and storage uses approved at-rest encryption; evaluate customer-managed keys only against an actual requirement.

All periods below are **proposed operational defaults**, not statutory claims. The combined prompt's minimum 30-day audit floor is preserved. Legal holds override normal deletion only for the affected approved evidence class; avoid collecting forbidden content in the first place.

| Record | Proposed POC retention / access | Deletion/recovery rule |
| --- | --- | --- |
| Sanitized audit decisions/transitions | 90 days immutable, never below 30-day floor; auditor read, separate publisher | Compliance chooses production tier/legal hold; raw artifacts are excluded |
| Execution/spec/idempotency/projection | 90 days after terminal plus resolved cleanup; current object authorization | Active/uncertain records retained; minimal evidence persists under audit policy |
| Diagnostic logs/traces | 7 days, pre-export redacted; scoped developers/operators | No raw tokens/commands/environment; sampling cannot remove mandatory audit events |
| Published input archives | Proposed 7-day publication retention plus active execution leases; app/classification-scoped consumption | Admission requires retention through deadline/cleanup; lease-aware deletion never removes another execution's input. No new lease after expiry/revocation; backup/soft-delete copies need approval |
| Input workspace on VM | Removed with the single-use VM at cleanup | No cross-execution reuse; independent of shared archive retention |
| Public execution events | 90 days after terminal plus resolved cleanup, sanitized metadata only | Active/uncertain retained; expired cursors return 410 and clients refresh projection |
| Test results / SBOM / output artifacts | 7 days; classification-based reads | Hash/type/size manifest survives only as sanitized audit evidence |
| Staging/partial uploads | ≤24h, inaccessible to callers | Sweep after upload lease expires; successful upload may be recovered by manifest |
| Temporal history | Propose 7 days after closure in dev; namespace owner confirms | No secrets/full specs; not used as 90-day idempotency authority |
| DB automated backups | Propose 7 days for POC; production based on agreed recovery/classification | Restore only into reviewed private scope; deletion latency includes backups |
| Future raw plans/plan JSON/work directories | At most 24h after finalized/expired operation, subject to approved active plan window | Highly restricted; do not retain as long as sanitized audit |
| Future active Terraform state | Life of deployment plus approved recovery/deletion window | No audit WORM lock on actively mutable state; restricted versions/backups |

Blob immutable storage is a candidate for sanitized evidence, with time-based retention/legal-hold behavior subject to exact account/container configuration and review. Application append-only DB roles and hash chains help detect tampering but do not independently create immutable storage. Test WORM in a disposable approved scope before locking real retention policies; retention locks have material consequences. [Azure immutable storage](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-storage-overview).

Input/output size caps and source-persistence rules require data-owner values in V10. Structural API limits are set in section 4, but an unset live artifact/volume cap must block the affected capability. Artifact manifests contain classification, residence, digest, byte size, content type, owner, expiry and deletion status. Never reuse a broad SAS/storage key or expose credential-bearing cloud URLs through public APIs.

## PostgreSQL access, schema change, and backup

Recommend PostgreSQL Flexible Server with private access, Entra authentication and managed identity for hosted API/workers. Map Entra principals to least-privilege PostgreSQL roles; use a separate migrator/admin role, token-aware connection establishment, TLS verification and bounded pools. Refresh tokens for new connections and test recycling/expiry/failover; a password field carrying an Entra token is not a static password design. Developer access uses their development Entra principal. [PostgreSQL managed-identity connections](https://learn.microsoft.com/en-us/azure/postgresql/security/security-connect-with-managed-identity).

Use numbered forward SQL migrations, migration checksum/history, and a deployment migration lock. Expand first, deploy compatible readers/writers, then contract only after old revisions are gone and backup/restore tests support it. No application startup DDL or automatic destructive rollback. Index execution ownership/status, due outbox rows, idempotency unique scope, provider attempt keys, and event sequence. API/worker roles cannot delete audit evidence or bypass tenant/app predicates; evaluate DB row-level security as defense in depth after testing pooled session behavior, not a replacement for application authorization.

Flexible Server documents PITR and backup retention up to 35 days; actual backup redundancy and supported configuration depend on deployment. Proposed POC DB recovery objectives are RPO ≤15 minutes and RTO ≤4 hours, subject to DB-owner agreement and a measured restore. These are planning targets, not measured service guarantees. Published audit/output recovery targets: no loss of an acknowledged durable object inside its retention window, restore access within 4 hours; demonstrate version/redundancy behavior and account for publication gaps. Temporal RPO/RTO and backup compatibility come from its operating team. [PostgreSQL backup and restore](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/concepts-backup-restore).

Restore to a new endpoint; reestablish Entra roles/private DNS/TLS/keys and compare DB records, object manifests, Temporal runs and provider resources before dispatch resumes. Committed PostgreSQL intent can be lost within RPO; surviving provider/workflow evidence must be reconciled or quarantined, never silently resubmitted. No cross-store atomic restore is claimed.

## Minimum telemetry and audit schema

Use OpenTelemetry SDK instrumentation in API, control/provider activities, adapter and host agent, with one OTLP export path to an approved POC collector/backend. Target behavior propagates validated W3C context through allowlisted Temporal headers and links short spans after durable waits. Current local code propagates `traceparent` but parses and discards `tracestate` because no vendor list is approved; it exports only to the local collector. Host-agent/live-provider telemetry remains future work. Execution ID and workflow/activity IDs belong in logs/traces/audit, not metrics labels. Drop arbitrary baggage, provider resource IDs and sensitive request attributes from public telemetry; restricted operator telemetry has separate access.

One export platform can have separate authenticated ingestion pipelines. Untrusted workload stdout/stderr cannot emit authoritative audit/lifecycle events or select another execution's correlation attributes. The runtime/host collector stamps assignment-derived identity and ingestion time, preserves source trust labels and rejects forged control events. Provider observations and platform decisions alone establish completion/cleanup. S07 injects forged execution IDs, success/cleanup spans, ANSI/control characters and secret canaries. Include Azure Activity Log for management-plane mutation evidence, Batch task/node observations for workload evidence, and application audit for authorization; none substitutes for the other.

If application-secret delivery is later enabled, the per-app/environment delivery identity reads only admitted secret versions in that vault; the transfer/claim role and public API have no vault read grant. The application policy binds execution, secret purpose/version, expiry and destination before each delivery. S11/V06 tests authorized delivery, wrong app/version, revoked/expired assignment, history/log redaction and post-cleanup denial. Until that gate passes, `secret_refs=[]` is enforced, not an advertised working secret feature.

Required stage vocabulary: `api.admission`, `execution.persist`, `workflow.dispatch`, `profile.resolve`, `provider.submit`, `capacity.allocate`, `worker.ready`, `image.admit`, `image.pull`, `container.start`, `container.finish`, `artifact.publish`, `execution.cancel`, `execution.timeout`, `execution.cleanup`. Record only observed timestamps; inferred state transitions do not manufacture latency samples.

```json
{
  "schema_version": "1.0.0",
  "event_id": "evt_example",
  "execution_id": "exec_example",
  "sequence": 12,
  "occurred_at": "2026-09-05T18:00:00Z",
  "recorded_at": "2026-09-05T18:00:01Z",
  "event_type": "execution.cleanup_observed",
  "actor_ref": "principal-record-id",
  "owner_binding_ref": "owner-binding-version",
  "spec_digest": "sha256:example",
  "policy_version": "policy-1",
  "stage": "execution.cleanup",
  "outcome": "succeeded",
  "evidence_refs": ["evidence-record-id"]
}
```

This is a schema illustration, not an actual execution. Admission evidence also records authenticated principal/client, authorization decision and grant provenance; immutable template/profile/image/source versions; worker/control identity; operation attempts/retries; result/artifact digests and cleanup proof. Future cost/approval events add immutable binding references. Store safe structured values, not full environments, commands, source or raw SDK errors. Resolve detailed provider references only in restricted evidence, separate from the public event feed.

Metrics use bounded `environment`, `profile`, `execution_class`, `stage`, `outcome` labels. Capture counters for accepted/denied/terminal executions, retries/throttling/capacity/quota/errors/orphans; gauges for active/uncertain work; histograms for queue/readiness/pull/start/run/cancel/cleanup; usage quantities with defined units. Compute startup and cleanup p50/p95 for separate cold/warm cohorts with sample count and missing-observation rate. Numeric thresholds are V11 decisions.

M1 implements audit append/outbox, searchable execution evidence and redaction tests; M2/M3 prove provider/agent stages and cleanup evidence. Export immutable audit in the approved live evidence store once available; M1 local append-only fixtures are not a WORM claim. Production collector HA, dashboards/alerts/SLO operations, full SIEM onboarding, sampling tuning and long-term retention engineering are later acceptance work.
