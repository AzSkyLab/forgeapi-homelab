# Repository working instructions

- Current authorized work: local-first API/core development. See `docs/progress.md`; roadmap entries are not authorization to provision or deploy.
- Start with `docs/handoff.md`; read only the code/design sections relevant to the task. Preserve original requirements, original review findings and unrelated changes.
- Keep changes small and end-to-end. Add regression/acceptance tests, implement, then verify. Do not substitute fake results for real infrastructure or security evidence.
- Commands: `make check` (Go + C compiler), `make test-docker` (containerized unit/race tests), `make test-integration` (real local PostgreSQL), `make demo` (running local HTTP/Temporal walkthrough). Never silently skip missing required dependencies.
- Keep portable API/domain types independent of Azure SDKs. Workflows must be deterministic; I/O belongs in activities or dispatch. Persist accepted intent atomically with its outbox entry.
- Entra is required for normal local startup; fixture identities exist only in tests. Keep loopback/private-network boundaries and explicit local mode. Synthetic workloads only; no paid Azure resources or deployment is authorized. Entra app configuration/testing is authorized only in the confirmed tenant.
- Avoid static API keys/client secrets. Humans use browser PKCE locally; prefer managed identities for Azure-hosted services and WIF for trusted external workloads. Do not introduce a secret fallback without explicit approval. Never commit tokens, credentials or tenant-specific local config, mount host Azure credentials into workloads, or log raw authentication errors/tokens.
- Use one coordinating session. Delegate only when explicitly requested; give helpers bounded ownership and preserve other edits. Separate worktrees/branches are preferred for concurrent writers.
- Update `docs/progress.md` with behavior, evidence, remaining risks and the next task. Human review/acceptance is separate from an assistant's completion report. Do not commit or push without being asked.
