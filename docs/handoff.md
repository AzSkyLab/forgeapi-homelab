# Start here — next coding session

**Goal:** finish the core API before infrastructure. Normal Docker startup requires Entra; compute stays simulated. No paid Azure resources, deployments, static Azure credentials, commits or pushes are authorized by this handoff.

## Five-minute orientation

- **Setting up at work tomorrow?** Follow [work-setup.md](work-setup.md): exact setup command, human sign-in and required live proof.
- [TLDR](TLDR.md): what to explain and roadmap.
- [Entra setup](entra-local.md): exact one-time identity configuration; no secret.
- [Progress](progress.md): evidence and limitations. Do not confuse old fixture-demo proof with connected Entra proof.
- `sh scripts/verify-local.sh`: after tenant setup, check Docker, run tests, start stack and sign in for the demo. Mac/Linux; no host Go.
- `make test-docker`: credentials-free unit/race tests. `make test-integration`: separate ephemeral PostgreSQL tests.

## Code map — read only the path needed

| Behavior | Implementation / nearby tests |
| --- | --- |
| Input validation/defaults | `internal/execution/input.go`, `input_test.go` |
| Auth and current grants | `internal/auth/entra.go`, `keys.go`, `policy.go`, `*_test.go` |
| HTTP authorization/ETags/cursors | `internal/httpapi/api.go`, `api_test.go`, `entra_test.go` |
| Atomic acceptance/idempotency/outbox | `internal/store/postgres.go`, tagged `postgres_integration_test.go` |
| Fake workflow and dispatch | `internal/orchestration/workflow.go`, `dispatch.go`, `workflow_test.go` |
| Required Entra startup | `internal/local/config.go`, `cmd/forgeapi/main.go`, `compose.yaml` |
| Secretless meeting walkthrough | `cmd/demo/main.go`, `login.go`, `scripts/demo.sh` |

## Next work, one task at a time

1. **Environment setup:** the single-user real Entra → Docker walkthrough passed on Linux. For the Mac/new approved tenant, follow [work-setup.md](work-setup.md). Second-user ownership denial/revocation with real tokens remains a separate unverified gate. Record only sanitized results.
2. **Bounded event long-polling:** own `internal/httpapi/api.go` and focused HTTP tests; consult `docs/design/04-api.md` pagination section. Accept only a bounded wait, return new events or the unchanged tail cursor on timeout, honor cancellation, reauthorize before delivery, and preserve existing non-wait behavior. No new endpoint/provider/queue.
3. **Recovery/dispatch policy:** own orchestration/store tests and corresponding implementation; choose one crash/revocation case from `docs/design/05-temporal-recovery.md`. Prove it with a failing test before changing behavior. Live compute remains disabled.

Catalog pagination, content negotiation/tracestate, migrations, audit/telemetry, CI/scanning, workflow replay and connected Temporal evidence remain in the core backlog. Full M1 is not complete. Group/app-owner policy, dispatch-time revocation and production security need separate work.

## Paste into the next coding session

> Read AGENTS.md and docs/handoff.md, then the current evidence in docs/progress.md. Work on only the first unfinished task above. Inspect the named files and necessary design section, not the entire design folder. Run the focused baseline, add acceptance tests, implement the smallest coherent change, and run the required gates. Preserve unrelated edits. Do not weaken authentication, create paid resources, add secret fallbacks, commit or push. End with changed behavior, actual test results, blockers and the next bounded task.

This handoff is usable with Sol; no runtime agent framework or model API key is needed. It reduces repeated discovery without skipping relevant requirements or human review. Keep one coordinating session; use helpers only when explicitly requested.
