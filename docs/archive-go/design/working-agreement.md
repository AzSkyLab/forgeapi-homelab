# Working agreement — engineers, Codex, agents, and tests

**Status:** proposed full working agreement; local implementation authorized 2026-09-07. The concise active instructions and actual commands are now in [CONTRIBUTING](../../CONTRIBUTING.md), [AGENTS](../../AGENTS.md) and [current work](../progress.md). Supports [section 11](11-delivery.md); [ADR-0012](../adr/0012-engineering-working-agreement.md) records the recommendation. Broader test/enterprise gates below remain proposals where not implemented. No specialist agents are activated.

## Agreement to review first

Recommend one accountable human owner per work item, one coordinating Codex session, small changes with observable acceptance criteria, and a shared Go-based test harness. Use bounded specialist agents for independent tasks when authorized and useful. Keep decisions and test evidence in the repository or linked work item so a new session can resume from recorded facts.

The requesting engineer has explicitly approved the local implementation increment without a separate manager gate. Broader architecture/milestone acceptance remains a human decision. Routine implementation and verification within the approved scope proceed without another approval for each file, dependency pin, or test run. Material public-contract/security/scope changes are surfaced with a concrete recommendation. Provider experiments, cloud spending, production deployment and later milestones retain their separate gates.

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

Use one coordinating session. Current repository instructions require **explicit delegation**, even for independent work; this proposal does not activate helpers. If authorized, proposed limit is coordinator plus at most two active helpers, subject to the actual session limit. Keep the user's current model selection unless they set another preference. An extra reviewer is another analysis pass, not independent security certification or human approval.

Every delegation carries: objective, owned files, relevant contracts/acceptance, allowed actions, prohibited scope, dependency assumptions and expected evidence. Tell builders they share a codebase, must preserve others' changes and must coordinate overlapping edits. Read-only review can overlap implementation in a different area; review the final integrated diff again when dependencies change. Do not keep delegating the same search, let helpers redefine public contracts independently, or allow nested delegation by default. Stop unneeded helpers and consolidate their output into one handoff.

Current Codex guidance supports explicit subagent requests and applicable project instructions; availability and controls depend on the client/session. This M0 proposal does not imply agents are already configured or running. [Official subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents).

