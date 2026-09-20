# New machine / new session — start here

**Outcome:** continue from the repository, not chat memory. Start a fresh local core environment on the destination; do not move or resume the existing Azure deployment by accident. At this handoff, the inspected implementation was committed as `3a2b74a`; include subsequent handoff-document changes in the source you transfer. No commit/push is performed by these instructions.

**Adding more Terraform patterns?** The concrete development task is [PAT-01: Add the Terraform pattern interface and registry](#pat-01-add-the-terraform-pattern-interface-and-registry). The machine-setup prompt at the end deliberately stops after setup; use the PAT-01 prompt when you choose to start that refactor.

## What is ready, and how modular is Terraform?

**Reusable foundation, but not yet drop-in resource support.** Keep the API/SQL/Temporal flow; extract the Key Vault-specific parts before adding another real pattern. “Pattern” means a reviewed Terraform configuration, potentially from an existing repository—not an arbitrary resource type or HCL submitted by a caller.

| Reuse | Extraction needed before another pattern |
| --- | --- |
| Entra caller checks, deployment routes and durable receipts | [HTTP adapter](../internal/httpapi/deployment.go) accepts one hard-coded pattern and returns a singleton catalog |
| Atomic acceptance/approval outboxes and plan/apply lifecycle | [Deployment model](../internal/deployment/model.go) hard-codes pattern/queue, vault naming/resource ID and `vault_uri` output; [store](../internal/store/deployment.go) also writes the fixed pattern ID |
| Saved-plan hashing, expiry and current executor binding | [Plan validator](../internal/deployment/plan.go) accepts exactly `azapi_resource.vault` with the approved vault properties |
| Restricted workspace, bounded Terraform subprocess and certificate client | [Runner](../internal/keyvaultrunner/runner.go) embeds the Key Vault bundle, renders fixed variables and performs vault-specific absence/readback checks |
| Separate human and Azure executor authority | Current RG role is Key Vault Contributor, not rights for other resource types; review only the actions/scopes a selected new pattern needs |

The reusable pattern interface/registry is **not implemented yet**. PAT-01 below is the recommended local refactor before adding another real pattern.

Only then choose one actual second pattern and add its focused plan/security/readback tests and narrow RBAC proposal. Existing-repository execution still needs a separately scoped commit/content-pinned bundle path. Hosted managed identity, remote state, multi-resource graphs, update/import/destroy and general partial-apply recovery are not implemented. No new resource type, executor refactor, RBAC change or live apply is authorized merely by this recommendation. Current next authorized coding task remains cancellation/retry/history-budget acceptance in [handoff](handoff.md).

## PAT-01: Add the Terraform pattern interface and registry

**Status:** planned, not implemented. **Purpose:** let the shared deployment flow resolve a reviewed pattern instead of hard-coding Key Vault throughout the application. This is local code/test work; it does not require another Azure resource, registration or role. Start when the engineer explicitly selects this task.

### Implementation steps

1. **Define a small pattern interface at the infrastructure-worker boundary.** It supplies immutable pattern ID/bundle digest/files, target/input validation, variable rendering, exact plan validation, resource ownership/absence/readback checks and sanitized outputs. Keep cloud I/O out of Temporal workflows and portable compute/domain types.
2. **Add a compiled-in registry.** Resolve only explicitly registered patterns and their pinned content binding; reject unknown IDs or digest mismatches. No dynamic plugins, arbitrary HCL, caller source URLs or default-to-Key-Vault fallback. A registered pattern is not automatically authorized for every caller/target.
3. **Make Key Vault the first implementation.** Extract the existing vault naming/resource-ID rules, `ValidatePlan`, variable preparation, ARM absence/verification and output mapping from [model](../internal/deployment/model.go), [plan guard](../internal/deployment/plan.go) and [runner](../internal/keyvaultrunner/runner.go). Reuse the existing [embedded bundle](../patterns/key-vault/pattern.go); keep its Terraform files and digest unchanged.
4. **Wire selection end to end.** The configured catalog, HTTP admission, store and runner must resolve/bind the same pattern ID/digest. Keep API routes, current owner/executor authorization, atomic outboxes, exact saved-plan approval and per-resource mutation ownership. Preserve existing Key Vault records, workflow/activity names/history and `vault_uri` response compatibility; do not silently reinterpret old receipts or plans.
5. **Prove extensibility without Azure.** Register a second synthetic pattern **only in tests**, with different inputs, plan rules and outputs. Exercise the real selection/admission/dispatch boundaries with fixtures. Do not expose that test pattern in normal startup or create a second real resource type in this task.

### Acceptance and stopping point

- Key Vault's existing plan/security/identity/HTTP tests still pass; the extraction does not weaken its exact-create guard.
- Unknown patterns, cross-pattern plans and changed bundle/target/executor bindings fail closed; the test pattern cannot use Key Vault's rules accidentally.
- Old Key Vault receipt JSON, pattern digest, resource ownership and workflow identity remain compatible; retries do not start another apply.
- Run focused regressions, `make check` (or Docker equivalent), and the affected real local PostgreSQL/Temporal suites where admission/outbox/workflow boundaries change. Fixtures are not Azure security evidence.
- Update the lab OpenAPI only if its actual contract changes, plus progress/handoff with results and remaining risks. Stop after the registry works with Key Vault and the test-only pattern. No RBAC, credentials, live plan/apply, state migration, remote repository loading, update/import/destroy or hosted identity work.

**Prompt to start PAT-01 after machine setup:**

> Implement PAT-01 in docs/session-transfer.md: add the Terraform pattern interface and compiled-in registry, preserving Key Vault behavior and using a second synthetic pattern only in tests. Read AGENTS.md, docs/handoff.md and the named code paths. This request selects PAT-01 as the local coding priority; leave unrelated core acceptance work pending. Add regression tests first, make the smallest end-to-end extraction, run the affected local checks, and update progress/handoff. Do not provision Azure resources, change identities/RBAC, run live Terraform, migrate existing state, commit or push. Stop when the listed acceptance criteria are met and report evidence.

## Move source, not credentials or running state

- Use the intended repository/branch/revision on the destination. On the source and destination, compare `git log -1 --oneline` and inspect `git status --short`; uncommitted/untracked changes do not travel with a clone. Include this file and its linked handoff updates through your normal reviewed source-transfer process. Do not use a blanket archive of the working directory.
- Do **not** put `.env`, `config/*.local.json`, `.local/`, Azure CLI caches, private keys, token caches, Terraform state/plans or database backups in Git or chat. Leave the existing vault, restricted state/evidence and Docker volumes intact on this machine.
- Rebuild helpers on the destination; Linux `.local/bin` binaries are not Mac executables. No `/tmp/forgeapi-*` tool/cache path is a portable prerequisite.
- A fresh checkout has **no prior execution/deployment records or Temporal history**. The documented completed-Key-Vault replay requires its original database/configuration. Do not run that replay, `make keyvault-demo`, `make keyvault-worker` or executor bootstrap on a fresh machine to “restore” it. Reusing an old request key against an empty database is not a historical replay.
- Migrating active deployment management would require separately reviewed PostgreSQL + Temporal + Terraform state/evidence reconciliation and identity setup. There is no implemented cross-store migration/restore procedure; copying a plan or DB alone is not enough.

## Destination setup — fresh local core only

1. Use the intended checkout as the working directory. Read [AGENTS](../AGENTS.md), [handoff](handoff.md) and this page. [Progress](progress.md) is historical evidence plus current remaining work; its lab IDs are not destination configuration.
2. On Mac, use approved Docker Desktop with Linux containers/Compose; on Linux, Docker Engine/Compose. Node.js 22+ and a signed-in Azure CLI are needed for identity bootstrap; Make runs the shared commands. Host Go/Terraform are **not needed** for the core Docker demo. Check ports 8080, 54329, 7233, 8233 and browser callback 8400. First builds need approved registry/module access; sign-in/JWKS need Entra connectivity.
3. Confirm the intended tenant with the engineer before any tenant writes. **If the approved API/client registrations already exist in that tenant**, reuse their public IDs and follow [manual local configuration](entra-local.md#local-configuration), generating a new cursor key and local grant for the destination user. Do not run the creation script blindly: its ignored setup journal does not arrive with Git, and it intentionally stops on existing registrations without that journal. Do not share cursor keys/tokens or overwrite existing destination config.
4. **If new registrations are needed and their creation is explicitly approved in the confirmed tenant**, follow [work setup](work-setup.md). That creates only the two core API/client registrations and local configuration—not an executor or Azure resource. Browser/MFA/admin consent steps belong to the human; never disable auth or request their password/token.
5. Leave `config/deployment.local.json` and executor credentials absent for this core-only environment. Deployment operations will be unavailable; synthetic compute remains usable. Do not run the lab executor setup script. The existing certificate's expiry is **2026-09-15 01:18:36 UTC**; it is not a portable work credential or permission to rotate/create one.
6. After local Entra configuration, run:

   ```sh
   sh scripts/verify-local.sh
   ```

   This checks Docker/config, runs unit/race/contract and real local PostgreSQL/Temporal tests, starts/upgrades the stack and opens browser PKCE for the synthetic demo. Browser and helper must be on the same machine. Stop on failures and record them; missing dependencies do not count as skipped passes. Do not use `docker compose down -v` to fix a failure.

Stop setup after **`PASS: local walkthrough complete`** and passing required tests. Record actual destination OS/architecture, source revision, commands and sanitized execution/result evidence in progress. The previous Darwin cross-build is not proof this Mac works; separate databases do not prove two-user isolation. No hosted service, paid infrastructure or Key Vault operation is part of setup.

## Paste into the new session

> Read AGENTS.md, docs/handoff.md and docs/session-transfer.md in this checkout. Help me get a fresh local core environment working on this machine, reusing approved Entra registrations where available; confirm the intended tenant and authority before any identity writes. Use the documented Docker/test/browser-PKCE flow and record actual results. Do not copy credentials/state, provision resources, start the Key Vault worker, replay the previous vault request into an empty database, commit or push. Stop after setup proof and report the next bounded task. The foundation is reusable, but Terraform is still Key Vault-specific: the pattern-registry extraction described in session-transfer.md is a recommendation, not authorization to implement or apply another pattern. Read only relevant files; do not reread the full design package. Use one coordinating session with no delegation unless I explicitly request it.

No chat-history export, runtime LLM framework or model API key is needed. The repository instructions, source, contracts and recorded evidence are the handoff; the receiving session must inspect its own checkout and environment rather than assume this machine's state exists there.
