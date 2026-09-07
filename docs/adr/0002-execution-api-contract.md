# ADR-0002 — Execution as operation and proposed API conventions

**Status:** Proposed; standards-owner review outstanding. **Date:** 2026-09-05. [Design](../design/04-api.md).

## Context

The public capability is temporary execution with observable progress, result and cleanup. A separate job object would duplicate its status. The organization-approved Zalando revision/profile is unknown; the source proposes 202 and lowercase states.

## Decision

Use OpenAPI 3.1.1, an execution as its own operation resource, durable `202` + Location, idempotency, separate cancellation intent and bounded long-poll event pages. Use snake_case fields, plural kebab-case paths, absolute links, JSON/problem JSON, bearer transport security and no URL version prefix. Preserve lowercase source states. Close governed input schemas; keep outputs extensible. ETag protects read freshness; future mutable registries require If-Match, but cancellation does not.

Require five caller fields; expand omitted immutable template defaults into the complete spec and reject changed fixed fields. Cache only accepted 202 idempotency outcomes; an explicit default and an omitted field are different caller payloads. Keep accepted-to-finish timeout with observed started_at/timed_out_from metadata. Events carry complete status-facet snapshots; logs retain a tail cursor at empty pages; artifact names identify template outputs. Cancellation bodies are optional, keys remain required. Dot-separated registry permissions in bearer security requirements are an explicit proposed guideline naming deviation, not Entra OAuth scope definitions.

## Alternatives

Separate job plus 201 is the public Zalando async recommendation to compare at review. SSE offers push semantics but adds an ingress/reconnection requirement unnecessary for the first slice. Media-type versioning is reserved for unavoidable incompatible representations.

## Consequences

Consumers track one execution ID but must inspect workload, delivery and cleanup independently. The proposed 202/resource choice, lowercase enums, governed-input restrictions and correlation profile require explicit local standards treatment; this is not a compliance claim.

## Verification dependencies

V01 records immutable guideline revision, internal lint version and accepted deviations. F01/F14 validate canonical alias-route idempotency and payload conflicts; S02 covers object/cursor authorization. M0 sketch checks do not establish an implemented API.
