# ADR-0008 — Short activities, fixed warm workers, replay-safe changes

**Status:** Proposed. **Date:** 2026-09-05. [Design](../design/05-temporal-recovery.md).

## Context

**Implementation note, 2026-09-07:** local process-loss and version-gated legacy/new history replay have evidence; history/Continue-As-New and full retry/cancellation acceptance remain open. No ACA rollout, enterprise Temporal or scaler proof exists. The bounded native Terraform activity in [ADR-0013](0013-local-terraform-lab-exception.md) is not the short-activity compute path described below.

ACA revisions/scale-in can terminate workers; external execution takes longer than a normal activity call. The deployed Temporal server, authorization and versioning capabilities are unknown. Backlog-only scaling does not account for all active work.

## Decision

Use short bounded submit/inspect/cancel/result/cleanup activities separated by durable timers. Workload retry is separate and initially zero. Start fixed warm ACA replicas with bounded concurrency; handle shutdown and lost acknowledgements. Use deterministic workflow patching and replay corpus validation, retaining compatible paths. Record exact deployed versions before opting into current Worker Deployment Versioning. Namespace/operation authorization is mandatory independent of task queues.

Use measured history event/byte budgets and a tested Continue-As-New trigger; preserve original deadlines and drain messages. Explicit transient/permanent activity-error branches and external tickets preserve recovery across DB/result outages. M1 process/replay tests, M2.0 ACA shutdown proof and optional later autoscaling are distinct gates (V04/V15/V22).

## Alternatives

One activity spanning an entire workload complicates shutdown/recovery. Autoscaling may later help but requires proof of the exact managed ACA scaler/authentication/metric behavior. Legacy Temporal worker-versioning recipes are not automatically suitable for current deployments.

## Consequences

Idle control-worker cost is explicit; workload VMs remain separately managed. Continue-As-New/history limits and cleanup reconciliation avoid indefinitely growing histories or lost cleanup. Temporal KEDA documentation is a research input, not proof the deployed ACA service supports it.

## Verification dependencies

V03/V04/V15: server/SDK matrix, replay, messages across Continue-As-New, mTLS rotation, SIGTERM/revision kill, stalled activity recovery and bounded concurrency. Optional scale-in is V22; fixed replicas remain the fallback.