Use separate Git worktrees/branches for independent **writing** sessions when supported, with one integration owner. Helpers in a shared checkout still require disjoint file ownership; a spawned agent is not automatically an isolated worktree. Share contracts first and serialize edits to OpenAPI, migrations, dependency locks and central configuration. Worktrees isolate files, not external databases, ports, namespaces or cloud resources: give test runs unique resource names and credentials/scopes appropriate to their environment. Git is now usable; no concurrent writer setup or CODEOWNERS assignment is implied. [Official worktree guidance](https://learn.chatgpt.com/docs/environments/git-worktrees).

## Repository instructions and durable project state

Root `AGENTS.md`, `CONTRIBUTING.md`, `docs/handoff.md` and `docs/progress.md` now exist and are the active instruction/onboarding/task record. Keep Makefile/scripts authoritative for exact commands. Codex documents repository instruction discovery; check for relevant personal overrides in each environment. Team configuration belongs in the repo, with no assumed edits to either engineer's global configuration. [Official AGENTS.md guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

Keep the active root instructions aligned with these rules:

- Read the current milestone/task and relevant accepted design. Continue authorized work through required verification; do not turn roadmap entries into permission.
- Respect one editor per shared file and preserve existing changes. Use independent worktrees for concurrent writing sessions when available.
- Delegate bounded investigator/builder/reviewer work only when explicitly authorized; integrate and verify the result yourself.
- Keep cloud calls outside deterministic workflows, provider fields outside public contracts, and secret values outside history/logs/test reports.
- Run the change-specific checks plus required gates. Never count skipped tests as passed, weaken assertions to make CI green, or claim mock/fake results prove cloud isolation.
- Update task/contract/ADR evidence when behavior or a consequential decision changes. Hand off exact checks, remaining uncertainty and next step.

Use `CONTRIBUTING.md` in M1 for human setup/branch/review instructions, `AGENTS.md` for compact machine-facing rules, accepted ADRs for lasting decisions, and a task record for live progress. Start with one root instruction file; add narrower instruction files only when a directory actually needs different rules. Any optional custom agent definitions remain checked-in proposals until activated and tested against the installed Codex client.

## Test framework and evidence boundaries

The current harness uses Go 1.27.1, chi/httptest, Temporal SDK 1.48.0 and Compose-based PostgreSQL/Temporal fixtures. Exact pins are in [references](references.md#version-register), `go.mod`, Dockerfile and Compose files. Local tests and selected connected Entra evidence are recorded in [progress](../progress.md); enterprise, full failure-matrix and Mac evidence remain separate. No test framework installation is needed merely because an older M0 candidate appears below.

| Layer | Current framework / remaining scope | What it proves and where it runs |
| --- | --- | --- |
| Domain, policy, state transitions, normalization | Native Go `testing`, table-driven tests, small explicit fakes | Input/decision behavior and transition invariants; local and every code PR |
| HTTP/auth/object boundaries | `net/http/httptest` against actual chi router/middleware; local signed JWT/JWKS fixtures | Request/response and denial behavior without Entra access; real tenant validation remains V02 |
| OpenAPI and public payloads | Redocly 2.51.2 and Python schema/example checks; `santhosh-tekuri/jsonschema/v6` 6.0.2 validates actual compute/lab-deployment handler responses in Go | No runtime request-middleware validator or enterprise profile selected; input guards have focused tests. Keep both OpenAPI contracts separate; no dialect downgrade |
| Workflow logic | Temporal Go SDK `testsuite.TestWorkflowEnvironment`, controlled activity results/time skipping | Durable-decision logic, timers/cancel/error paths; does not prove a real server/worker restart |
| Workflow compatibility | Temporal Go SDK workflow replayer with sanitized representative histories | Candidate workflow code replays histories for affected versions; run on workflow/SDK/interceptor changes |
| SQL, outbox and concurrency | Docker Compose PostgreSQL fixture, `pgx` and tagged Go integration tests; **not Testcontainers** | Real transactions/migrations/concurrency and retained evidence. `make test-integration` runs store tests; do not run concurrent suites against the same fixture DB/project |
| Real orchestration integration | Pinned local Temporal development server/CLI plus PostgreSQL fixture; separately launched API/worker and durable fake external state | Kill/restart/acknowledgement gaps F03/F11/F13/F15/F18. Test harness controls process/container lifecycle; no fixed sleeps as correctness assertions |
| Provider conformance | Persistent simulated-effect/recovery cases now; shared live-compute adapter conformance remains future | Record actual fixture effects, not only mock call counts. Key Vault evidence is not live compute conformance |
| Security, fuzz and telemetry | Auth/contract negative tests, race detector, OTLP wire tests/redaction canaries and govulncheck; broad fuzz campaign remains unverified | Token/cursor/body redaction and selected boundaries, not full S01–S11 closure |
| End-to-end acceptance and performance | Go HTTP scenario runner/standard benchmarks; approved live workload scenarios for F/S/V evidence | Full API→workflow→fake locally; enterprise/dev and live Azure gates separately; p50/p95 require real observations and agreed targets |

Go's [testing](https://pkg.go.dev/testing) and [httptest](https://pkg.go.dev/net/http/httptest), the [Temporal test suite](https://docs.temporal.io/develop/go/best-practices/testing-suite) and real Compose fixtures provide distinct evidence layers. Testcontainers, Spectral and kin-openapi were M0 alternatives, not installed requirements. Enterprise OpenAPI convention approval still requires V01; JSON Schema response checks do not replace authorization tests.

Current tests live beside packages, including tagged cross-process tests in `internal/orchestration` and PostgreSQL tests in `internal/store`; no `tests/` tree is required. Use the `integration` tag and explicit fixture endpoints for those suites. Default tests need no cloud credentials. Required integration jobs fail if their dependencies are unavailable. Fixture images/tools are pinned; first use needs approved download access. Dispose only resources the run owns. The current Compose fixture project is shared by local invocations, so serialize runs; separate worktrees alone do not isolate it.

Local fixtures may use generated short-lived test signing keys and disposable local-only database credentials. They are isolated test plumbing, not an Entra bypass or deployed credential pattern. Production startup must reject fixture issuer/fake-provider configuration in a live profile. Use synthetic inputs; no customer data, production credentials or raw secret-bearing histories in test fixtures/CI artifacts. A test runner's Docker access never demonstrates that untrusted workload Docker access is safe.

## Shared commands, CI gates, and definition of done

The following commands exist in the current Makefile/scripts. Dockerfile pins the Go build/test environment; no Dev Container is checked in. GitHub Actions defines local checks in `.github/workflows/core.yml`; remote execution is not claimed. Documentation validation remains a separate [Python/Redocly command](validation.md), not part of `make check`. The full required local/connected/live case sets remain in [section 11](11-delivery.md#test-tiers-and-positive-acceptance).

| Current command | Purpose / when required |
| --- | --- |
| `make check` | Host Go + C compiler: formatting, vet, unit/HTTP/workflow/contract tests with race detection, and build |
| `make test-docker` | Containerized credentials-free unit/race/contract/redaction tests; no host Go needed |
| `make test-integration` | Real PostgreSQL store/migration/concurrency tests; not Temporal integration |
| `make test-core` | Real PostgreSQL + Temporal, worker process kill/restart, selected history replay and recovery |
| `make vuln-docker` | Pinned govulncheck; reachable advisories fail the gate |
| `make up`; `make demo` | Upgrade/start the local stack preserving volumes; browser PKCE and authenticated synthetic HTTP/Temporal walkthrough |
| `sh scripts/verify-local.sh` | Mac/Linux preflight, tests, startup and signed-in local demo; actual Mac execution remains unverified |
| `make keyvault-identity-check` | Explicit read-only check under the configured lab executor against the existing vault; needs valid lab config/certificate, not a unit test |

`make test-replay`, `make test-security` and `make test-acceptance` are not implemented targets; use the actual suites above rather than copy the old proposed names. No live create/apply is part of normal tests or core acceptance. `make keyvault-demo`/`make keyvault-worker` are separately scoped operational commands, not default verification gates; use the retained-result replay instructions in the Key Vault runbook.

The [Go race detector](https://go.dev/doc/articles/race_detector), [fuzzing](https://go.dev/doc/security/fuzz/) and [govulncheck](https://go.dev/doc/security/vuln/) complement behavior tests; none individually proves correctness or security. Disable optional tool telemetry where required by the enterprise development policy; the Spectral container's documented opt-out must be included if that distribution is selected.

Publish `go test -json` output, relevant sanitized test artifacts, schema/replay results, tool/version and commit/config identity as CI evidence. A concise PR links the task and explains observed behavior, checks, remaining limitations and any migration/rollback concern. Coverage helps find untested critical branches; there is no invented blanket percentage gate. Authorization, idempotency/dispatch, lifecycle and cleanup require explicit negative/failure-path coverage. Flaky required tests need an owner and repair; rerunning until green is not evidence that the failure is resolved.

A task is ready for human review when its acceptance behavior is implemented, affected contract/docs are consistent, required checks actually passed and meaningful risks/unrun checks are identified. A milestone is accepted only when its F/S/V evidence and required human decisions are recorded. An agent's completion message, a mock test, or a successful build cannot substitute for those gates.
