# Co-development in small, tested changes

Start with [README](README.md) for commands and [progress](docs/progress.md) for the next task. The [TLDR](docs/TLDR.md) is the shared explanation; consult design sections only when the task touches them.

For each change, agree on one behavior, its acceptance example, owner, and exclusions. Add a failing test where practical, implement the change, then run `make check` and any affected database/integration checks. Review the diff and the actual results before accepting it. Do not claim skipped checks passed.

| Code | Owns |
| --- | --- |
| `internal/execution` | Portable input policy and public/domain types |
| `internal/httpapi` | Routes, fixture identity boundary, HTTP errors and cursors |
| `internal/store` | PostgreSQL transactions, accepted responses and outbox |
| `internal/orchestration` | Deterministic workflow decisions, dispatch and fake activities |
| `internal/local`, `cmd` | Explicit local-only configuration and process entry points |

One human is responsible for each task; one coordinating assistant integrates and verifies it. Use optional specialist agents only when explicitly requested, with a bounded task and owned files. They cannot supply human, security or deployment approval. No custom agents have been activated by this scaffold.

For concurrent engineers or writing sessions, use separate branches/worktrees and designate one editor for shared schemas/contracts. Preserve others' changes. Each engineer runs their own local stack; separate worktrees on one laptop must also use different Compose project names and host ports.

Before handoff, record changed behavior, commands/results, limitations and the next task in `docs/progress.md`. Keep original requirements/reviews intact. Changes that expand into cloud provisioning, live credentials, spending, deployment or persistent infrastructure capabilities need explicit scope approval.

The local fixture identity header is not a security solution. No real data or credentials belong in fixtures, logs, tests or documentation. Enterprise authentication and the remaining M1 safety gates must be implemented and verified before any shared deployment.
