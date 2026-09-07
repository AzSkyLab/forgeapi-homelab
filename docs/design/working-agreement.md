# Working agreement — engineers, Codex, agents, and tests

**Status:** user-requested working agreement, revised 2026-09-07; approval pending. Supports [section 11](11-delivery.md); [ADR-0012](../adr/0012-engineering-working-agreement.md) records the recommendation. Required repository/test setup is V20; optional agent preferences are V21. This document neither approves M1 nor activates agents.

## Agreement to review first

Recommend one accountable human owner per work item, one coordinating Codex session, small changes with observable acceptance criteria, and a shared Go-based test harness. Use bounded specialist agents for independent tasks when authorized and useful. Keep decisions and test evidence in the repository or linked work item so a new session can resume from recorded facts.

You and your manager approve architecture/milestone direction together. After M1 approval, routine implementation and verification within that scope proceed without another approval for each file, helper, dependency pin, or test run. Material public-contract/security/scope changes are surfaced with a concrete recommendation. Provider experiments, cloud spending, production deployment and later milestones retain the gates already defined in the design.

## People, responsibilities, and decisions

| Participant | Responsibility | Decision / handoff |
| --- | --- | --- |
| Requesting engineer | Primary day-to-day collaborator; supply workload intent, prioritize tasks and review API/developer experience | Accept task behavior; identify a human owner/reviewer and resolve product details within approved scope |
| Manager / second engineer | Architecture partner; agree acceptance targets, tradeoffs and enterprise dependencies; own engineering tasks as assigned | Joint milestone/design signoff; route organizational decisions to their actual owners |
| Codex coordinating session | Read current instructions/state, turn the agreed task into a bounded change, implement/integrate, verify and report evidence | Responsible for the integrated result; no self-awarded human/security approval |
| Optional specialist agent | Inspect, implement or review a specific assigned subtask | Return exact findings/diff, files, evidence and unresolved risks to the coordinator |
| Human reviewer | Review consequential code/contract changes and the supporting evidence | One appropriate human reviewer per ordinary change; both engineers need not approve every file or routine PR |
| Platform, identity, security and data owners | Supply access/configuration and accept decisions within their responsibility | Their tenant/security/data approvals cannot be supplied by Codex or inferred from another role |

The API/data and orchestration/provider columns in section 11 are workstreams, not permanent assignments of you versus your manager. Assign them per task based on capacity and expertise. Either engineer can pair with Codex; when multiple chats operate concurrently, make checkout and file ownership explicit.

## One work item, from intent to review

1. **Define:** record task ID, approved milestone, intended behavior, acceptance examples, F/S/V requirement IDs, scope/exclusions, human owner, affected files/contracts and required checks. A short issue or Markdown task record is sufficient.
2. **Claim and inspect:** identify current branch/worktree and existing edits; confirm which person/session owns affected files. Read the relevant accepted design/ADR and only the code needed for this task.
3. **Implement:** make a reviewable vertical increment. For a bug or changed behavior, establish the failing regression case where practical, then fix it. Update the API/schema/migration docs when the behavior requires it. Preserve unrelated edits.
4. **Verify:** run the targeted checks and the required CI gates for affected boundaries. Keep the integrated branch as the final evidence target. A skipped or unavailable required test remains unverified.
5. **Review and hand off:** report what changed and why, paths/commit, exact commands/results, any unrun checks, unresolved failures, next step and rollback implications. Obtain the appropriate human review; merging/publishing/deployment follows the task's existing authorization.

Suggested task example: `M1.2 / F13 — recover accepted execution after dispatcher dies`. Acceptance: crash after SQL commit and after Temporal start; one accepted execution, one workflow identity, one fake workload, monotonic events. Owner A edits admission/outbox SQL; owner B edits dispatcher/fake crash harness; shared schema changes have one designated editor. Evidence is invocation counts, DB rows, events and workflow identity after restart, not a mock reporting that Start was called once.

On a steering message, the coordinator records the changed requirement and adjusts the current task. A status question does not cancel ongoing work. At handoff/session end, update the task record with completed work, open decisions, test evidence and the next actionable item; do not rely on chat memory as the sole project record. Report progress when a finding, blocker or decision changes what happens next, without making users supervise routine commands.

## Agent architecture and boundaries

These are development assistants, distinct from Temporal activities or agents running inside workload VMs. No OpenAI runtime dependency, generalized agent platform, new API key, plugin installation or custom agent service is required for the Infrastructure Platform API.

| Agent role | Good bounded task | Expected output |
| --- | --- | --- |
| Investigator | Locate a relevant code path or evaluate one dependency/contract question, read-only | Findings with file/line or official-source evidence and a recommended next step |
| Builder | Implement one independently scoped change in assigned files/module | Diff, meaningful tests, exact test output and unresolved integration assumptions |
| Reviewer | Review the actual diff against task acceptance, especially auth, concurrency and cleanup | Actionable findings with location, risk and verification gaps; no edits unless reassigned |

