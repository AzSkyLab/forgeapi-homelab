# ADR-0012 — Shared engineering workflow, bounded agents, and layered tests

**Status:** Proposed. **Date:** 2026-09-05. [Working agreement](../design/working-agreement.md).

## Context

The original delivery plan identified two workstreams and acceptance scenarios but did not specify how the engineers and Codex coordinate, how optional agents own changes, or which framework produces the evidence. Those decisions affect reproducibility, review effort and safe parallel work.

## Decision

Recommend one accountable human task owner and one coordinating Codex session. Use bounded investigator/builder/reviewer helpers when authorized and useful, with explicit file ownership and one integration owner. Create concise root AGENTS.md and human contributing instructions during approved M1.1; keep current task state and acceptance evidence durable. Routine approved milestone work continues without repeated per-file approval.

Use Go's native tests/HTTP tools, Temporal test suite and replay, Testcontainers PostgreSQL, a pinned real Temporal integration fixture and shared provider conformance scenarios. Reuse the M0 Redocly/schema checks; evaluate/pin an OpenAPI 3.1.1-capable Go validation library and enterprise rules against this exact contract in M1. Separate local fixture, connected enterprise and live cloud evidence in CI and milestone acceptance.

This agreement was explicitly requested in the conversation. Keep task ownership and required repository/test setup in V20; optional development-agent/client preferences are V21 and do not block M1. M0 now has a versioned documentation checker and Redocly 2.51.2 profile; application harness/Go library selection still belongs to M1. No delegation is activated by this revision.

## Alternatives

An always-active specialist fleet increases coordination and token use without a demonstrated benefit for every task. Ad hoc chat instructions and unnamed test tools make handoff/reproduction difficult. Mock-only tests cannot establish persistence, restart safety or cloud isolation; requiring live Azure for every code change would unnecessarily couple routine development to paid/shared resources.

## Consequences

Humans review consequential direction and changes; Codex integrates and verifies within authorized scope. Agent review supplements human review. Worktrees and per-run resources prevent interference only when explicitly configured. The new working agreement is a design proposal; no agents, project instruction files, dependencies or CI runners are activated by creating it.

## Verification dependencies

V01/V04/V20: approve required task/test responsibilities with M1 scope, restore usable Git, assign owners, pin compatible tooling and reproduce checks in Dev Container/CI. V21 separately governs optional agents/client instruction behavior; it does not block coordinator-only work. Existing acceptance cases remain authoritative; skipped tests never count as passed.
