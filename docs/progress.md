# Current work — local core implementation and acceptance

## Pattern-registry handoff clarification — 2026-09-07

Expanded the previously buried recommendation into [PAT-01: Add the Terraform pattern interface and registry](session-transfer.md#pat-01-add-the-terraform-pattern-interface-and-registry), with named extraction points, end-to-end binding/compatibility requirements, a test-only second pattern, checks and a dedicated implementation prompt. Linked it prominently from the main handoff. Status remains planned; selecting this task is separate from machine setup or authorization to add/provision a real resource. Documentation only; no code/runtime/Azure change. PASS: 37 local links/anchors across four handoff/status documents and `git diff --check`; previous runtime evidence was not rerun. Next action is destination setup, then the engineer's selection of core acceptance or PAT-01.

## Cross-machine handoff and Terraform modularity review — 2026-09-07

Added [session transfer](session-transfer.md), linked from handoff/work setup. The source checkout was clean at `3a2b74a` before this documentation change; the new handoff edits must also be included in the source transferred. The document separates fresh core setup from unsupported deployment-state migration, explains reuse of existing Entra registrations without the ignored bootstrap journal, and warns that completed-vault replay is not safe to treat as historical replay on an empty database.

**Assessment:** API/SQL/outbox/Temporal/workspace/credential boundaries are reusable, but pattern ID/catalog selection, model/store defaults, target resource IDs, exact plan guard, variables, ARM checks, outputs and RBAC are still Key Vault-specific. A small pattern contract/registry extraction with Key Vault preserved and a synthetic test-only second pattern is recommended before another real resource type; it is not implemented or newly authorized. Existing repository execution, hosted MI and broader state/recovery remain separate work.

**Evidence/limits:** read the actual model, plan guard, HTTP/store/worker/pattern and setup boundaries. PASS: all 30 links/anchors in the four handoff/status documents resolve, the existing design/schema checker passes, and `git diff --check` passes. No application, tenant or Azure test was rerun. Destination setup/Mac runtime remains unverified. No credentials/state copied, runtime behavior changed, commit or push performed. Next action: reproduce fresh local core on the destination; existing next coding task remains cancellation/retry/history-budget acceptance unless the engineer explicitly reprioritizes the Terraform extraction.

## Design/ADR reconciliation — 2026-09-07

Updated the design package to 0.4.0 with current local topology, implementation notes on all original ADRs, actual layout/commands/pins, and an evidence overlay separating local/lab proof from future enterprise/live-compute gates. Added ADR-0013 for the already-authorized Key Vault/certificate exception; **all ADRs remain Proposed pending human review**. Original requirements and review findings were preserved. Four deployment operations now have a separate lab OpenAPI contract, leaving portable compute schemas and their provider-field denylist intact. Approval deduplication, legacy executor omission, zero pre-plan timestamp, public target metadata and no general repository/update/destroy support are explicit.

**Evidence:** the new actual-handler contract test failed on the absent lab schema, then passed after implementation. Python design/schema/router/link checks and Redocly 2.51.2 lint for both contracts passed. Native `make check` passed format/vet/unit/race/build after an approved rerun allowed loopback test sockets; the sandbox restriction was not a product failure or skipped test. [Reproducible checks and evidence limits](design/validation.md). No product runtime logic, dependencies, credentials, Azure resources or running stack changed; no integration/live demonstration was rerun, commit made or push performed.

**Remaining risks / next task:** human review of the updated contract/ADR, existing incomplete M1/enterprise/isolation gates, and the lab credential/state limits remain. Next authorized coding task is local cancellation/retry/history-budget acceptance from handoff; hosted managed identity and general Terraform-repo execution still require separate scope/approval.

## Documentation consistency follow-up — 2026-09-07

Reconciled the handoff, meeting close and historical checkpoints: the one-vault spike is complete, the approved lab certificate is a limited exception, the current executor bundle differs from the original successful-create bundle, and rejected-record polling is fixed. No code, runtime or Azure changes. **Evidence:** all 39 local link targets across ten onboarding/status documents resolve, and `git diff --check` passes; anchors and external URLs were not checked, and prior runtime evidence below was not rerun. Remaining implementation risks are unchanged. **Next authorized local coding task:** cancellation/retry/history-budget acceptance; ACA identity/hosting remains future work requiring separate scope and approval.

## Executor identity transition — lab certificate path verified, 2026-09-07

**Latest explicit approval:** the engineer approved a dedicated home-lab service principal with a short-lived certificate. Created **ForgeAPI Lab Terraform**, its service principal and **Key Vault Contributor on the test RG only**. Its self-signed RSA certificate lasts seven days and expires **2026-09-15 01:18:36 UTC / September 14 21:18:36 EDT**. No client password, API/Graph permission, tenant-wide consent, extra compute, additional vault or Defender change. Existing human Azure role assignments were not changed.

**Implemented:** the native worker and pinned Terraform provider now use certificate-only authentication, separate from the Entra API caller. The worker reuses the existing MSAL dependency with in-memory tokens; ARM reads no longer shell out to the human's CLI. Terraform's CLI/MSI/OIDC fallbacks are disabled and ambient credentials/command overrides are stripped. The approved private key stays in ignored `.local/lab-executor/client.pem` (0600, parent 0700), never Docker/Temporal/API/state. New accepted targets bind executor client/principal/fingerprint; changed identity/certificate, missing configuration and expired certificates fail closed. The target now pins bundle `sha256:f41d8ff766b736abe972f0f2215b411f736c18da2dea6fd9c5aecf7217a55e9a`. Original receipts/history remain immutable; old human-authenticated plans cannot be rebound/applied under the new executor. The demo now stops immediately on retained `rejected_no_effect` records.

**Evidence:**

- PASS: repeatable `scripts/setup-lab-executor.mjs` created the dedicated objects and read back the exact public certificate/service principal/RG assignment; a second run verified/reused them without duplicates or rotation. Journaled uncertain writes stop for review. Runtime identifiers/path are in ignored `config/terraform-executor.local.json`; bootstrap IDs are in `.local/lab-executor/setup.json`.
- PASS: `./.local/bin/forgeapi-keyvault-worker -verify-identity` authenticated as the configured non-human principal, verified actual ARM vault settings, then ran a fresh Terraform **data-source-only plan** against that same existing vault. Client `87e3277b-5b5f-4b3d-96af-57bc4aa5dd4d`, principal `63e0da96-202f-49a0-98a3-cae9a0c11b5d`; no token printed. This proves certificate/ARM/provider reads, **not a new create/apply under the service principal**.
- PASS: native `make check` (vet, race tests, build), Terraform validation, certificate validity/fingerprint/path/permission guards, token identity and delegation rejection, no credential inheritance/fallback, fixed ARM host/GET-only behavior and redaction, data-only verification boundary. Initial executor regression tests failed before implementation; the certificate fixture was corrected to explicitly use a 0700 parent, preserving the production permission check.
- PASS: focused real PostgreSQL race tests for executor/certificate approval mismatch, immutable receipt replay across a configuration identity change, legacy-human rejection without outbox writes, expiry, concurrency and preserved no-effect recovery.
- PASS: `make up` rebuilt/upgraded local API/compute binaries; health succeeds and tokenless identity returns 401. Before/after hashes of both deployment bodies/receipts and both original saved plans/states are identical; zero undelivered deployment outboxes. Explicit missing-identity startup exits 1 with no CLI fallback even though the administrator remains signed in.
- PASS: final focused race tests include immediate historical-rejection handling; updated worker cross-builds for Darwin arm64 and amd64. Actual Mac execution remains unverified. No new dependency was added for the certificate flow.

The native infrastructure worker remains stopped after verification; `make keyvault-identity-check` is the safe repeatable check. The existing vault/state and UAMI/role are retained. The UAMI is not used by the lab certificate path. No commit or push. [Short identity guide, setup and expiry](terraform-identity.md).

**Future identity integration — requires separate scope and approval:** implement and verify the ACA worker's managed-identity credential adapter and hosted connectivity at work; current code deliberately accepts only `lab_certificate` and local PostgreSQL/Temporal endpoints. This is not the next authorized local coding task or permission to deploy. No managed-identity execution or new non-human create is claimed. Do not turn the lab certificate into a work default, use automatic human/secret fallback, rerun the original saved plan, or create another resource merely for a test. Certificate rotation/revocation needs explicit administrator review; the registration/RBAC do not auto-delete at certificate expiry. A person controlling the private key or privileged worker can exercise its permissions outside ForgeAPI; production developer isolation remains a separate gate. No extra activity-log role was added for recovery; unavailable log access fails closed.

### Earlier UAMI bootstrap checkpoint (superseded runtime decision)

**2026-09-07 authorization/evidence:** the engineer separately requested a UAMI in the same test RG and the RBAC needed for Terraform. Created `uami-forgeapi-terraform-01` in `centralus` and assigned **Key Vault Contributor** at `forgeapitestRG01` only. Independent Azure GETs verified both identity and assignment. Exact identifiers are in ignored `config/terraform-identity.local.json`; this is a bootstrap inventory, not configuration already consumed by the worker. No Contributor/Owner, role-assignment-write or data-plane role was granted. The built-in role permits broader vault management than the create-only application guard; do not describe it as an RBAC create-only role. No existing human permissions were changed. No credential, federated trust, compute or Defender configuration was created.

At this earlier checkpoint, the worker still used human CLI authentication and a certificate exception had not yet been approved. The subsequent approval, implementation and actual non-human read proof are recorded above. Ordinary local Docker has no Azure managed-identity endpoint; the work-hosted identity and production isolation boundary are still unverified.

The bootstrap alone was not execution proof. Preserve the completed vault, original rejected attempt, saved plans/state and receipts. [ACA identity behavior](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity), [external federation](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation), [credential guidance](https://learn.microsoft.com/en-us/entra/identity-platform/security-best-practices-for-app-registration).

## Earlier completed spike — one empty Key Vault through Terraform/API, 2026-09-07

This checkpoint preserves the original create and recovery evidence. The later identity approval, current bundle and certificate-only runtime are recorded above; the original successful create used human CLI authentication.

**Live result — PASS, 2026-09-07 20:59:33 EDT:** `kv-forgeapi-0907-a8c2` was created in `forgeapitestRG01`, `centralus`, through the authenticated API → Temporal → Terraform path. Deployment `dep_000b66b69f5db372949a27ae80b806e4` reached `succeeded`; the helper exited 0. The engineer explicitly reconfirmed this one-vault exception after the latest pasted instructions raised an authorization conflict. The assistant then approved the inspected saved plan, digest `sha256:5f3df47f43ba7c309572c5cfac2e3284366e61e6e1d3dcfacf98038ee5037fb9`, through the API. This is live implementation evidence, not separate human acceptance of the entire design/core.

Independent ARM readback returned `Succeeded`, Standard SKU, RBAC enabled, public access disabled, empty access policies/network allowlists, trusted-service bypass off and seven-day soft delete; purge protection was unset. PostgreSQL contains all six ordered lifecycle events and both delivered outboxes. Retained Terraform state contains exactly one managed vault matching the Azure resource ID. The native Key Vault worker was stopped after success; the local core Docker stack and both original/corrected workspaces remain. Corrected state and plan are mode 0600 inside a mode 0700 directory. No data-plane operations, other resources or Defender changes were performed. This final pass inspected the real saved plan/hash, API result, database, Terraform state and Azure resource; no additional broad test run was needed.

The engineer explicitly requested one simple Key Vault in the specified existing test RG/subscription. That approval superseded the earlier synthetic-only authorization **only for this one empty Standard vault**. Exact tenant/subscription/RG/name/owner stay in ignored `config/deployment.local.json`; the live subscription is in the already confirmed tenant and the RG exists in `centralus`. `Microsoft.KeyVault` was already registered. At that checkpoint, no new Entra objects, permissions, secrets, paid dependencies or hosting were authorized; the subsequent identity approvals are recorded above.

Implementation at this checkpoint: four separate deployment routes (not mutations of the synthetic compute contract), a digest-pinned Terraform/AzAPI pattern, additive migration 005, atomic acceptance/approval outboxes, stable-ID Temporal phases and a trusted native host runner using the signed-in human's Azure CLI. The one-target guard permits exactly one empty Standard vault create with RBAC authorization and public network access disabled; rejects target overrides, extra resources, update/delete/import actions and changed/expired plans. The resource is retained on success. Apply has one attempt; an uncertain outcome requires inspection rather than automatic retry/destroy. [Runbook and limitations](key-vault-demo.md).

Evidence at this checkpoint:

- PASS: Terraform 1.15.9 `validate`; AzAPI 2.11.0 signed checksum lock for Linux amd64 / Darwin arm64 / Darwin amd64.
- PASS: native and containerized `make check` (format/vet/unit/race/build), including plan/auth/environment/workflow guards.
- PASS: real PostgreSQL concurrent deployment acceptance/replay, one resource owner, conflict/expiry/digest/target approval and atomic outbox checks (`make test-integration`, `make test-core`). Existing worker-kill and legacy/new-history replay checks still pass.
- PASS: additive live migration; 19 executions and 25 idempotency records preserved, five migrations applied, no prior compute history removed. Pre-change ignored DB backup: `.local/pre-keyvault-20260907.dump` (0600). This is not a cross-store recovery guarantee.
- PASS: new worker/demo cross-build for Apple Silicon and Intel Mac; actual Mac execution remains unverified.
- PASS: Docker-built native worker extraction/help on Linux (host Go optional); shell syntax and preserved design/source-hash checks. `make vuln-docker`: zero reachable Go vulnerabilities; unreachable imported/module advisories remain reported.
- Connected API → Terraform plan **PASS**: after correcting the target-file permissions and completing fresh browser sign-in, `dep_b2cb28e137f32a511eeb01aebe4860ee` reached `planned`. Its original saved plan digest is `sha256:b75de97b58e865b6004c3cfee96665d6c807144524d93271ce06a1fdc88216f1`. Inspected one Standard vault create, zero updates/deletes, exact approved RG/region and security settings. The assistant submitted approval of that exact plan through the authenticated API under the engineer's create request; this was not a separate human acceptance review.
- Original connected apply **FAILED**: Azure rejected explicit `enablePurgeProtection=false` with HTTP 400 / `BadRequest` (activity correlation `96d9aec8-fffe-fa8b-ec16-4604fcc35b91`). The API preserved the attempt as `recovery_required`, with no automatic retry. At that checkpoint ARM listed no vault, the retained Terraform state had no resources, and a host process check found no running Terraform process. This attempt did not create the vault; the corrected attempt above did.
- Pattern fix: omit that property on create, matching the ARM service contract. A regression test first reproduced rejection of omission in our guard, then passed after updating the pattern/guard; explicit false/true/null are now rejected for this constrained pattern. Terraform validation and focused Go tests pass. The original saved plan, state, record and original target digest remain preserved: **do not retry that plan or silently rebind it**. The successful create used corrected bundle `sha256:cb88ba4dc8f939d155c4ca9faee1e67093876d78615600ded09d3ced80266d4b`; the current certificate-only bundle is recorded above.
- No-effect recovery **PASS**: the operator-only recovery command verified the original failed Temporal workflow, stopped Terraform processes, unlocked empty state, unchanged saved-plan hash, ARM absence and the exact Azure rejection correlation/message before marking the original record `rejected_no_effect`. Original receipt/history/outboxes remain intact; only its target-ownership slot was released. Focused recovery unit tests and the real PostgreSQL preservation/re-admission regression passed. The successful corrected request used fresh key `key-vault-corrected-20260907-01`; this was not an automatic apply retry.
- Engineer instruction: **do not use Defender**, and subsequently proceed without further Defender checks. No Defender plan was enabled or changed; a proposed read-only pricing-tier check was interrupted before a result was obtained. Do not repeat those checks or change existing subscription security settings. Existing coverage/billing remains unverified, not a guarantee of zero subscription cost.

This one-vault task is complete; retain the vault and state. To show the existing result again, use the corrected request key in the [runbook](key-vault-demo.md#show-the-completed-demonstration-again); the default key still belongs to the original rejected attempt. The helper now stops immediately on `rejected_no_effect`, with regression evidence in the identity update above. Remaining spike limitations include trusted-host credentials, local-only state, a narrowly recognized rejection recovery and no general update/destroy/repo execution or full crash matrix. Next bounded coding task remains the local cancellation/retry/history-budget acceptance below. A future existing-repo integration needs a separately approved commit-pinned bundle. Do not create another vault, erase history or broaden infrastructure scope. No commit or push.

## Latest core build — 2026-09-07

The local core implementation is running and the engineer's fresh real-Entra walkthrough passed. **Full core/M1 acceptance is not yet complete**: the remaining gates below include local work, not just enterprise access. This update does not authorize infrastructure or ratify the original design/ADRs.

Implemented in this build:

- All ten API operations, catalog pagination, event waits (0–25s), unchanged empty-tail cursors, reauthentication/current-grant checks after waits and disconnect handling. Long polls have a 64-request local cap.
- Standard media-range negotiation, weak/list ETags, acceptance headers, same-origin problem types, bounded log pages and digest/size-verified artifact bytes. Actual handler responses are validated against the target OpenAPI schemas in Go tests.
- Serialized admission (10 outstanding per caller / 100 total or backlogged intents), including unresolved cleanup; replays do not consume slots. Current grant and template revocation are checked before new dispatch. Failed dispatch backs off and becomes visibly attention-required after repeated failure.
- Four checksum-verified transactional migrations. Existing idempotency scopes are SHA-256 digests at rest; responses and execution histories remain intact. Atomic admission also writes a separately queryable audit ledger.
- A persistent **synthetic** provider-effect table, retained late-allocation tombstones, separate outcome/delivery/cleanup projections and durable recovery tickets. Independent reconciliation recovers unfinished finalization and closed-workflow projections. No image, workload or Azure compute is launched.
- Version-gated Temporal workflow evolution; original activity semantics retained for old histories. New activity retries have explicit call/retry budgets. Synthetic capacity/image/nonzero-exit classification has unit tests; these are not live provider observations.
- Private container-loopback `/livez` and `/readyz`; local OTLP collector with no published port/remote exporter; allowlisted trace fields, no body/token/baggage export. Valid tracestate is parsed then discarded because no vendor entries are approved locally. Trace export is best-effort; PostgreSQL audit is durable.
- Read-only-permission, SHA-pinned GitHub CI definition for format/vet/unit/race/build, PostgreSQL/Temporal recovery/replay and vulnerability checks. No deployment/publication or PR secrets. Remote CI execution is not claimed.

Evidence:

| Check | Observed result |
| --- | --- |
| `make check`; `make test-docker` | PASS: native/containerized unit/race checks, contract schemas, auth, long polling, artifact integrity and telemetry redaction |
| `make test-core` | PASS: real PostgreSQL migration/replay, concurrent admission, dispatch revocation/expiry/backoff, persistent effect retry, late-allocation fence, blocked cleanup and recovery |
| Actual worker process kill/restart | PASS: one allocation, eight ordered projection events; replayed 58 new-history events and 22 pre-version-marker events with current code |
| Cleanup call blocked by a real PostgreSQL lock | PASS: delivery completed independently, succeeded outcome stayed immutable, cleanup failure/ticket stayed visible; cleanup converged after lock release |
| OTLP wire/redaction and live collector | PASS: actual protobuf export and correlated local worker/cleanup/delivery traces; credential/body/identity canaries not exported |
| `govulncheck@v1.7.0 ./...` | PASS: zero reachable vulnerabilities after upgrading `golang.org/x/text` 0.37.0 → 0.39.0 for GO-2026-5970. Scanner also reports unreachable imported/module advisories; not a claim of no advisory anywhere |
| Existing local stack upgrade | PASS: 16 executions and 21 idempotency records preserved; four migrations applied; zero plaintext scopes remain. Both application volumes retained; pre-upgrade DB snapshot is in ignored `.local/core-upgrade.invTqQ/database.dump` |
| Fresh real-Entra walkthrough | PASS: `exec_46ee0cabf57c44907963ca8e64820af4` succeeded with verified synthetic artifact; `exec_46af7666bc9b1ea3eedcf4c9da57fa72` cancelled; `exec_bda6bbbf454fe165d288e1ac1add8dec` timed out. Replay/conflict and tokenless/former-fixture denial passed; helper exited 0 |
| Final rebuild and portability | PASS: final containerized `make check`, normal `make up`, host health response and tokenless identity 401. API and demo cross-builds passed for `darwin/arm64` and `darwin/amd64`; actual Mac execution remains unverified |

Upgrade rule: use `make up`, which stops API/worker before migrating. Migration 003 intentionally makes old plaintext-key writers incompatible (they fail atomically); do not run old and new writers together. There is no destructive down-migration. Prefer a forward fix; reverting to the old binary requires a coordinated pre-upgrade restore **before any new work is accepted**, not an ad-hoc rollback of one store. The saved PostgreSQL snapshot alone is not cross-store disaster-recovery proof.

### Remaining acceptance work, in order

1. **Next local task:** enforce/verify cancellation-budget failure, permanent-versus-transient activity failure handling and history-budget/Continue-As-New behavior. Add precise failure-window tests, including ambiguous Start acknowledgement and sustained DB/result outages. Current tests cover selected failure cases, not all F01–F16/F18 combinations.
2. Complete the bounded retry/orphan metrics and remaining S07 evidence; exercise the full HTTP/error/permission matrix beyond the actual-response schema samples. Define/test retention pruning without deleting active idempotency/tombstones. Broad group/app-owner/classification policy is not implemented.
3. Human review plus connected second-user revocation/ownership, Mac execution and enterprise Temporal TLS/namespace authorization. None is replaced by fixture tests or the successful single-user local walkthrough.
4. Only after M1 acceptance, separately approve shared hosting/live compute. No paid resources, deployment, commit or push was performed.

## Earlier implementation and identity evidence

**Authorization, 2026-09-07:** the requesting engineer approved starting local API implementation and a runnable demonstration with tests/TLDR. This is scoped approval to build; it does not assert manager, enterprise-security or cloud-deployment signoff. Infrastructure provisioning remains out of scope.

**Identity update, 2026-09-07:** the engineer requested Entra as the normal configuration (not opt-in), then explicitly authorized setup in the current Azure CLI tenant. Two dedicated app registrations and their service principals were created; the engineer completed browser sign-in and the real-token Docker API walkthrough passed. Paid Azure resources remain prohibited. Local human sign-in uses PKCE; future hosted/external workloads should use managed identities/WIF. No client secret, API key, ARM role or tenant-wide consent was created.

## Implemented

- Go API and separate worker; local Docker Compose with PostgreSQL and persistent Temporal development server/UI.
- One immutable fixture template; five required inputs, governed defaults, strict JSON parsing and bounded requests.
- Atomic PostgreSQL acceptance + idempotency + outbox; stable Temporal workflow ID; retryable dispatch.
- Submit/alias, status, cancellation, event/log pages, results, artifact download and identity/catalog routes.
- Clearly simulated lifecycle/output; owner/auditor policy; same-origin URLs, signed resource/principal-bound cursors and sanitized errors.
- Native Go unit/HTTP tests, Temporal virtual-clock tests, tagged PostgreSQL tests and an executable HTTP demonstration.
- Entra-only runtime authentication: tenant/issuer/audience, RS256/JWKS, token times, approved client, delegated scope or explicit app-only role. No fixture startup/fallback; former demo headers are rejected.
- Current file-backed grants for the synthetic application/environment, tenant/object-ID ownership, owner/auditor data separation and per-request revocation even with old ETags/cursors. The latest build also rechecks grants before dispatch; this is not the full enterprise policy registry.
- MSAL browser PKCE helper with in-memory tokens and a Docker-built host-native executable; required default Compose Entra settings, separate credentials-free automated-test project, Mac/Linux rehearsal script and compact [coding handoff](handoff.md).
- Repeatable identity-only setup using the signed-in Azure CLI (`scripts/setup-entra.mjs`, Node built-ins only). Generated IDs/journal, current grants and cursor key stay in ignored local files. Exact [work setup instructions for the next assistant](work-setup.md) are ready.

## Current authentication verification

| Check | Result |
| --- | --- |
| `make check` | PASS: formatting, vet, race tests and build, including signed-token HTTP/ownership/revocation tests |
| `docker compose -f compose.test.yaml run --build --rm --no-deps tests` | PASS: credential-free containerized unit/auth/HTTP/workflow/race suite |
| `make test-integration` | PASS: atomic concurrent acceptance, cancellation ownership/replay and reconnect durability against isolated Docker PostgreSQL |
| `sh scripts/demo.sh -h` | PASS on Linux: classic Docker builder, host-native executable extraction, help-only invocation and temporary build-container cleanup; no sign-in attempted |
| Native helper cross-builds for `darwin/arm64` and `darwin/amd64` | PASS: both binaries compiled; actual Mac execution remains unverified |
| `docker build --target runtime -t forgeapi-local:dev .`; network-disabled startup-denial checks | PASS: non-root runtime image builds with public CA certificates; missing Entra settings and explicit `AUTH_MODE=fixture` both exit unsuccessfully as required |
| Normal Compose without `.env`; example-values `config --quiet`; test Compose config | PASS: normal configuration refuses missing Entra values; both files validate with appropriate synthetic configuration inputs |
| `python3 docs/design/tools/validate_design.py`; `git diff --check` | PASS: source hashes/design regressions and whitespace |
| Local onboarding links; shell syntax; config/build artifact exclusions | PASS: 21 local links resolve, both scripts parse, local config and generated helper are gitignored |
| Real tenant registration + browser token + local API walkthrough | **PASS, 2026-09-07:** the engineer completed MSAL browser PKCE; the Docker API validated a real Entra access token and the full authenticated walkthrough passed |
| `node scripts/setup-entra.mjs --tenant <confirmed-tenant>` | PASS: created dedicated API/client registrations + service principals, generated config, then a second run verified/reused the same objects without creating duplicates or overwriting local grants/key |
| Read back the dedicated client's actual delegated consent | PASS: user-specific (`Principal`) consent only for `executions.access` on ForgeAPI and ordinary `openid offline_access profile` sign-in scopes; no tenant-wide grant |
| Second real user, managed identity/WIF caller, Mac execution | **NOT VERIFIED** |

The API startup intentionally fails without valid Entra settings and a readable grant file. `/healthz` alone cannot prove authentication works. Old fixture-owned database rows remain intact but are not readable by new Entra principals. No schema reset/data migration was needed: ownership and idempotency now use namespaced tenant/object IDs.

Handoff state: the Entra-configured API and worker are running with PostgreSQL and Temporal, all using the existing preserved application volumes. Host `/healthz` succeeds and tokenless identity access returns 401. The separate test database is disposable. No static Azure credential or paid resource has been created. Read [work-setup.md](work-setup.md) to repeat this on another approved tenant/Mac; do not reuse this machine's tenant-specific config or credentials.

Connected evidence: `exec_fb9e5342a8a72dc70e0ef9a8204bc477` completed successfully after real Entra sign-in. Alias/original idempotency replay stayed stable, changed input returned 409, missing token and former fixture header returned 401, events/logs/results and synthetic artifact digest/size checks passed. `exec_0d7390d4182d4b9d97a58c205a940a0b` was cancelled while running; `exec_8b8930e138c20503c4fd04902e16e51a` timed out. The helper exited 0 with `PASS: local walkthrough complete`. These are local synthetic execution IDs, not reusable fixtures. No token was printed or persisted by the helper.

## Earlier fixture baseline — historical evidence, not current Entra proof

Verified 2026-09-07 on Linux amd64: Go 1.27.1, Docker Engine 29.7.2, Compose 5.5.1, PostgreSQL 16.14, Temporal CLI/server image 1.8.3 and Go SDK 1.48.0. Full M1 acceptance is **not** claimed.

| Check | Result |
| --- | --- |
| `make check` | PASS: formatting, `go vet`, race-enabled unit/HTTP/workflow tests, build |
| `docker compose run --build --rm --no-deps tests` | PASS: Docker-only unit/HTTP/workflow tests with race detector |
| README's tagged PostgreSQL test command | PASS: 20 concurrent identical submissions create one execution/outbox/acceptance; conflict/replay and cancellation ownership; reconnect preserves accepted intent |
| `docker compose up --build -d --wait` | PASS: local stack starts; host API health and Temporal UI each return HTTP 200 |
| `docker compose run --rm demo` and host `go run ./cmd/demo` | PASS: submission/alias replay, 409 conflict, owner/data denial, lifecycle, logs/events, artifact digest/size, running-job cancellation, timeout |
| Manual worker-stop/API-restart sequence in `demo.md` | PASS: accepted intent survived API restart and completed after worker resumed; original acceptance replayed unchanged |
| Recreate containers/network without deleting volumes | PASS: prior API result and completed Temporal history retained |
| `python3 docs/design/tools/validate_design.py`; `git diff --check` | PASS: design regressions/source hashes and whitespace |
| Pinned Redocly 2.51.2 lint; onboarding document link check | PASS: target OpenAPI description and 13 local onboarding links |
| Installed `docker compose version`, `docker compose config --quiet`, `docker compose ps` | PASS: Compose 5.5.1 is now installed and recognizes the running local stack |
| Pinned Go/PostgreSQL/Temporal `docker manifest inspect` checks | PASS: all three image indexes publish Linux amd64 and arm64 variants; no platform override is needed for Intel/Apple-silicon Mac builds |

Integration findings fixed before handoff: initialized the Temporal volume for its non-root UID; used a standard project bridge so host ports publish correctly on this Docker engine; canonicalized stored JSONB responses so first acceptance and replay match. Published ports remain explicitly loopback-only; the bridge is not an outbound-network sandbox.

Recovery sample: `exec_b24f39b4c4b4a3790f000df2e97586a5` was accepted with worker stopped, remained accepted after API restart, then succeeded on worker resume. The earlier `exec_58157bab0d72c7f7c9314aaeb3807bf7` retained its completed Temporal run/history and API results after container recreation. These synthetic IDs are local evidence, not fixtures required on another machine.

Initial verification used checksum-verified temporary Go/Compose binaries because neither command was on PATH. The user subsequently installed Compose; `docker compose version` now reports 5.5.1 and the installed command passes the configuration/status checks above. The work demonstration target is macOS; use the organization's approved Docker Desktop installation and the README's Mac preflight. Native Go remains optional.

No Windows/macOS/ARM machine, real Entra tenant, enterprise Temporal connection, cloud provider, vulnerability scanner, long-running fuzz campaign, or full failure/replay suite was verified by this earlier baseline. Commands containing `docker compose run ... demo` or the old test service apply only to that earlier revision; use the current README commands now.

## Earlier backlog — superseded by the latest acceptance list above

1. **Next environment task:** repeat the proven setup on the Mac in its approved tenant using [work-setup.md](work-setup.md). Connected second-user ownership/revocation and workload-identity evidence remain pending; the successful single-user walkthrough does not cover them.
2. Complete event long-polling with bounded wait, tail-cursor and cancellation tests, then catalog pagination, content negotiation and tracestate. Broader group/classification/app-owner policy and dispatch-time grant rechecks remain incomplete; unsupported inputs are rejected, not silently accepted.
3. Harden lifecycle/recovery: bounded cancellation/dispatch attention, workflow failure reconciliation, independent cleanup/delivery retries, replay histories, controlled crash windows, telemetry/audit, retention and cross-store recovery. Current fake effects complete atomically in PostgreSQL; they do not prove external-effect recovery.
4. Add versioned incremental migrations beyond the initial additive local schema, CI gates, vulnerability scanning and full runtime OpenAPI conformance. The M0 OpenAPI is the **target** contract; the local slice is not fully conformant.
5. Complete required local and connected M1 evidence in the design delivery plan. Then separately approve shared ACA hosting and live provider work. Do not deploy the current local fixture binary.

The demo has no workload failure injection, real image execution, input uploads, secret delivery, cost enforcement or live cloud effects. The published `sbom` is synthetic JSON, not a real SPDX document. Existing ADRs remain Proposed pending individual decision records; this local increment does not silently ratify all M0 proposals.