Default to the coordinator alone for small/sequential changes. Once this agreement is approved and encoded in applicable `AGENTS.md`, request a specialist only when it can independently advance the task or review a consequential change. Proposed limit: coordinator plus at most two active helpers, subject to the actual session limit. Keep the user's current model selection unless they set another preference. An extra reviewer is another analysis pass, not independent security certification or human approval.

Every delegation carries: objective, owned files, relevant contracts/acceptance, allowed actions, prohibited scope, dependency assumptions and expected evidence. Tell builders they share a codebase, must preserve others' changes and must coordinate overlapping edits. Read-only review can overlap implementation in a different area; review the final integrated diff again when dependencies change. Do not keep delegating the same search, let helpers redefine public contracts independently, or allow nested delegation by default. Stop unneeded helpers and consolidate their output into one handoff.

Current Codex guidance supports explicit subagent requests and applicable project instructions; availability and controls depend on the client/session. This M0 proposal does not imply agents are already configured or running. [Official subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents).

Use separate Git worktrees/branches for independent **writing** sessions when supported, with one integration owner. Helpers in a shared checkout still require disjoint file ownership; a spawned agent is not automatically an isolated worktree. Share contracts first and serialize edits to OpenAPI, migrations, dependency locks and central configuration. Worktrees isolate files, not external databases, ports, namespaces or cloud resources: give test runs unique resource names and credentials/scopes appropriate to their environment. Usable Git metadata is a prerequisite here and remains unresolved in V20. [Official worktree guidance](https://learn.chatgpt.com/docs/environments/git-worktrees).

## Repository instructions and durable project state

During M1.1, create a short root `AGENTS.md` from the accepted agreement. Include actual setup/check commands once they exist; keep the task runner authoritative rather than copying scripts into instructions. Codex documents repository instruction discovery; verify a fresh session reads the intended repository guidance and check for relevant personal overrides. Team configuration belongs in the repo, with no assumed edits to either engineer's global configuration. [Official AGENTS.md guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

The initial root instructions should explicitly cover these rules:

- Read the current milestone/task and relevant accepted design. Continue authorized work through required verification; do not turn roadmap entries into permission.
- Respect one editor per shared file and preserve existing changes. Use independent worktrees for concurrent writing sessions when available.
- Request bounded investigator/builder/reviewer help under the agent rules above only when useful; integrate and verify the result yourself.
- Keep cloud calls outside deterministic workflows, provider fields outside public contracts, and secret values outside history/logs/test reports.
- Run the change-specific checks plus required gates. Never count skipped tests as passed, weaken assertions to make CI green, or claim mock/fake results prove cloud isolation.
- Update task/contract/ADR evidence when behavior or a consequential decision changes. Hand off exact checks, remaining uncertainty and next step.

Use `CONTRIBUTING.md` in M1 for human setup/branch/review instructions, `AGENTS.md` for compact machine-facing rules, accepted ADRs for lasting decisions, and a task record for live progress. Start with one root instruction file; add narrower instruction files only when a directory actually needs different rules. Any optional custom agent definitions remain checked-in proposals until activated and tested against the installed Codex client.

## Test framework and evidence boundaries

Recommended application tools are named below; exact versions/digests and Go 1.27.1/Temporal SDK candidate compatibility must be tested and pinned in M1.1. Standard-library components follow the selected Go toolchain. Application harnesses remain uninstalled/unverified; the separate M0 documentation tooling has actual results in validation.md.

| Layer | Proposed framework / setup | What it proves and where it runs |
| --- | --- | --- |
| Domain, policy, state transitions, normalization | Native Go `testing`, table-driven tests, small explicit fakes | Input/decision behavior and transition invariants; local and every code PR |
| HTTP/auth/object boundaries | `net/http/httptest` against actual chi router/middleware; local signed JWT/JWKS fixtures | Request/response and denial behavior without Entra access; real tenant validation remains V02 |
| OpenAPI and public payloads | M0 uses pinned Redocly 2.51.2 plus repository-relative Python schema/example regressions; M1 evaluates enterprise rules and a 3.1.1-capable Go request/response validator | Reproduce [validation commands](validation.md); do not substitute style/schema lint for authorization or runtime testing; `kin-openapi` remains an unselected compatibility candidate |
| Workflow logic | Temporal Go SDK `testsuite.TestWorkflowEnvironment`, controlled activity results/time skipping | Durable-decision logic, timers/cancel/error paths; does not prove a real server/worker restart |
| Workflow compatibility | Temporal Go SDK workflow replayer with sanitized representative histories | Candidate workflow code replays histories for affected versions; run on workflow/SDK/interceptor changes |
| SQL, outbox and concurrency | Testcontainers for Go PostgreSQL module; real migrations and unique per-run database | Transaction/constraint/lease behavior and restart durability; local container runtime and CI integration job, no SQLite substitute |
| Real orchestration integration | Pinned local Temporal development server/CLI plus PostgreSQL fixture; separately launched API/worker and durable fake external state | Kill/restart/acknowledgement gaps F03/F11/F13/F15/F18. Test harness controls process/container lifecycle; no fixed sleeps as correctness assertions |
| Provider conformance | One Go contract suite with fake and later Azure fixture factories and explicit capability expectations | Same observable invariants, including no duplicate launch and verified cleanup; record actual effects rather than just SDK mock calls |
| Security, fuzz and telemetry | Native Go fuzz seeds/property checks, race detector, collector/test exporter with synthetic secret canaries | Malformed input/idempotency/cursor safety, data races and pre-export redaction; fuzz budgets bounded in CI |
| End-to-end acceptance and performance | Go HTTP scenario runner/standard benchmarks; approved live workload scenarios for F/S/V evidence | Full API→workflow→fake locally; enterprise/dev and live Azure gates separately; p50/p95 require real observations and agreed targets |

Go's [testing](https://pkg.go.dev/testing) and [httptest](https://pkg.go.dev/net/http/httptest) provide the base; [Temporal's Go test suite](https://docs.temporal.io/develop/go/best-practices/testing-suite) and [Testcontainers PostgreSQL](https://golang.testcontainers.org/modules/postgres/) cover their distinct layers. [Spectral](https://github.com/stoplightio/spectral) and [kin-openapi](https://github.com/getkin/kin-openapi) are candidates with version-specific capability checks; do not down-convert the API silently to accommodate tooling. Use the organization's approved 3.1 validator if the candidate cannot validate the actual dialect.

Keep ordinary Go tests near their package and cross-process/provider suites in `tests/`. Use explicit `integration` / `acceptance` test selection so default tests need no cloud credentials. The required integration job must fail if Docker/Temporal/PostgreSQL is unavailable; it cannot silently skip and report success. Fixture dependency images/tools are pinned and pre-fetched through the approved development path. Dispose only resources the run owns, using test cleanup plus a bounded failure janitor.

Local fixtures may use generated short-lived test signing keys and disposable local-only database credentials. They are isolated test plumbing, not an Entra bypass or deployed credential pattern. Production startup must reject fixture issuer/fake-provider configuration in a live profile. Use synthetic inputs; no customer data, production credentials or raw secret-bearing histories in test fixtures/CI artifacts. A test runner's Docker access never demonstrates that untrusted workload Docker access is safe.

## Shared commands, CI gates, and definition of done

The following names are the proposed M1 developer interface; **they do not exist yet**. The M0 documentation checker/lint command in [validation](validation.md) does exist and is not application scaffolding. Dev Container and GitHub Actions will wrap the same checks. No browser/UI test framework is needed for the API-only first slice. The exact required local/connected/live case sets are in [section 11](11-delivery.md#test-tiers-and-positive-acceptance).

| Proposed command | Purpose / when required |
| --- | --- |
| `make check` | Formatting check, vet/lint, API lint/schema examples, unit/HTTP/workflow tests with race detection where supported, and build; every relevant code PR |
| `make test-integration` | Real PostgreSQL/Temporal/fake scenarios; required for M1 behavior changes at these boundaries and before milestone acceptance |
| `make test-replay` | Replay retained sanitized histories; required on workflow/SDK changes |
| `make test-security` | Auth/authorization/redaction regressions, fuzz seed corpus and `govulncheck`; required for security-relevant changes, dependency gates before release |
| `make test-acceptance` | Requires an explicit environment/authorized target; runs connected dev or live Azure scenarios only for the current approved milestone |

The [Go race detector](https://go.dev/doc/articles/race_detector), [fuzzing](https://go.dev/doc/security/fuzz/) and [govulncheck](https://go.dev/doc/security/vuln/) complement behavior tests; none individually proves correctness or security. Disable optional tool telemetry where required by the enterprise development policy; the Spectral container's documented opt-out must be included if that distribution is selected.

Publish `go test -json` output, relevant sanitized test artifacts, schema/replay results, tool/version and commit/config identity as CI evidence. A concise PR links the task and explains observed behavior, checks, remaining limitations and any migration/rollback concern. Coverage helps find untested critical branches; there is no invented blanket percentage gate. Authorization, idempotency/dispatch, lifecycle and cleanup require explicit negative/failure-path coverage. Flaky required tests need an owner and repair; rerunning until green is not evidence that the failure is resolved.

A task is ready for human review when its acceptance behavior is implemented, affected contract/docs are consistent, required checks actually passed and meaningful risks/unrun checks are identified. A milestone is accepted only when its F/S/V evidence and required human decisions are recorded. An agent's completion message, a mock test, or a successful build cannot substitute for those gates.
