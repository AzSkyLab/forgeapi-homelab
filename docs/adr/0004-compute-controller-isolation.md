# ADR-0004 — Batch viability before integration; single-use VM isolation

**Status:** Proposed; live-provider decision pending. **Date:** 2026-09-05. [Design](../design/06-compute-images.md).

## Context

**Current applicability, 2026-09-07:** future live-compute proposal; no Batch, disposable-VM or workload-Docker isolation experiment has run. The completed Key Vault spike does not establish any of these controls. [Evidence status](../design/12-verification.md#current-evidence-overlay).

The representative test needs Docker capabilities and untrusted-source isolation. Batch is the first candidate, but product container support does not prove required host/metadata/image boundaries or disposable cleanup.

## Decision

M2 tests a dedicated single-node pool/job/task per execution, initially fresh dedicated capacity and no workload retry. All runtime fleet mutations go through Batch. Pin worker-image and OCI/helper digests; remove the VM after use. Recommend a trusted supervisor running pinned Compose under a protected rootless daemon, with untrusted tests receiving service-network access and no Docker API. D04 must confirm that this is the required representative workload. M2.1 supplies identity assignment, one-way launch claim, transfer and supervisor controls before the Batch gate, rather than deferring them to M3.

## Alternatives

For Docker authority compare: (1) supervisor-run Compose with no untrusted Docker API (recommended), (2) constrained rootless API broker if direct calls are required, (3) an explicitly evaluated nested isolation runtime. A broker needs its own endpoint/streaming policy, owner, estimate, image supply chain and bypass corpus; it is not assumed small or available. Host-root inside a single-use VM is not equivalent while Batch agent/node credentials remain reachable.

For capacity, a shared pool with fresh nodes could reduce churn but changes Batch's pool-level isolation argument and needs separate proof; no used VM is reassigned. Direct VMSS is a separate fallback controller/fleet, not a fix for host-root authority. Compare allocation modes, actual pool/job/core/IP quotas and deleting-pool exposure before paid tests; cold first, with any later warm pool already bound to its reserved execution.

## Consequences

First topology may have high latency/quota cost; measure before committing. Required Docker behavior cannot be silently dropped to pass. A failed safety gate blocks live integration; fake-provider work remains useful. Baseline is removal, not reimaging or reassigning warmed used nodes.

## Verification dependencies

V05–V11, F03/F08–F12/F15, S03–S09. Both engineers and security review exact runtime/identity evidence and record Batch accept/reject/pending before M3.
