# ADR-0003 — Durable outbox and reconciliation of external effects

**Status:** Proposed. **Date:** 2026-09-05. [Design](../design/05-temporal-recovery.md).

## Context

SQL commits, Temporal starts/signals, provider submissions and object uploads are separate effects. Lost acknowledgements and worker restarts can otherwise lose accepted work or duplicate an untrusted workload.

## Decision

Commit admission, immutable spec/owner/decision, execution, idempotency response, capacity reservation and outbox atomically. Dispatch one stable workflow ID with explicit conflict/reuse handling. Persist external attempts before effects; add provider discovery by execution/spec digest, deterministic external IDs and durable duplicate markers. Fence projection writers by generation and deduplicate events. Reconcile uncertainty before submission retries, after restarts, and across restores. Keep cancellation decisions in SQL and signals as wake-ups.

Use ConflictPolicy Fail and ReusePolicy RejectDuplicate; Continue-As-New stays in the original chain. The control reconciler closes never-started expired/cancelled admissions with fencing and no-dispatch proof. Closed/absent workflows recover through leased DB tickets, never a new workload Start. Handle DB/result retry exhaustion explicitly. Cancellation-budget exhaustion is a platform-origin failed outcome with unknown presence retained, not unconfirmed cancelled. M2's durable prelaunch claim is consumed once and never reset by retry or restore.

## Alternatives

Synchronous DB-then-Temporal start leaves a dispatch gap. Temporal-only public state complicates authorization/retention/querying and does not eliminate external-effect ambiguity. A workflow ID or DB lease alone does not fence provider effects.

## Consequences

Extra tables/dispatcher/sweeper are justified by concrete failure windows. SQL is admission/projection authority, Temporal orchestration authority, provider actual-state authority. Cleanup cannot finish while late submissions remain unresolved; key retention outlives workflow history.

## Verification dependencies

F01/F03/F11/F13–F18 and V04/V13 prove crash, acknowledgement, stale-write, late-delete and restore behavior using persistent external fake state plus live conformance later. No exactly-once cloud guarantee is asserted before evidence.
