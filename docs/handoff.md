# Start here — next coding session

**Goal:** continue local core acceptance. Normal Docker startup requires Entra; compute stays simulated. The separately authorized one-empty-Key-Vault spike and lab identity setup are complete; see [Key Vault walkthrough](key-vault-demo.md). The approved seven-day home-lab executor certificate is a limited credential exception, not a work default. No additional infrastructure, paid dependencies, credentials, commits or pushes are authorized.

**Key Vault spike complete:** deployment `dep_000b66b69f5db372949a27ae80b806e4` reached `succeeded` through the real API/Temporal/Terraform path; independent Azure readback matched. The vault/state remain; the native Key Vault worker is stopped. Do not provision again or retry the preserved rejected attempt. Use the runbook's completed-demo replay command, then continue the next local acceptance task below.

**Identity follow-up complete for the lab:** the engineer explicitly approved a seven-day certificate. The native worker and Terraform now use a dedicated lab service principal, with no human CLI fallback; actual ARM/Terraform reads passed. `make keyvault-identity-check` repeats that read-only proof. Certificate expiry: September 14, 2026, 21:18:36 EDT. The native infrastructure worker remains stopped; the existing vault and original evidence are preserved. The UAMI/RG role remain available but are not used by the lab certificate path. Read [identity setup and limits](terraform-identity.md). ACA managed-identity execution/hosted connectivity are not implemented; they require a separately scoped work-environment integration. Do not create another vault, revive old human-bound plans or introduce automatic credential fallback.

## Five-minute orientation

- **Windows setup complete:** Ubuntu-24.04/WSL2 local tests and real-Entra walkthrough passed; reviewed 2026-09-08 in [progress](progress.md). Work from `/home/cam/code/forgeapi-homelab`. Mac setup remains a future destination gate.
- **New session on another machine?** Start with [session transfer](session-transfer.md): source/config/state boundaries, fresh-machine setup, pasteable prompt and Terraform extension assessment. A clone does not contain prior deployment records or credentials.
- **Setting up at work tomorrow?** Follow [work-setup.md](work-setup.md): exact setup command, human sign-in and required live proof.
- [TLDR](TLDR.md): what to explain and roadmap.
- [Entra setup](entra-local.md): exact one-time identity configuration; no secret.
- [Progress](progress.md): evidence and limitations. Do not confuse old fixture-demo proof with connected Entra proof.
- `sh scripts/verify-local.sh`: after tenant setup, check Docker, run tests, start stack and sign in for the demo. Mac/Linux; no host Go.
- `make test-docker`: credentials-free unit/race/contract/OTLP tests. `make test-core`: real ephemeral PostgreSQL/Temporal, worker kill/restart, old/new history replay and cleanup recovery. `make vuln-docker`: vulnerability gate.

## Code map — read only the path needed

| Behavior | Implementation / nearby tests |
| --- | --- |
| Input validation/defaults | `internal/execution/input.go`, `input_test.go` |
| Auth and current grants | `internal/auth/entra.go`, `keys.go`, `policy.go`, `*_test.go` |
| HTTP authorization/ETags/cursors | `internal/httpapi/api.go`, `api_test.go`, `entra_test.go` |
| Atomic acceptance/idempotency/outbox | `internal/store/postgres.go`, tagged `postgres_integration_test.go` |
| Workflow, dispatch and recovery | `internal/orchestration/core_workflow.go`, `core.go`, `dispatch.go`, `recovery.go`, `core_integration_test.go`; `workflow.go` preserves legacy replay |
| Versioned migrations / durable simulated effects | `internal/store/migrate.go`, `migrations/`, `simulation.go`, `core_integration_test.go` |
| Local traces and durable audit | `internal/telemetry/`, `config/collector.yaml`, `internal/store/postgres.go` |
| Required Entra startup | `internal/local/config.go`, `cmd/forgeapi/main.go`, `compose.yaml` |
| One-target Terraform spike | `internal/deployment/`, `internal/httpapi/deployment.go`, `internal/store/deployment.go`, `internal/keyvaultrunner/`, `cmd/keyvault-worker/`, `patterns/key-vault/` |
| Secretless meeting walkthrough | `cmd/demo/main.go`, `login.go`, `scripts/demo.sh` |

## Next work, one task at a time

**Terraform extensibility task:** [PAT-01 — Add the Terraform pattern interface and registry](session-transfer.md#pat-01-add-the-terraform-pattern-interface-and-registry) now has concrete implementation steps, acceptance criteria and a separate copy/paste prompt. It is the recommended task before another real pattern; it is not implemented. Select it explicitly to reprioritize from the core acceptance task below. Machine setup alone does not start the refactor.

1. **Environment setup:** the single-user real Entra → Docker walkthrough passed on Linux. For the Mac/new approved tenant, follow [work-setup.md](work-setup.md). Second-user ownership denial/revocation with real tokens remains a separate unverified gate. Record only sanitized results.
2. **Next authorized local coding task:** cancellation-budget failures, permanent/transient retry classification and history-budget/Continue-As-New behavior in `internal/orchestration/core_workflow.go`, `core.go`, `recovery.go` and focused store/Temporal tests. Read only relevant recovery requirements in `docs/design/05-temporal-recovery.md`. Preserve original deadlines, cancellation intent, outcome immutability and legacy replay.
3. **Remaining local gates:** ambiguous Start response and sustained DB/result outages, retry/orphan metrics, retention rules and the remaining HTTP/security matrix. See the latest section of `docs/progress.md`; passing the current suite is not the entire F01–F16/F18/S07 matrix.

**Future identity integration, not current authorization:** implement and verify the ACA worker's managed-identity adapter and hosted PostgreSQL/Temporal connectivity only after separate scope and approval. This does not replace the next local coding task or authorize deployment.

Long polling, catalog pagination, media negotiation, current dispatch policy, migrations, durable audit/local OTLP, CI/scanning and old/new workflow replay are implemented. Valid tracestate is parsed but discarded (no approved local vendor list). Full M1 is not complete; group/app-owner policy and enterprise Temporal/security evidence remain open. Use `make up` for upgrades: it stops writers before migration and preserves data. Do not run pre-migration plaintext-idempotency writers against the upgraded database.

## Paste into the next coding session

> Read AGENTS.md and docs/handoff.md, then the current evidence in docs/progress.md. Work on only the first unfinished task above. Inspect the named files and necessary design section, not the entire design folder. Run the focused baseline, add acceptance tests, implement the smallest coherent change, and run the required gates. Preserve unrelated edits. Do not weaken authentication, create paid resources, add secret fallbacks, commit or push. End with changed behavior, actual test results, blockers and the next bounded task.

This handoff is usable with Sol; no runtime agent framework or model API key is needed. It reduces repeated discovery without skipping relevant requirements or human review. Keep one coordinating session; use helpers only when explicitly requested.
