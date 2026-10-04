# Session handoff: continuous API improvement

**Current as of:** 2026-10-04. All work is merged into `main` (fast-forward) and also on
`platform-2026-10`; the engineer asked for no PRs. Nothing has been published or
deployed. The engineer wants consecutive bounded milestones without
questions; record decisions in progress. The current state is a verified checkpoint: no test
run, emulator or agent is pending.

### Start the next session with this prompt

> Continue ForgeAPI in /home/zerocool/github/forgeapi-homelab. Read first: AGENTS.md,
> docs/session-handoff.md, docs/agent-architecture.md and the newest docs/progress.md entries
> (everything dated 2026-10-03 and 2026-10-04). Find the installed Ponytail skill (last seen at
> ~/.codex/plugins/cache/ponytail/ponytail/4.10.1/skills/ponytail/SKILL.md; rediscover if the version
> moved) and apply it in full. Model routing: Opus 5.5 plans and reviews every diff; Sonnet subagents
> write code with explicit file ownership, are told other agents share the tree, never run
> git stash/checkout/restore/reset or repo-wide `ruff format`, and never run app code outside pytest
> (outside pytest, settings load the real .env and .local/data). Root runs the full suite (always
> with `-rf`), lint, whitespace, reruns every integration result itself, and owns all docs. Disclose
> if routing or skills are unavailable.
> Hard rules: never create, change or delete anything in real cloud from the home lab (real cloud
> happens only after the move to the work environment; the engineer's lab Entra tenant may be used
> read-only for token/Graph verification); Floci emulators are free home-lab
> verification only — no Floci-specific product features; never touch .local/data, retained
> resources, or the stopped forgeapi-floci-test-*/forgeapi-floci-demo-* containers; start emulators
> only with `docker compose -f compose.floci.yaml -p <unique-project> up -d` and remove the project
> afterwards (zero containers/networks). Local Terraform is 1.15.9; the platform image uses 1.16.5
> (copy it out of the local forgeapi image into the scratchpad). Always keep the status page current:
> https://claude.ai/artifact/1CvrDzH8fYX9ZCbTt6FAYj (republish after every milestone).
> State: the work is pushed on branch `platform-2026-10` (see git log);
> default suite 1181 passed, 31 optional skips (Floci + opt-in real Graph). Every feature needs fast tests plus proof through the real stack (HTTP API →
> real Temporal → app.worker.build_worker → Terraform → Floci, with independent emulator readbacks);
> never call a skipped or unrun check passed. Don't ask me questions; make reasonable decisions,
> record them in progress.md, update session-handoff.md, docs/work-deployment.md and
> docs/work-prompt.md (with their "Current as of" lines) in the same change, and keep shipping.
> Next: (1) open reviewable PRs from the branch if I ask; (2) prepare the work-environment move
> (docs/work-deployment.md) — anything provable in the lab first; (3) lab gaps: live Entra group
> membership for access re-checks, Postgres only if multi-replica is wanted. Parked unless I ask:
> four-eyes approvals, notifications/webhooks, OpenTelemetry.

### Latest verified checkpoint

- **Newest (2026-10-04): AKS base rehearsed on kind with `entra` auth as shipped** and real
  lab-tenant tokens (401s, group scoping, deploy/destroy through the pod, JWKS through the 443
  egress rule); **work-day runbook** in `deploy/aks/README.md`. **Next:** the work cluster
  (follow the runbook); optionally publish an image by pushing a `v*` tag (engineer's call).
- **Newest (2026-10-04): merged to `main`** (fast-forward to `bd19198`, at the engineer's request;
  no PRs). First GitHub-hosted Check run passed (run 37240347133: `check`, `image-scan`). Image
  publication still not run.
- **Newest (2026-10-04): real Entra verification from the home lab** (engineer-provided lab
  tenant, signed-in `az`; reads and token issuance only, nothing created in Entra/cloud). Real
  tokens through API → Temporal → worker → Terraform → Floci (401s, group-scoped units/envs,
  deploy/destroy). New opt-in `FORGEAPI_LIVE_GROUP_CHECKS` (Graph `getMemberGroups` before
  background app acceptances), proven on real Graph; a real-stack run found and fixed app
  activities failing on a transient 503. Tenant-specific IDs stay outside the repo (the run dir
  is in the session scratchpad). Real-Graph pytest: set `FORGEAPI_TEST_ENTRA_OID`,
  `FORGEAPI_TEST_ENTRA_MEMBER_GROUP`, `FORGEAPI_TEST_ENTRA_NONMEMBER_GROUP`.
- **Newest (2026-10-04): work-move prep proven in the lab.** Temporal TLS/mTLS in a real
  handshake (`tests/test_temporal_tls_live.py`, default suite) and AKS workload identity through
  API → Temporal → worker → Terraform → Floci Azure on 1.15.9 and 1.16.5
  (`tests/test_floci_workload_identity.py`, `--floci`). Default 1169 passed ×2. Live Entra
  membership re-checks deliberately not built (needs Graph; see progress). **Next:** PRs if the
  engineer asks; otherwise the work cluster (webhook, federated credential, RBAC, Azure Disk,
  real Temporal certs) — work environment only.
- **Newest (2026-10-04): independent three-part review → ~28 fixes; self-service Deploy;
  `deploy/aks` rehearsed end to end on a local kind cluster (`deploy/aks-rehearsal/`).** Default
  1167 passed ×2. **Next:** apply `deploy/aks` on the work AKS cluster (Entra, workload identity,
  Azure Disk, NetworkPolicy) — work environment only; commit the tree in reviewable PRs when the
  engineer asks.
- **Newest (2026-10-03): database-backed teams** (`FORGEAPI_TENANTS_SOURCE=db`), operator admin
  API `/admin/teams…` and portal Teams screens (wizard, editor with impact preview, history,
  import); proven live in the browser including a deploy through a wizard-created team.
- **Newest (2026-10-03): pattern onboarding** — templates (Azure primary), contract checker
  CLI (`python -m app.pattern_check`), `GET /patterns/{name}/check`, portal Contract panel,
  [pattern-onboarding.md](pattern-onboarding.md). Default 1068 passed. Engineer rule: Floci is only
  free home-lab verification; design for real Azure at work.
- **Newest (2026-10-03): flake root cause proven and fixed in the product** (shared plugin cache
  "text file busy" → `TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE=true`; background-launched
  workers ignoring SIGINT → restored at import), budget leak on failed deploys fixed, cost history,
  pattern changelogs, `cloud_not_available` 422 and deployable patterns. Concurrent full suites:
  999 passed ×4. The earlier "open" flake items are resolved.
- **Newest (2026-10-03): fleet management** — drift checks (+ optional sweep), upgrade
  detection, `POST /resources/{id}/upgrade`, `POST /resources/{id}/promote`, portal Fleet view
  (missing vs changed). Default 960 passed. Open: one unreproduced cross-session flake and two
  unreproduced demo check failures (see progress).
- **Newest (2026-10-03): app failover (`POST /apps/{id}/failover`) and ordered, gated teardown
  (`POST /apps/{id}/destroy`, `AppTeardownWorkflow`)**, portal controls, proven on Floci on both
  Terraform versions and live in the demo. Default 910 passed. Floci bug noted: UpdateHealthCheck
  drops Type/RequestInterval.
- **Newest (2026-10-03): `POST /apps` one-request HA rollouts** (Temporal `AppRolloutWorkflow`,
  two exact-digest gates, portal stepper and gate approval), proven on Floci on both Terraform
  versions and live in the demo. Default 866 passed. **Next candidates:** app-level teardown
  (`DELETE`-style destroy of all members in safe order), live health probes per replica,
  app-level failover endpoint. No real cloud from the home lab.
- **Newest (2026-10-03): multi-cloud HA app recipe** ([ha-apps.md](ha-apps.md)): replica
  patterns per cloud + Route 53 failover router via `input_refs`, portal Apps view, failover
  approved from the portal in a live Floci demo. **Next (phase 2):** `POST /apps` fan-out with
  one approval gate (Temporal; in progress), health status in the API. **Engineer rule
  (2026-10-03): never do anything in real cloud from the home lab**; real-cloud runs happen only
  after transfer to the work environment.
- **Newest (2026-10-03): developer portal at `GET /console`** (Home, Infrastructure by project,
  Landing zones, Jobs, History, Catalog, resource pages with managed objects) plus resource
  fields `cloud`, `region`, `estimated_monthly_cost`, `owned_by_caller`, `created_at`,
  `managed_objects` and discovery `clouds`. Proven live on Floci AWS/Azure/GCP with a two-unit
  tenant mapping, including approving a prod plan from the portal. Default 835 passed.
- **Newest (2026-10-03): built-in web console `GET /console` (capability `web_console`),
  proven live: seeded real operations, rendered in headless Chromium (dark/light/400 px), and a
  plan applied through the console reached `succeeded` with the file written.** Default 829
  passed, 24 Floci skips. **Project status page (engineer wants one, refreshed every milestone):**
  https://claude.ai/artifact/1CvrDzH8fYX9ZCbTt6FAYj
- **Newest (2026-10-03): drift flake cause proven (per-test provider downloads; a run failed
  with a `releases.hashicorp.com` TLS timeout) and removed with a persistent test provider mirror
  (`TF_CLI_CONFIG_FILE`; full suite passes with a dead proxy); Floci fixtures destroy leftovers
  on failure; input_refs consumer apply on AWS, Azure (container; Floci 501s blob properties) and
  GCP; new `failure_diagnostics` (`diagnostic` on failed/uncertain operations).** Default 820
  passed, 24 Floci skips; Floci 7 + 5 + 12 passed on Terraform 1.15.9 and 1.16.5. Emulators
  removed. See the newest progress entry, including a no-damage incident with `.local/data`.
  **Rule added:** coders never run app code outside pytest (`.env` holds real lab settings).
  **Next:** hosted CI/AKS when the engineer pushes; candidate gaps: operator-facing
  reconcile guidance using `diagnostic`, CI reuse of the provider mirror.
- **Newest (2026-10-02): placed-mode features proven on Floci AWS, Azure and GCP through real
  Temporal** (`tests/test_floci_placed_features.py`, 11 passed on Terraform 1.15.9 and 1.16.5).
  Default suite 807 passed, 22 optional Floci skips, 156.83 s. Emulators removed. **Next:**
  find the intermittent drift-test failure; add `try/finally` emulator cleanup to the Floci
  suites; consumer apply on Azure/GCP; hosted CI/AKS when the engineer pushes.
- **Before that: today's features proven through real Temporal + worker + Terraform +
  Floci AWS** (`tests/test_floci_features.py`, `--floci`): 4 passed on Terraform 1.15.9 and
  1.16.5; existing `tests/test_floci.py` 7 passed on both. Emulators removed afterwards. Default
  suite 807 passed, 11 skips (four new Floci tests skip without `--floci`). **Open: one
  intermittent drift-test failure (2 of 10 full runs), cause unconfirmed; see progress.**
- **Earlier: independent Opus review of today's features; its three findings fixed.
  Full suite 807 passed, seven Floci skips, 163.06 s; Ruff and whitespace passed.** Before it:
  opt-in lazy plan expiry, output references and destroy protection. See the newest
  [progress](progress.md) entry. Earlier today, in order: catalog listing/filters/guardrail
  discovery/disk cleanup/version upgrades (737), budget discovery + client (751), labels (776).
  **Next candidates:** hosted CI/AKS when the engineer pushes; a real AKS apply of `deploy/aks/` (not authorized yet).
- **Earlier today: budget discovery and client/CLI pattern listing + inventory filters
  complete. Full suite 751 passed, seven Floci skips, 129.01 s; Ruff and whitespace passed.**
  See the newest [progress](progress.md) entry.
- **Previous (2026-10-02): catalog listing, inventory filters, guardrail discovery, plan-file
  cleanup and pattern version upgrades complete. Full suite 737 passed, seven Floci skips,
  127.67 s; Ruff and whitespace passed.** Details, decisions and limits in the newest
  [progress](progress.md) entry. Counts below (716) are an older checkpoint.
- **Model routing changed (engineer instruction, 2026-10-02):** Opus 5.5 plans and reviews,
  Sonnet 5 implements; `AGENTS.md` and the prompts below say so. Earlier entries naming Astra
  and Sol are history. Ponytail is cached at version 4.10.1.
- **No wedged resources milestone complete:** plan discard endpoint, `init`-only provider-cache
  lock, Terraform phase deadlines and stale-plan/lock refusals recorded as `failed`. **Full
  suite after all milestones today: 716 passed, seven optional Floci skips, 123.07 s**; Ruff and whitespace passed. See the
  newest [progress](progress.md) entry for evidence, limits and the ranked review findings.
  **Security hardening also complete:** `FORGEAPI_*` is stripped from Terraform/git children, the
  git token goes only to `init`, and `auth_mode=none` refuses non-loopback callers unless
  `FORGEAPI_ALLOW_UNAUTHENTICATED_REMOTE=true` (lab manifests set it). Work target per the
  engineer: Temporal on AKS, API host free to choose. **Also complete:** resource inventory (`GET /resources`, `GET /resources/{id}`), operator
  reconciliation (`POST /operations/{id}/reconcile`, mapping `operators` groups), plan
  `change_summary` and error `reason` values. The engineer wants decisions made, not asked.
  Readiness (`/readyz`) and full OpenAPI metadata are also complete. **The older-image upgrade
  probe below has now run and passed** (see progress). Also complete: opt-in Temporal TLS,
  AKS workload identity, `deploy/aks/` (rendered, never applied), operation filters, long-poll
  and request IDs. Also complete: drift visibility,
  hardened image (digest pins, no Temporal binary in production, Trivy gate) and Terraform
  1.16.5. Floci passed 7/7 on 1.16.5. Also complete: attribute-level
  plan detail and plan guardrails. **Paused for a machine restart at a verified checkpoint; no
  agent, test, container or probe is running.** **Next (superseded, done):** inventory filters and `GET /patterns`,
  disk cleanup, guardrail discovery; hosted CI/AKS waits for the engineer to push. The engineer
  wants continuous feature work without questions. Dropping legacy workflows from the worker needs an
  engineer decision. The upgrade probe below remains unstarted. Older counts in this file
  (502) describe the previous checkpoint.

- **README guide complete:** five native Mermaid diagrams explain the architecture, plan/apply
  sequence, operation states, durable storage and placement. The guide includes runnable local
  steps, endpoint/configuration/recovery tables and version compatibility. All diagrams were
  parsed/rendered and visually checked; README references, shell syntax and example intent
  validated; three documentation tests passed. `#http-client` remains stable. Render previews
  are gitignored under `.local/verification/readme-20261002/`. No runtime changes were made.
- **Full suite: 502 passed, seven optional Floci skips, two existing dependency warnings,
  87.23 s.** Final Astra reviews, repository Ruff and whitespace passed. Documentation checks
  and relative links were also verified. All source changes remain uncommitted/unreleased.
- Completed: operation API v1 compatibility, thin client/CLI, stable operation/event pagination,
  acceptance/dispatch and worker-interruption proofs, current replay fixtures, quiesced
  backup/restore, release checks, and boundary hardening. See [progress](progress.md) for detail.
- Latest runtime fixes: `app/catalog.py` handles missing/unexecutable Git and replaces invalid
  subprocess bytes before version filtering, preserving pins and sanitized diagnostics.
  `app/ledger.py` catches directory-creation OSError into fixed 503; unavailable refusal audit
  prevents acceptance/dispatch. Real filesystem repair permits same-key recovery.
- Latest client fix: `app/client.py` rejects duplicate IDs, returned anchors and inconsistent
  operation-page continuations. Valid older pages omitting next_before and additive fields
  remain supported. These changes and real HTTP read-contention/read-failure tests are covered
  by the 502-test result. Sol implemented; Astra planned/reviewed; root ran verification/docs.
- **Current packaged-image smoke passed:** image `forgeapi:local-check-20261002-502`, ID
  `sha256:2ff6b29460288abe49204d439ebae9656e10a754f5e39b1084d6b2f2bb8581d9`.
  Real isolated API/Temporal engine/local-file Terraform lifecycle through HTTP; root/v1
  identical keys and execution digests retain one operation/resource, one accepted event per
  phase and one plan/apply receipt. Actual file length/hash matched expected content.
  All owned containers, volume and network were removed and absence verified. Image retained.
- Smoke evidence and reviewed temporary harness are preserved in the gitignored directory
  `.local/verification/image-smoke-20261002-502/` (`evidence.json`, `smoke.py`), as well as
  their original `/tmp/forgeapi-current-image-smoke*` paths. The artifact directory is useful
  on this workspace but is not part of a fresh clone. Full image/operation/tool details are
  recorded in progress. The initial sandbox could not access Docker through Python; the
  approved rerun passed. Never use ordinary compose for this probe: it mounts retained state.
- **Output evidence correction:** real Terraform `1e400` is an exact 401-digit integer, not
  infinity. One explicitly injected overflowing output response after real apply/output proves
  the defensive serialization/uncertainty guard. Do not claim normal Terraform reproduced it.
- Storage consumption and read-only current-client checks against the older hosted image are
  already complete. Do not repeat cleanup or mutate retained cloud/emulator examples.

### Upgrade probe — completed 2026-10-02 (kept for its scope; do not repeat)

Verify an actual **older packaged Temporal runtime → current image** upgrade in a fresh,
disposable local environment. The current smoke is a fresh install, not an upgrade proof.
A locally available older Temporal image is `forgeapi:temporal-agent`, ID
`sha256:7ba180975fc9c22212e3d161f5750d4aea05bf107b1dcc13ca0931ef7ab38fc8`.
Historical progress records it as a refreshed Temporal image; inspect its contract, tool
versions and workflow source read-only before selecting it as the upgrade baseline. Do not substitute the
pre-Temporal `agent-redesign` image or claim it is the same image as the hosted older server.

Have Astra plan the probe after that inspection. If suitable, create a local-file pending plan
with the older image on a fresh owned volume; stop its API/engine and all writers; then use the
current image with the same paths/volume/toolchain. Verify original key/operation/resource/digest,
retained history, no replan, and exactly one apply. Reuse the isolated smoke approach and preserve
only safe evidence; never mount retained `.local/data`, credentials or live deployment volumes.
This experiment has **not run**. Do not add migrations/adapters or infer downgrade/mixed-worker
support from it. If the baseline cannot support a valid probe, document the reason.

After local evidence, meaningful remaining release gates are hosted CI on the intended revision,
publication of an immutable artifact and controlled hosted upgrade/consumer verification.
No commit, push, publication or deployment is authorized merely by these queued steps; obey
AGENTS.md and preserve the shared dirty/untracked tree. Work-cloud identity remains unverified.

### Verification and limits

Run `uv run pytest -q --tb=line` for the full suite and `uv run ruff check .` for lint.
This environment needs the existing approved pytest escalation for loopback/Temporal tests;
sandboxed TestClient runs may hang. Root runs these tests; coding agents should freeze edits
before verification. `tests/test_docs.py` and `git diff --check` check documentation/whitespace.
The two current warnings are dependency deprecations (Starlette/httpx and AnyIO BlockingPortal).
Optional Floci tests are not included in the normal full-suite result.

The [versioning policy](api-versioning.md) covers **the new operation API only**. `/v1` is
canonical and root aliases remain v1. Read-only current-client checks against the retained older
hosted image passed; this does not establish arbitrary downgrade or old-worker replay support.
GitHub-hosted execution of the new release workflow and work-cloud identity remain unverified.
Keep live resources, `.local/data/`, private placement/state and saved plans intact. No automatic
apply retry, replan, force-unlock or uncertainty recovery is authorized. Detailed evidence and
historical milestone counts belong in [progress](progress.md), not this checkpoint summary.

**Quiesced single-host backup/restore is verified locally.**
`tests/test_backup_restore.py` restores an offline copy of the complete temporary ledger,
Temporal database, Terraform state and saved plans at the original paths. A fresh server and
worker retain operation/workflow identities and execute a pending digest once without
replanning. This uses the same configuration/toolchain with no post-snapshot changes; it does
not prove recovery from a stale snapshot, live backup or restoration of cloud resources.

**Controlled worker interruption is also verified locally.**
Two real Terraform/Temporal tests cover worker SIGKILL during apply and a surviving worker's
late completion. Both preserve uncertainty, the resource reservation and one apply attempt.
A test-only interceptor shortens the asserted production 30-minute timeout to five seconds;
this is not a full-duration or hosted-cloud interruption test. See the current progress entry.
An API SIGKILL after acceptance and before dispatch is also verified: a restart preserves
the reservation, and the caller's identical retry recovers the same operation and plans once.

**A thin agent HTTP client is also implemented locally.**
`app/client.py` exposes importable `Client` and `python -m app.client` commands for discovery,
pattern description, validation, submission, operation listing/status, exact-plan execution and events. The caller
retains its body/key and supplies a reviewed digest; no command autonomously applies or retries
a mutation. README contains examples. Local HTTP tests and limitations are recorded in progress.

**Operation API v1 compatibility is also implemented locally.**
Read [the compatibility policy](api-versioning.md) before changing request fields, response
contracts or persistent execution behavior. `/v1` is canonical; root aliases remain bound to
v1 and share the original idempotency scope and fingerprints. Discovery advertises capabilities
and the application release separately from the API major and pattern versions. The guarantee
covers the new operation API only. No image was published or deployed for this milestone.

**Storage consumption is also complete.** All three hosted emulators
passed create → object upload → exact byte/length/SHA-256 readback → object delete/404 →
API-planned infrastructure destroy/404. Retained resources remain intact. Safe evidence is
`.local/pve-placement/evidence/consumption-20260930.json`; operation IDs and checks are in
[progress](progress.md#2026-10-01--hosted-storage-consumption-complete). The acceptance sequence below is retained as
the completed milestone's contract, **not an instruction to repeat it in a fresh session**.
Read this with [the architecture](agent-architecture.md), [progress](progress.md), and
[the placement demo guide](../deploy/emulator-placement/README.md). Work deployment has a
separate [brief](work-deployment.md).

## Start here in a new session

1. Read `AGENTS.md`, this handoff, `docs/agent-architecture.md`, `docs/progress.md`, and the installed Ponytail skill. Discover the current Ponytail plugin path if its cached version has changed; apply Ponytail in full throughout the session.
2. Use Claude Opus 5.5 to plan the bounded milestone and review its final diff; use Claude Sonnet 5 for implementation. Give any coding agent explicit file ownership and tell it to preserve other edits. If model routing or Ponytail is unavailable, say so plainly and continue with the available model rather than claiming it ran.
3. Inspect `git status`, current API health, operations, and the remote deployment read-only before mutating anything. At the handoff, branch `main` HEAD is `1e9cdfd3faa9edbc0c295a444498f6c991cb5e04`; **all 2026-09-30 redesign changes are dirty or untracked**. A fresh checkout of HEAD lacks this work. Do not reset, clean, overwrite, commit, or push it without the engineer's instruction.
4. Storage consumption, local API versioning, the thin HTTP client, interruption/dispatch-crash verification, current planning-history replay and quiesced backup/restore are complete; do not automatically rerun consumption or repeat cleanup. Use current progress to choose the next authorized bounded milestone. No real-cloud credentials or resources were needed for these proofs.

Resume prompt for another model:

> Read `AGENTS.md`, `docs/session-handoff.md`, `docs/agent-architecture.md`, and the newest entries in `docs/progress.md`. Apply the installed Ponytail skill in full. Preserve the entire dirty/untracked working tree and all retained state/resources; do not commit, push, deploy or repeat completed storage cleanup. Resume the current checkpoint above, verifying any explicitly unfinished work before starting one bounded next milestone. Use Opus 5.5 for planning/review and Sonnet 5 for implementation when routing is available, and disclose unavailable routing. Keep working through verified milestones without routine approval. Update progress, this handoff, and both work deployment documents as behavior changes. Report actual evidence and limits; never call unrun checks passed.

Archived prompt used to implement the completed milestone:

> Continue ForgeAPI from `docs/session-handoff.md`. Read `AGENTS.md`, `docs/agent-architecture.md`, `docs/progress.md`, and the installed Ponytail skill first. Apply Ponytail full. Use Claude Opus 5.5 for planning and final code review and Claude Sonnet 5 for coding, with scoped ownership and no reverting other edits; disclose if that routing is unavailable. Preserve all dirty/untracked redesign work, retained demos, and `.local/data/`. Build the next milestone only: use the hosted placement API at desktop loopback port 38000 to create new Azure/AWS/GCP storage, independently upload/read/compare/delete object bytes, and plan/destroy only those new resources through the API. Record safe evidence and operation IDs even on partial failure. Do not use real cloud credentials, create a new controller/MCP service, or touch existing retained resources. Follow the acceptance and stop conditions in the handoff.

## Current architecture and safety contract

The active path is **desktop HTTP client → FastAPI (`app.main:app`) → SQLite operation ledger and Temporal phase workflows → Python worker activities → saved Terraform plan → Floci Azure/AWS/GCP**. The desktop does not run Terraform. There is no server-side model, MCP server, GitHub Actions controller, or custom polling controller. `app.legacy:app` retains the previous API for existing state; old guides are under `docs/archive-temporal/`. `docs/rewrite-plan.md` and `docs/archive-go/` are historical. Do not revive their next tasks or turn this into home-lab infrastructure provisioning.

In particular, the former custom-controller implementation and its tests in older progress entries were superseded when the engineer selected Temporal. Older entries also call distributed ledger/plan storage the next milestone; that is a conditional hosting requirement. **Storage consumption, the thin HTTP client and controlled interruption verification are complete.** Recovery from uncertain outcomes still requires operator reconciliation; no recovery mutation endpoint exists.

`POST /v1/operations` (also `/operations`) accepts an intent keyed by `Idempotency-Key` in one ledger transaction with its resource reservation and `accepted` audit event. It dispatches a plan workflow, whose activity stores exact binary plan bytes and a SHA-256 digest. The caller inspects safe changes and explicitly executes that digest. Separate Temporal workflows run planning and applying; I/O stays in activities. Terraform activities have one attempt. A missing/changed plan fails before apply; an outcome after execution starts may be `uncertain` and reserves the resource until an operator reconciles it. Never automatically replan, reapply, retry an apply, force-unlock, or start replacement work for an uncertain outcome. `503 dispatch_unconfirmed` means acceptance may already be durable: retry the **same** key/body or operation/digest, and preserve the returned operation ID.

Bootstrap a client supporting older servers through `/agent`, follow advertised links and
gate optional features on capabilities. Missing capability metadata means baseline behavior;
unknown response fields can be ignored, but unknown states/actions must stop mutation.
Requests stay strict. `/v1/openapi.json` and `tests/fixtures/v1/` establish the canonical
contract. `tests/test_version_upgrade.py` and `tests/fixtures/upgrade/` preserve historical
request/ledger evidence. Coordinate API/worker upgrades on the single host and preserve the
Terraform/provider toolchain for pending plans; this is not an arbitrary downgrade guarantee.
For operation pages, require discovery capability `stable_operation_pagination` before using
`before=<next_before>`. Visible anchors avoid repeats from new arrivals; states remain live.
Client operation/event commands fetch one explicit page. Event continuation uses `--after`
and `--limit`, verifies identity/sequence and permits new audit action/outcome strings.
Offset clients remain valid. Do not combine `before` with nonzero `offset`. Operation `offset`
and event `after` accept 0 through 9223372036854775807; larger values return structured 422.
`tests/test_workflow_replay.py` uses captured plan, successful apply and controlled activity-
failure histories with normal timeouts/retries as future-change baselines. The failed activity
is test-only; the workflow and uncertainty writer are real. These do not establish replay of a
released older worker or actual timeout history.

Stable workflow IDs are `forgeapi-{operation_id}-plan` and `forgeapi-{operation_id}-apply`. Plan activity timeout is 10 minutes; apply timeout is 30 minutes. A timeout does **not** kill a Terraform subprocess. Ledger state changes fence late completions so they cannot clear `uncertain`. There is no outbox: an API crash between the acceptance transaction and dispatch needs the caller's same-request retry. The real Temporal integration proves worker replacement, queued acceptance and duplicate suppression. `tests/test_worker_interruption.py` additionally kills a real worker during a gated apply, replaces it, observes an accelerated start-to-close timeout and verifies uncertainty with exactly one plan/apply. Its separate surviving-worker case verifies a real late success write cannot clear uncertainty or append a success audit event. Continued local-exec after worker death does not establish successful Terraform state commit. Production workflow code and retry settings are unchanged.

Tenant placement fixes cloud target and region privately, injects required inputs, and prevents caller overrides. A changed target blocks execute/update/destroy. The only caller input for the placed storage fixtures is `name`. API outputs are safe: sensitive values and target IDs are withheld; no object payload or credential belongs in logs or evidence. The current API/worker share protected local SQLite, Temporal history, Terraform state and plans on one host. This is an unreleased single-host simulation, not a distributed Container Apps deployment. Agents integrate through HTTP/OpenAPI.

Budget admissions reserve the larger of previous and requested estimated cost while operations are pending; SQLite serializes admission. Legacy committed costs are read from the older database **without migrating it**. Audit events are append-only in the ledger, with database triggers rejecting event update/delete; mutating refusals record `refused` without submitted secrets. Cold catalog checkouts use a per-commit file lock and `.forgeapi-ready` completion marker, so parallel requests never consume an incomplete repository cache. These details matter when modifying acceptance or pattern discovery.

## Redesign inventory and file map

| Area | Current behavior and primary files |
| --- | --- |
| HTTP contract | `app/main.py`, `app/contracts.py`, `app/policy.py`: discovery, pattern description, validation, operations, execute, list/read/events; strict intents, safe public operations, tenancy checks. `tests/test_operations.py`, `tests/test_operation_policy.py`, `tests/test_api.py` cover lifecycle and policy. |
| Agent HTTP client | `app/client.py`, `tests/test_client.py`: JSON commands and an importable client; discovery/capabilities, explicit keys/digests, same-origin transport and safe failure handling. No client state database, polling loop or autonomous execution. |
| Durable acceptance | `app/ledger.py`, `app/dispatch.py`: SQLite operation/resource ledger, scoped idempotency, atomic acceptance/audit, resource reservation, stable phase workflow IDs, dispatch ambiguity. `tests/test_operations.py`, `tests/test_audit.py` cover these rules. |
| Execution and recovery | `app/operation_workflow.py`, `app/operation_activities.py`, `app/worker.py`, `app/terraform.py`: deterministic Temporal phase workflows, one-attempt activities, exact saved-plan digest, protected state/receipts, conservative uncertainty. `tests/test_temporal_operations.py` exercises the real Temporal boundary. |
| Controlled interruption | `tests/test_worker_interruption.py`, `tests/interruption_worker.py`, `examples/interrupted-apply/`: real worker SIGKILL and late completion during gated local Terraform, with a test-only accelerated timeout, command counts and audit fencing. |
| Acceptance/dispatch crash | `tests/test_dispatch_crash.py`, `tests/dispatch_crash_api.py`: actual API SIGKILL after acceptance, durable reservation across restart and identical-request recovery to one completed plan. No apply. |
| Workflow replay baseline | `tests/test_workflow_replay.py`, `tests/fixtures/recovery/`: captured plan, successful apply and controlled activity-failure histories with normal timeout/retry settings. Diagnostic normalization is documented. No pre-versioning or actual-timeout history claim. |
| Quiesced backup/restore | `tests/test_backup_restore.py`: whole temporary data-tree copy with all writers stopped, same-path restore, retained ledger/audit/workflow identities and exact pending-plan execution once. No post-snapshot changes, cloud restore or cross-version guarantee. |
| Placement and secrets | `app/cloud_targets.py`, `app/tenants.py`, `app/catalog.py`, `app/policy.py`, `docs/tenancy.md`, `docs/outputs.md`: cloud catalog selector, private BU/environment target and fixed region, platform-injected variables, account/project guards, hidden target outputs. `tests/test_cloud_targets.py`, `tests/test_tenancy.py` cover refusals and isolation. |
| Packaging and fixtures | `Dockerfile`, `deploy/engine/Dockerfile`, `compose.yaml`, `compose.floci.yaml`, `patterns.yaml`, `examples/floci-*`, `deploy/emulator/`, `deploy/emulator-placement/`: API/worker packaging, optional local Floci stack, tagged test patterns, hosted k3s demo and independent HTTP verifier. `tests/test_floci.py` uses Terraform and three pinned emulators. |
| API compatibility | `docs/api-versioning.md`, `tests/test_versioning.py`, `tests/test_version_upgrade.py`, `tests/fixtures/`: root/v1 parity, discovery capabilities, canonical schema, old request fingerprints and saved-plan upgrade checks. |
| Stable operation pagination | `app/main.py`, `app/ledger.py`, `app/contracts.py`, `tests/test_operation_pagination.py`: visible operation anchors, optional `next_before`, capability discovery and preserved offset mode. No schema migration; the client consumes the capability-aware pages. |
| Release checks | `.github/workflows/check.yml`, `.github/workflows/image.yml`, `tests/test_release_workflow.py`: read-only locked/frozen checks gate image publication; package-write permission is confined to publishing. Locally verified; no hosted Actions run yet. |
| Legacy state | `app/legacy.py`, `docs/archive-temporal/`: previous deployment API and workflow types for old state, outside the new compatibility guarantee. New IDs use `op_` and `res_`; legacy IDs use `dep_`. No automatic migration. |

The redesign explicitly permits the operation ledger, idempotent intents, exact-plan execution and optional Floci tests. Keep every other parked feature out of this milestone: no general retry engine, cancellation, outbox, authz expansion, OTel, Postgres or new Docker architecture. Keep Terraform patterns in external root-module repos; `examples/` contains local test modules only. The home-lab work here does not authorize new real Azure pattern types, paid resources, AWS/GCP cloud roles, or a production identity design.

## Running simulations and protected state

The placement simulation runs in namespace `forgeapi-placement` on `k3s-server-01` (`10.0.20.10`, VM 200) hosted by `pve-desktop` (`10.0.10.13`). It uses one pinned pod, image `forgeapi:floci-placement` ID `d49f9197c9dde522993824549fa25737b7aae2346c05ff0c6a5dff0dca78a40a`, and host data `/var/lib/forgeapi-placement`. The 2026-10-02 runtime reports API/engine image ID `sha256:2b946bce45583e0e3b128d94436e67df80711f2cb01e5366098820e8e2150ad6`; the earlier ID above is the recorded build identifier. The last observed state was 5/5 containers running with zero restarts; **recheck it in the next session**. The desktop uses an SSH plus k3s port-forward to loopback API **38000**, Temporal UI **38233**, Floci AWS **34566**, Azure **34577**, and GCP **34588**. Exact tunnel and rollout commands are in `deploy/emulator-placement/README.md`. It is an unauthenticated loopback demo behind SSH, with no public/LAN Service or Ingress. Do not expose it to the network.

The original remote `forgeapi-emulator` namespace and API **28000** remain separate and intact. The earlier desktop Azure demo at **18000** and its `.local/floci-demo/` state remain intact. `.local/data/` still contains state for an older live Azure vault: **never delete it**. No PostgreSQL VM was created; that detour ended before any VM, database or credential change. Do not delete or migrate any of these resources while proving the next milestone.

The original remote demo uses host data `/var/lib/forgeapi-emulator`, API **28000**, Temporal UI **28233**, and its own emulator ports; see `deploy/emulator/README.md`. Its retained examples are `forge-work-azure-7fd9cf2134` (`op_171da5938157439ca5e53eef16607c3f`), `forge-work-aws-41b5e92b4f` (`op_20d7d0eab062438a879da2a0f7f62d17`), and `forge-work-gcp-05821f6b59` (`op_0642cbcfe0c64899822fc19e1aa4518a`); IDs and last readback are in `.local/pve-floci/evidence/retained.json`. The desktop Azure demo uses `.local/floci-demo/data`, API **18000**, Temporal UI **18233**, emulator **4577**, and retained group `rg-forgeapi-agent-demo`, restored by `op_25efe6c357d2464a904b5da328490db6` on `res_59dec822d8b1423584bab54cf9e08d4f`. These are also excluded from next-milestone cleanup.

Retained placement examples are live demonstration resources, **not fixtures to overwrite or clean up**. The local evidence is `.local/pve-placement/evidence/retained.json` (gitignored); do not print or commit secret-bearing state. Azure also has the private `data` container.

| Cloud | Retained storage name | Create operation | Resource ID |
| --- | --- | --- | --- |
| Azure | `forgeazure97e662fb4de5` | `op_97b86d4dad2e44beb5e7f576b38adac1` | `res_2678f96de5b7409e8aa5a6e6ef2e68cd` |
| AWS | `forgeawsb27ca40e14bf` | `op_51f6f38e75664557a94bf8a65f68f1f0` | `res_3abfb4c9321340dc8b3187ced048b260` |
| GCP | `forgegcpff8eea71a68b` | `op_37cb74de7fa04d0fa3991806007476aa` | `res_3cc54c18749c426cbec8d001a9ba4676` |

## What has been proved

- Release gate: local locked dependency sync, workflow regression, repository Ruff and whitespace checks passed. Final Astra review passed. Actions are pinned and publishing depends on unconditional read-only checks; GitHub-hosted execution remains unverified.
- Quiesced backup/restore: the focused test passed with real persistent Temporal and local-file Terraform. Whole-tree hashes/symlink targets, explicit state and plan files, complete workflow event hashes/run IDs/results and ledger/audit survive a same-path restore. The original pending digest applies once; only apply/output run after restore. Same configuration/toolchain, no post-snapshot changes and local fixture only.
- Latest combined verification: **414 passed, 7 skipped** (optional local Floci) in **80.49 s**; repository Ruff and whitespace checks passed. Includes catalog Git deadlines and annotated-tag filtering, real Temporal alias concurrency, strict capability negotiation, placement-safe plan summaries, real client transport, refusal audit responsiveness, authenticated-principal validation, configured and historical budget guards, finite-number validation, client operation/event pages, request-boundary proof, pinned release-workflow/tag checks, bounded stable pagination, all three replay histories, quiesced backup/restore, interruption and dispatch-crash recovery. Final Astra code review found no remaining issues. These changes remain local and unreleased.
- Stable pagination: **36 focused tests passed** across pagination, versioning, operations and policy. Review confirmed visible anchors and preserved offset/alias behavior. OpenAPI changed only by optional `before` and optional nullable `next_before`; no existing required fields/defaults/routes changed.
- Acceptance/dispatch crash: **1 focused test passed in 1.98 s**. Real API SIGKILL, SQLite acceptance/reservation across restart, absence of dispatch until caller retry, same operation/resource identities, one completed plan and changed-body refusal are verified locally. No apply or hosted crash was exercised.
- Workflow replay: **3 focused tests passed**, covering captured plan, successful apply and controlled activity failure (11/11/17 events). Normal timeout/retry settings, activity order/outcomes and completed histories are checked. Failure uses a test-only activity and real uncertainty writer. Current baselines only; no pre-versioning or actual-timeout history claim. Ruff and final Astra review passed.
- Controlled interruption: both focused tests passed in **21.41 s**. Real worker SIGKILL, replacement, Temporal timeout history, retained uncertainty, rejected replacement work and late-result/audit fencing are covered. The five-second test timeout asserts the unchanged production 30-minute setting. This is local process evidence, not hosted-cloud or successful post-kill state-commit evidence.
- Thin HTTP client: full suite **205 passed, 7 skipped** (optional local Floci). A real loopback TCP server and real local-file Terraform plan/apply prove explicit execution, duplicate request behavior, digest refusal and file readback; the focused client test uses a recording dispatcher. Transport/compatibility regressions cover ambiguity and safe stops. This client has not been used for a new hosted rollout; see [progress](progress.md#2026-10-01--thin-agent-http-client).
- Local v1 compatibility: full suite **191 passed, 7 skipped** (optional local Floci), plus **2 passed** for the final historical database fixture-loading correction; Ruff and whitespace checks passed. Coverage includes cross-alias idempotency, canonical OpenAPI, discovery/client fixtures and an old planned row executing a real temporary plan once without replanning. No pre-upgrade Temporal history fixture was captured; workflow code is unchanged. This versioning work has **not been deployed**; see the current [progress entry](progress.md#2026-10-01--operation-api-v1-compatibility).
- Before the final Azure Storage expansion, the full suite reported **167 passed** in 173.27 s. Focused final checks: Azure Storage/container lifecycle **1 passed** in 95.43 s; wrong AWS executor account **1 passed** in 7.42 s; placement and docs **12 passed** in 2.87 s; emulator router **4 passed** in 2.06 s. These are recorded runs, not a claim that a fresh full suite passed after every last change.
- Remote desktop → hosted API/Temporal/Terraform → Floci lifecycle through the SSH tunnel passed for Azure, AWS and GCP. Each had absence before apply, presence after apply, and absence after destroy, plus override refusal, duplicate-call behavior, exact-plan execution, audit ordering and hidden placement outputs. Azure readback checked both account and private container. Evidence: `.local/pve-placement/evidence/lifecycle.json`; see operation IDs in `docs/progress.md`.
- Three **retained** placement resources were created and independently read back as present. Evidence: `.local/pve-placement/evidence/retained.json`. These are preserved for inspection, not for the next destructive test.
- The known tested matrix pins Floci AWS **2.1.0** with Terraform AWS provider **6.14.1**, Floci Azure **0.13.0** with AzureRM provider **4.65.0**, and Floci GCP **0.9.0** with Google provider **7.36.0**. Emulator images are pinned by digest in `compose.floci.yaml` and the deployment manifests; inspect those files before upgrades. `uv` runs Python/tests, while Terraform, git, the Temporal test binary, and optional Docker/Floci are needed for the corresponding integration tests. The hosted client verifier itself uses Python standard library and needs only the tunnel.
- The Floci-AZ ARM container URL falsely returned success for a nonexistent container, so the fixture verifies the supported blob data path. Floci emitted normal Azure Storage hostnames, so an emulator-only loopback router in `deploy/emulator/storage_proxy.py` forwards only matching Storage hosts and rejects unrelated destinations and CONNECT. This is not a general proxy or real-cloud routing design.

The earlier results below predate object consumption; the completion evidence at the top now
proves byte upload/read/delete on all three hosted emulators. Cloud storage feature parity,
real Azure/AWS/GCP authorization, cloud workload identities, billing, regional limits,
work-Mac verification and production hosting remain unproven. Emulator cloud state can
disappear on restart even when the ForgeAPI ledger/state survives; confirm live readback
before relying on any older `succeeded` record. Floci-AZ 0.13.0 already lost an earlier ARM
resource group across restart despite WAL. Placement status is a dated observation.

## Completed milestone contract: consume newly provisioned storage

**Goal:** prove that an HTTP client can use each of the three storage products it provisioned through the hosted API. Reuse and minimally extend `deploy/emulator/verify.py` (a separate opt-in `--consume-objects` path is reasonable) after inspecting the actual emulator data-plane protocol documentation and current fixture outputs. Keep `app.main`'s resource contract unchanged; storage object traffic belongs directly between the client and the emulators, not through a new API object CRUD endpoint or control-plane proxy.

### Acceptance sequence, repeated for Azure, AWS and GCP

1. Confirm the placement API is the intended `agent-v1` instance at desktop loopback **38000**, the correct namespace and image are healthy, the emulator endpoints are reachable, and the retained example IDs remain untouched. Use only dummy emulator credentials and explicit loopback endpoint overrides. Never load real cloud identity for this test.
2. Generate a new unique, provider-safe storage name and an independently unique object name. Create a nontrivial byte payload containing both readable text and binary bytes; calculate its length and SHA-256 locally. Do not print or put the payload in evidence. Derive the actual storage bucket/account and Azure `data` container from the operation's safe outputs; do not invent API-provided object endpoints.
3. Validate the version/commit, submit the deploy intent through `POST /operations` with a persistent unique idempotency key, and **record key, returned operation ID and resource ID immediately**, including on partial failure. Poll with a deadline and the response's suggested interval. Confirm the saved plan has expected create actions and storage is absent before execution. Execute the returned digest through the API; poll to `succeeded` and independently verify storage exists via the emulator.
4. With the smallest protocol integration that the actual Floci endpoints support, upload the payload **directly to the cloud data plane**, independently read it back, and compare exact byte length and SHA-256. Use standard library or already installed SDK where practical. Cloud-specific endpoints/signing must come from checked emulator docs or observed behavior, not guessed wire protocols. Record only provider, storage/object names, statuses, length, hashes, safe operation/resource IDs and timestamps. Treat unexpected reads or mismatched hashes as failure.
5. Delete the object directly and independently read again to establish absence. Only after object cleanup is confirmed, submit a **new** destroy intent for that newly owned resource ID, save its own key/operation ID, inspect its delete plan and digest, execute via API/Temporal, and read back infrastructure absence. Do not destroy a retained example or any resource whose ownership is ambiguous.
6. Write incremental evidence under `.local/pve-placement/evidence/` after every accepted operation and significant data-plane step, so a crash does not lose the IDs. The file is local and ignored; summarize only safe proof in `docs/progress.md`. A failure should retain enough IDs, state and next safe action for manual recovery.

For `503 dispatch_unconfirmed` or an ambiguous transport failure, retry the same request with the same key/body, or the same operation/digest for execution, within a bounded window; query the recorded operation before considering further action. Do not create a fresh intent to bypass ambiguity. If an operation becomes `uncertain`, a payload cannot be independently verified, or ownership cannot be established, **stop mutation and record the recovery handoff**. There must be no unconditional `finally` block that destroys infrastructure on an ambiguous outcome. A failed local verification alone is not permission to reapply a saved plan.

### Completion and scope boundary

The milestone is complete when **all three clouds** have real remote evidence of create → object upload → byte-for-byte readback → object delete/absence → reviewed infrastructure destroy/absence, with safe IDs, byte counts, and matching SHA-256 recorded, and focused regressions pass. Record any Floci incompatibility precisely if it blocks completion; do not fake a result or silently narrow to a container/bucket metadata check. Run `uv run pytest` and `uv run ruff check .` when the change warrants them, plus the real hosted verifier; distinguish each result and preserve the logs/evidence. Update `docs/progress.md`, `docs/work-deployment.md` and `docs/work-prompt.md` in the same change if verification, settings, limits or request usage change, including their current-as-of lines.

**Later, separate tasks:** establish real work-cloud executor identities and permissions; design transactional remote ledger, plan storage and fencing **only if distributed hosting is required**. The thin HTTP client, controlled interruption, acceptance/dispatch crash, plan/apply/failure replay and quiesced backup/restore tests are implemented locally. Stable pagination and release checks are the current iteration sequence. Uncertain outcomes and post-snapshot changes still require manual reconciliation. The older statement that remote storage is automatically the next milestone is superseded here. None of those tasks is part of the first object-consumption slice.
