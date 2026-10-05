# Progress

Plan and milestone definitions: [rewrite-plan.md](rewrite-plan.md). Go-era progress: [archive-go/progress.md](archive-go/progress.md).

## 2026-10-05 — Released v1.0.0: images published

At the engineer's request: annotated tag `v1.0.0` on `013abca` (= `main`, CI green; matches
`project.version`). `.github/workflows/image.yml` run 37251359448: `check` (lint, default suite),
`image-scan` (both images, Trivy gate) and `publish` all succeeded. Published, public, with SBOM
and provenance:
- `ghcr.io/azskylab/forgeapi:v1.0.0` — `sha256:3b67bcdb6821e4211dfd7c7801ec9c1582242521fae61b0794f55a22a107a633`
- `ghcr.io/azskylab/forgeapi-engine:v1.0.0` — `sha256:649145413ff13c19155426ba9edd8933b443a851652e4baada000070fb87e787`
  (also tagged `latest` and `sha-013abca…`).

Readback: anonymous registry manifest requests returned 200 with those digests; the pulled
`forgeapi:v1.0.0` served `/healthz` `{"status":"ok","contract":"agent-v1"}` and contains Terraform
v1.16.5; smoke container removed. Runbook, handover, README and brief now name the digests.

## 2026-10-04 — Work handover pack

The engineer asked to prepare the repository so an LLM at work can take it and implement it there;
work already runs an earlier version (≈ `v0.7.x`: `/deployments` on Container Apps, Easy Auth,
Table Storage).
- **New `docs/work-handover.md`** (entry point): read order, ground rules at work, what changed since
  v0.7.x (no setting removed or renamed; `FORGEAPI_DB_BACKEND` must be `sqlite`), baseline and
  inventory step for the work repository, side-by-side strategy (old deployment untouched; legacy
  `app.legacy:app` still serves `/deployments` on Table Storage; adoption not built, with design
  facts: both versions key state `deployments/<id>.tfstate`), engineer decisions with
  recommendations, phases 0–7 with gates, proven vs unproven, report format.
- **`docs/work-prompt.md` rewritten** as a short paste prompt (it had grown to ~250 lines
  duplicating the brief). `AGENTS.md` gains an **At work** bullet (handover gates and engineer
  approvals replace the home-lab-only rules) and the hosted identity now says AKS workload identity.
- **Cold-read review** (a fresh Opus agent role-playing the work LLM) found 15 issues; all fixed:
  the work catalog had no way into the pod (new required `forgeapi-catalog` ConfigMap mounted in
  both containers, `FORGEAPI_CATALOG_PATH` in the ConfigMap); v2-token requirement for a reused
  registration; checkers ordered before the image existed and `pattern_check` cannot read private
  repos (checks moved after deploy: in-pod `team_check`, `GET /v1/patterns/{name}/check`); image
  tag must be `v1.0.0`, ACR import, pin by digest; secrets from Key Vault via the AKS secrets
  provider instead of local files; commented `FORGEAPI_STATE_*`/`FORGEAPI_AZURE_SUBSCRIPTION_ID`
  and Temporal TLS volume/mounts; brief contradictions (reconcile endpoint exists; Entra changes
  need approval, not forbidden); lab-only blocks labelled; ingress, Temporal-owner inputs and
  state-account decisions added.
- **Proof of the manifest change:** the README's kind rehearsal re-run with the changed base
  (image from `main`): pod 2/2 Ready, catalog read from the mounted ConfigMap, local-file plan →
  exact digest → `succeeded` (file read back in the pod) → destroy `succeeded`; cluster deleted.

## 2026-10-04 — AKS base rehearsed with Entra auth as shipped; work-day runbook

Goal: close "Entra auth on AKS" before the move and turn the work deployment into an ordered
checklist. Home lab only; the lab Entra tenant used read-only (token issuance); nothing created
in Entra or any cloud.
- kind v0.30 cluster, image built from `main` `96611a8`, in-cluster Temporal dev server, Floci
  AWS attached to the kind network (socat sidecar, one extra egress rule for port 4566 in the
  scratchpad overlay). The `deploy/aks` ConfigMap **as shipped** (`entra` auth) with only tenant,
  audience and Temporal address filled in; tenant mapping as the `forgeapi-tenants` Secret with
  real lab group IDs.
- Through the pod (port-forward): `/readyz` 200; no token / garbage / real ARM-audience token →
  401; real caller token → only the `platform` unit (the pod fetched Entra JWKS through the
  base NetworkPolicy's 443 egress); `hr` → 403 (validate and submit); S3 bucket plan → exact
  digest → `succeeded` (404 → 200), `owned_by_caller` true, no account ID → destroy (404). Pod
  3/3 Ready, 0 restarts; no bearer-token text in `api`/`worker` logs. Root's own script first
  expected 403 for an environment absent from this mapping; the API correctly returned 422.
- **Work-day runbook** added to `deploy/aks/README.md`: caller app registration, worker identity
  and federated credential (AKS OIDC issuer, subject), RBAC and the optional Graph permission,
  image, Secrets, apply, ordered verification (each lab-proven step marked), record, rollback.
- Cleanup: cluster deleted, Floci project removed, empty `kind` network removed (0 containers).

**Limits:** real-Entra workload identity, Azure Disk, real Azure RBAC and the work Temporal remain
for the work cluster; the image was built locally, not published.

## 2026-10-04 — Merged to main; first hosted CI run passed

At the engineer's request (no PRs): `platform-2026-10` pushed and `main` fast-forwarded to it
(`1e9cdfd..bd19198`). The push started the first GitHub-hosted run of `.github/workflows/check.yml`
(run 37240347133): `check` (locked sync, Ruff, default tests) and `image-scan` (both images built,
Trivy gate) succeeded. Image publication (`image.yml`, on tags) has not run.

## 2026-10-04 — Real Entra: caller auth through the full stack, live group re-checks via Graph

The engineer provided a lab Entra tenant with a signed-in `az` session ("you can use that to
verify"). Used read-only: token issuance and Graph reads; **nothing was created or changed in
Entra or any cloud** (existing app registration "ForgeAPI Lab API", existing `forgeapi-lab-*`
groups). Cloud targets stayed on Floci AWS. Tenant-specific IDs live only in the scratchpad
run directory, not in the repo.
- **Caller auth, real tokens** (API in `entra` mode, real Temporal, production worker, Terraform,
  Floci AWS; isolated run dir without `.env`): `/readyz` 200; no token, a garbage token and a real
  tenant-signed ARM token (wrong audience) → 401; the real v2 token (23 group claims) → discovery
  shows only the business unit of the caller's group (`platform`, not `hr`) and only `dev`
  (`prod` needs another group); validate/submit into `prod` or `hr` → 403; deploy in
  `platform/dev` planned → exact digest → `succeeded` (S3 404 → 200), `owned_by_caller` true, no
  account ID in the resource; destroy → 404. No token text in API or worker logs.
- **Live group re-checks** (`FORGEAPI_LIVE_GROUP_CHECKS`, default off): with `entra` auth, before
  `AppRolloutWorkflow` accepts the router and `AppTeardownWorkflow` accepts replica destroys, the
  worker calls Graph `getMemberGroups` for the requester and intersects with the request-time
  snapshot (`apps.current_caller`): removal takes effect as `requester_access_revoked`, added
  membership is never gained, a vanished object has no groups. Graph failure → 503
  `group_check_unavailable`, transient (the app waits and retries). Identity: existing
  `azure_identity.credential()`; worker needs `GroupMember.Read.All`. No new dependency (urllib).
- **Bug found by the real stack, fixed:** with the worker unable to reach Graph the app went
  `failed` ("router could not be accepted") instead of waiting: both app activities re-raised any
  5xx before checking for transient refusals, and Temporal's bounded activity retries ran out.
  Root fix in `app_activities.py`: transient check first. Red/green-reasoned activity tests added.
- **Real-stack proof:** `POST /apps` (two AWS replicas + Route 53 router) under a real token with
  live checks on: replicas gate → router accepted after the Graph check → ready → teardown, replica
  destroys accepted after the Graph check → destroyed, S3 404. Negative: worker with no Azure
  login (`AZURE_CONFIG_DIR` empty) held the app in `planning_router` for 120 s with no error and
  no router; the same app proceeded to the router gate once a worker with the login started.
  Before the fix the identical run failed the app. Real-Graph pytest (`tests/test_live_groups.py
  ::test_real_graph`, opt-in via `FORGEAPI_TEST_ENTRA_*`): member group kept, non-member group
  stripped. All test apps destroyed; Floci project removed (0 containers/networks).

**Evidence:** `tests/test_live_groups.py` (13 incl. the opt-in real-Graph test, which root ran:
13 passed with the real-Graph test enabled, after the activity tests). Default suite **1181 passed, 31
skipped** in two consecutive `-rf` runs; Ruff and whitespace passed (the docs test first caught the
undocumented setting).

**Limits:** Graph `GroupMember.Read.All` for the hosted identity is unverified (the lab used the
signed-in user); a requester identified by `sub` (no `oid`) is refused under live checks; the
lab token's group claim worked with 23 groups (overage >200 still refused, unchanged); the
workload-identity token exchange itself was not run against real Entra (needs a public OIDC
issuer — the work cluster).

## 2026-10-04 — Work-move prep: Temporal mTLS and AKS workload identity proven in the lab

Goal: close the two settings the AKS target depends on that had never run for real (home lab
only; no real cloud touched). Opus planned/reviewed; two Sonnet coders wrote the tests; root
probed by hand, reran everything and wrote the docs.
- **Temporal TLS/mTLS** (`tests/test_temporal_tls_live.py`, default suite). The Temporal dev
  server has no TLS listener, so the test puts a TLS terminator in front of it that requires a
  client certificate and offers ALPN h2; certificates are generated per test and the server
  certificate's only SAN is `temporal.internal`. Through it: `/readyz`'s `ready()`, the API's own
  dispatch (no injected client) and `build_worker` ran a local-file plan → exact-digest apply →
  file readback; every connection presented the client certificate. Refused: no client cert,
  unrelated CA, no server name, plaintext. No product change was needed.
- **AKS workload identity** (`tests/test_floci_workload_identity.py`, `--floci`, test pattern
  `examples/floci-wi-azure`). Root first proved by hand that Floci Azure answers the federated
  client-assertion exchange (`POST /{tenant}/oauth2/v2.0/token`, seen in its log) and that
  Terraform 1.16.5 + azurerm 4.65.0 creates a resource group with `ARM_USE_AKS_WORKLOAD_IDENTITY`
  and a token file (readback 200, destroyed 404); a missing token file fails with `reading OIDC
  Token from file ... provided by AKS Workload Identity`. The test sets only the webhook's
  `AZURE_CLIENT_ID`/`AZURE_TENANT_ID`/`AZURE_FEDERATED_TOKEN_FILE`, so it proves the product maps
  them: API → real Temporal → `build_worker` → Terraform → Floci created the group (404 before,
  200 after), a destroy planned with the token file removed ended `failed` with the fixed
  credential diagnostic and the group intact, and a destroy with the file restored read back 404.
  **Root rerun: 1 passed on Terraform 1.15.9 (44.43 s) and 1.16.5 (44.34 s).**
- **Backend:** by hand on 1.16.5, `backend "azurerm"` with `use_aks_workload_identity=true`
  initialised, authenticated through the token file and reached the storage-account lookup
  (404, nothing created). Full blob state was not run: the backend speaks HTTPS to
  `*.blob.core.windows.net` and the emulator storage proxy is HTTP-only by design; no
  emulator-specific plumbing was added (engineer rule). A dead proxy guaranteed nothing left the host.
- **Decision (lab gap 3, live Entra group membership):** first deferred (needs Graph, not provable on emulators); superseded the same day once the engineer offered a lab Entra tenant — see the next entry.
- Emulator compose project `forgeapi-wiprobe-20261004` removed (0 containers, 0 networks, no
  resource groups left). Default suite **1169 passed, 30 skipped** in two consecutive `-rf`
  runs; Ruff and whitespace passed.

**Limits:** emulator evidence only: real Entra federated credentials, the AKS webhook, Azure
RBAC, Azure Disk and the work cluster's Temporal certificates remain unverified. A diagnostic
that is not a credential error can still include a local file path (paths are not redacted).

## 2026-10-04 — AKS base rehearsed with real provider Terraform against Floci

The engineer asked why the kind rehearsal used only the `local-file` pattern instead of
simulating real cloud with Floci. Re-run (home lab only, no real cloud): kind cluster; Floci
containers attached to kind's Docker network; rehearsal overlay (scratchpad, not committed —
home-lab specific IPs) adding three socat sidecars (`localhost:4566/4577/4588` → emulators), the
emulator storage proxy as a sidecar, the emulator CA in the worker's `SSL_CERT_FILE`,
`HTTP_PROXY` to the proxy, and a tenant mapping placing Azure/AWS/GCP in `rehearsal/dev`.
- First attempt: budgeted zone refused the example patterns (no `estimated_costs`) — correct
  guardrail; rehearsal dropped the budget.
- Second attempt: every emulator call timed out (Azure TLS handshake, GCP apply POST, AWS plan
  deadline). Root cause proven: the kind node reached the emulators but the pod could not — **the
  base NetworkPolicy is enforced** (kind v0.30 CNI) and allows egress only to DNS, Temporal and
  TCP 443. This corrects the earlier claim that kind does not enforce policies. The GCP create
  plan passed (no API call) and its apply ended `uncertain`; the bucket read back 404 and an
  operator reconciled it as failed through the API.
- With an egress rule for the emulator ports added in the overlay: through the pod, Azure
  (resource group, storage account, container via the storage proxy), AWS S3 and GCS each planned,
  applied (readback 200 from the host), drift-checked (Azure: update-only emulator noise) and
  destroyed (readbacks 404). Cluster deleted, Floci detached, `kind` network removed.

**Takeaway for AKS:** the 443-only egress rule matches real Azure/Entra/GitHub; anything the
patterns reach on other ports (private endpoints, databases) needs an explicit egress rule.

## 2026-10-04 — Independent review and fixes; self-service Deploy; AKS base rehearsed on kind

**Independent review** (three read-only Opus 5.5 reviewers: apps/Temporal, teams/security,
fleet/ledger/runtime). Core guarantees held: no apply without the exact approved digest, no double
apply, no placement-ID or cross-unit leaks, no portal XSS. ~28 confirmed findings fixed by three
Sonnet coders (each fix with a test; root re-verified red/green for `app_member`, view-only
destroy and system-key collision):
- **Teams/tenancy:** a team edit (archive, group removal, target change) racing an in-flight intent
  is now refused inside the acceptance transaction (409 `placement_stale`; db mode stamps the team
  revision at validation); execute refuses new work accepted after an archive (`team_archived`);
  names matched with fullmatch (trailing newline bypass); YAML anchors/aliases rejected and team
  docs capped at 1 MB (alias bomb); import apply needs `expected_revisions` from its dry run
  (412/428); team warnings use only each pattern's pinned-version contract findings.
- **App workflows:** rollout/teardown survive transient DB failures, retry busy members (drift
  checks) instead of failing, re-plan members whose dispatch was lost, keep polling through
  `uncertain`, stop once ready/torn down (no sticky timeout error), re-check requester access
  (`requester_access_revoked`); a retried `POST /apps` revives a dead rollout; any destroy request
  resumes an unfinished teardown and any earlier key replays its own; members can't be changed
  outside the app (`app_member`); destroy/discard need change rights; system idempotency keys use
  a separator users can't send; new `POST /apps/{id}/discard` releases a failed app's planned
  members' budget. New behaviour behind `workflow.patched()`; old replay fixtures still replay.
- **Fleet/runtime:** drift sweep keeps ≤20 checks outstanding and stamps unplaceable resources
  (one refused event per interval); init-lock wait bounded by the deadline (plan ends `failed`,
  not `uncertain`); diagnostics never show state-lock holder text and redact injected values;
  promotion takes `size` (422 `promotion_needs_size`); `promoted_from` survives updates; upgrade
  takes `expected_commit`; drift check without local state reports `unknown`, never `in_sync`;
  SIGINT reset moved to the Terraform child only (parent untouched); symlinked pattern files
  never read (`symlinked_file`); half-initialised changelog cache repaired; `forget` excluded
  from managed objects.

**Self-service Deploy** in the portal (#deploy): zone → deployable pattern → generated form
(schema types, sizes, labels, output references) with live validation and budget impact →
exact request JSON + Idempotency-Key → plan review. Root fixed a navigation bug (Next could never
advance). Browser proof: over-budget growth/dev warned and refused; payments/dev
`storage-account-azure` submitted from the portal, planned 3 objects, applied, Azure readback 200.
Portal also sends import `expected_revisions` and shows plain-language hints for the new refusals.

**AKS rehearsal on kind** (`deploy/aks-rehearsal/`, local only, no cloud): the real `deploy/aks`
base with a locally built image (this tree), in-cluster Temporal dev server, kind storage, no
auth, NetworkPolicy placeholders filled. Pod 2/2 Ready (`/readyz` ledger+temporal ok); plan →
approve → apply through the pod under read-only root filesystems, uid 1000, fsGroup 1000; a plan
saved before `kubectl delete pod` kept digest `6fa1d8ea…` and applied after the restart with an
intact event trail; both resources destroyed; cluster deleted (zero containers).

**Evidence:** default suite **1167 passed, 29 skipped** in two consecutive `-rf` runs; Ruff and
whitespace passed. Floci re-proof on Terraform 1.16.5 (platform version) after the fixes: HA app rollout 1/1, placed features 12/12, fleet 3/3.

**Limits:** stale-group re-checks see tenant config changes, not live Entra membership; any team
edit between validate and submit now needs a resubmit; kind does not enforce NetworkPolicy and
proves nothing about Entra, workload identity or Azure Disk. (Correction, same day: kind v0.30 does enforce NetworkPolicy — see the Floci rehearsal entry.)

## 2026-10-03 — Database-backed teams: operator admin API and portal Teams screens

The engineer approved moving teams (business units) into the database with an operator-only
write path ("yes, make it world class"; both were parked items). Design: the API stays the only
writer — the portal is a static page, validation and audit live in the API, and target IDs must
be limited to operators.
- **Storage:** `teams` and append-only `team_revisions` tables in the operations database (same
  file the ledger uses, so backups include them; triggers abort UPDATE/DELETE on revisions).
- **Source switch:** `FORGEAPI_TENANTS_SOURCE=file` (default, unchanged) | `db`. Operators come
  only from `FORGEAPI_OPERATOR_GROUPS` (and auditors from `FORGEAPI_AUDITOR_GROUPS`), never from
  the database, so nobody can grant themselves admin through the API.
- **Admin API** (`/admin/teams…`, capability `team_admin`, discovery `caller.operator`): list,
  read (only place target IDs appear), dry-run validate with findings and impact, create, full
  replace with `If-Match` (428/412) and a required reason, warning acknowledgement (409
  `warnings_not_acknowledged`), revisions with path diffs, revert, archive/unarchive (archived =
  no new intents; destroy, discard, already-accepted executes, reads and drift checks still work),
  atomic YAML import. Impact errors refuse changes that would strand or move live resources;
  warnings flag budgets below reserved, patterns in use, removed access. Every write audited in
  the same transaction (`team.<action> <name> r<revision>`).
- **`python -m app.team_check tenants.yaml`** validates a mapping file with the same rules.
- **Portal Teams** (operators only): list with zone chips and budget meters, five-step onboarding
  wizard with live checks and generated YAML, detail, editor with impact preview and acknowledgement
  checkboxes, 412 conflict handling with diff, history with diffs and revert, YAML import.
- Root changes: reserved team names `new`/`import` (they would shadow portal routes) with a test.

**Evidence:** `tests/test_team_admin.py` (21) and `tests/test_team_check.py` (19), console tests.
**Live run in the browser:** demo switched to `db` mode with operator group `platform-ops`; the
portal Import screen dry-ran `tenants.yaml` (2 creates, no errors) and applied it (both teams r1,
12 resources attached, all ready resources visible again); the wizard onboarded team `growth`
(group, dev Azure zone with budget 200, two Azure patterns, inject `business_unit`/`cost_center`)
→ r1, offered in discovery; a developer deploy into `growth/dev` placed on Azure `eastus`, applied,
Azure readback 200; in the editor a subscription change on that zone was blocked
(`placement_change_with_resources`, impact "1 resource in dev would be affected") and a budget cut
to 20 required ticking the acknowledgement before Save enabled → r2; history shows both revisions
with actor, reason and changed paths. All Teams screens at 400 px without horizontal scroll and in
light theme; no page errors. Default suite **1114 passed, 29 skipped** in 4 consecutive runs; Ruff and whitespace passed. One earlier run (started right after the browser automation, without `-rf`) had 1 failure whose test is unknown; not reproduced in the next 5 runs. Future runs keep `-rf` so a recurrence names its test.

**Limits:** team checks skip resource types/variables/costs of git-hosted patterns (pattern
contracts cover those); a new intent checks placement before its budget transaction, so a team
edit landing in between is caught by the execute-time recheck; Postgres remains a later step.

## 2026-10-03 — Pattern onboarding: templates, contract checker, API check, portal Contract panel

The engineer asked whether any Terraform pattern can be dropped in and for an onboarding process.
A read-only map of what the API enforces found the contract scattered across code and 14 gaps
caught only at request time (or after apply). Built ([pattern-onboarding.md](pattern-onboarding.md)):
- **Templates** `examples/pattern-template-azure` (primary: empty `azurerm` backend the platform
  fills, provider wired to `var.subscription_id`, optional private endpoint from the injected
  subnet, Key Vault secret-reference example, sizes and costs, README, changelog, CI) and lighter
  `-aws`/`-gcp`. Root pinned their CI to Terraform 1.16.5 (was 1.9.8).
- **Checker** `app/pattern_check.py` + CLI `python -m app.pattern_check <dir|git-url> [--ref]
  [--cloud] [--terraform] [--json]` and `--catalog patterns.yaml`: ~35 rules with stable codes
  (errors = refused/breaks, warnings = risks), file:line, exit 1 on errors, registration snippet.
  Catalog loading now raises a clean error for unknown keys. **Root review change:** a provider
  that hard-codes the account/subscription/project is an error (`hardcoded_placement`) — it would
  ignore the landing zone — not a warning.
- **API** `GET /patterns/{name}/check?version=` (capability `pattern_checks`; static checks at the
  pinned commit, cached per commit). **Portal** Contract panel, catalog badges, resource chip.
- Decision (engineer, mid-task): Floci is only free home-lab verification; onboarding targets the
  real Azure environment at work; no Floci-specific onboarding steps or harness.

**Evidence:** `tests/test_pattern_check.py` (68, incl. real `terraform validate`). Templates checked
with `--terraform` on 1.16.5: Azure 0 errors / 0 warnings; AWS and GCP 0 errors / 1 warning
(`local_state` until their commented backend is set). A deliberately broken copy reported
`required_version_excludes_platform`, `sensitive_input`, `target_not_wired`, `secret_like_output`,
`remote_code` with file:line. **Live onboarding run:** the Azure template as a tagged git repo
checked clean from its git URL, the demo registry checked clean with it added, `GET
/patterns/storage-secure-azure/check?version=v1.0.0` returned 0/0 at the pinned commit, and the
portal Contract panel showed "passes". **Bug found by the live run and fixed:** the portal's
catalog page and upgrade panel failed with 422 for callers in several business units (no
`business_unit`/`environment` sent); now uses the selected zone or the first unit/environment
that offers the pattern. Default suite **1068 passed, 29 skipped**; Ruff and whitespace passed.

**Limits:** checks are static (provider behaviour, non-JSON-safe outputs and `for_each` keys built
through locals are found only at plan/apply); a variable described as platform-set is a caller
input unless the tenant mapping injects it (now an explicit registration step in the guide).

## 2026-10-03 — Cost history, pattern changelogs, placement 422, budget leak, and the flake root cause

- **Placement:** a pattern allowed in an environment without a target for its cloud is 422
  `cloud_not_available` (validate, submit, promote, updates; audited) instead of 503; malformed
  targets stay 503. Discovery `deployable_patterns` per environment; `GET /patterns?environment=`
  lists only those (capability `deployable_patterns`).
- **Pattern changelog** (capability `pattern_changes`): `GET /patterns/{name}/changes?from=&to=` —
  commit subjects (no authors), input-schema diff (default VALUES never shown), and
  `new_required_inputs`. Live: the router's v1.0.0 → v1.1.0 reports `health_path` removed and two
  new required inputs. Portal "What changes" panel; root made "Plan upgrade" disabled (with the
  reason) whenever new required inputs exist and memoized changes so auto-refresh can't flicker
  it back on (verified in Chromium across two refreshes).
- **Cost history** (capability `cost_history`): `GET /budgets/history` rebuilds a daily reserved
  series from the ledger (no new storage); the last day equals budget discovery's `reserved`
  (live demo: 180 = 180). Portal sparklines on zone cards, zone chart page (7/30/90 days, budget
  line, keyboard/hover readout), by-pattern and top movers, Home spend tile. Root replaced a test
  evasion (an SVG namespace split to dodge the no-URL assertion) with an explicit allowance.
- **Budget leak fixed (found by the cost-history rebuild):** a deploy ending `failed` kept its
  acceptance-time reservation forever; it now restores the previous amount through the same code
  path as discard/expiry (`ledger._restore_reservation`); reconcile-as-failed releases too;
  `uncertain` still holds. Red/green tests in `tests/test_budget_release.py`.
- **Intermittent failures — root cause proven, two product fixes:**
  1. `terraform init failed: … installing hashicorp/local …: text file busy`. Proven by hand that a
     fresh workspace's `init` (no lock file) re-extracts providers into the shared plugin cache in
     place (same inode, new mtime), so any plan/apply executing that binary makes the init fail —
     in production too, whenever one deployment's init overlaps another's plan/apply. Fix:
     `TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE=true` in `terraform._env` (init now links the
     cached copy). Real-Terraform red/green test. Very likely also the drift-test flake this
     session began with.
  2. SIGINT deadline test failed only when pytest was launched in the background: shells start
     background jobs with SIGINT ignored and exec keeps it ignored, so Terraform children would
     ignore the graceful deadline SIGINT and be SIGKILLed after GRACE without writing state (any
     worker started with `&`/nohup — including this session's demo worker). Fix: importing
     `app.terraform` restores the default SIGINT handler when it is ignored; red/green test.
  **Proof:** two concurrent full suites previously failed the same two tests every time; after
  the fixes, two rounds of two concurrent suites: **999 passed each, 0 failures**.

**Evidence:** new tests `tests/test_pattern_changes.py` (17), `tests/test_cost_history.py` (11),
`tests/test_budget_release.py` (7), two new `tests/test_terraform_bounds.py` regressions, console
tests. Floci re-proof after the Terraform environment change (root): placed features 12/12, app rollout 1/1, fleet 3/3 on **both Terraform 1.15.9 and 1.16.5**. Default suite **999
passed, 29 skipped**; Ruff and whitespace passed.

**Limits:** cost history is reconstructed (legacy rows flat; seeded rows corrected at "now");
the changelog compares variable blocks only (not resource changes — the upgrade plan shows
those); the demo's history spans one day so the charts show a single step.

## 2026-10-03 — Fleet management: drift detection, upgrades, promotion, portal Fleet view

- **Continuous drift detection** (capability `drift_checks`): `POST /resources/{id}/drift-check`
  accepts a `drift_check` operation (normal acceptance, so the busy rule applies) that runs
  `plan -refresh-only -lock-timeout=0s` into a scratch `drift.tfplan`, shows and deletes it,
  never applies; state proven byte-identical. Resource `drift_status`/`drift_checked_at`/`drift`;
  filter `?drift_status=`. Optional `DriftSweepWorkflow` (`FORGEAPI_DRIFT_SWEEP_MINUTES`, off by
  default) checks idle resources as `system:drift-sweep`. Drift-check operations are hidden from
  default `GET /operations` (older clients only know deploy/destroy); see api-versioning.md.
  **Root review fix:** a check needs deploy rights in the zone (it runs Terraform with the zone's
  placement), not just visibility; red/green test added. Root also added worker logging of the
  exception class (never the message) for failed checks.
- **Upgrade detection** (capability `upgrade_detection`): `latest_version`/`upgrade_available` from
  a per-process TTL cache (`FORGEAPI_VERSION_CACHE_SECONDS`, 5 s git timeout, null on failure),
  filter `?upgrade_available=`, filtered by the caller's allowed patterns before the cache.
- **One-call upgrade** (capability `resource_upgrade`): `POST /resources/{id}/upgrade {version}`
  re-plans with the stored caller inputs, labels, refs and size (root changed the portal plan
  from an `inputs: {}` body that could silently reset optional inputs to this endpoint).
- **Promotion** (capability `promotion`): `POST /resources/{id}/promote {environment}` → new
  resource in the target zone, same pattern/version, `expected_commit` = source commit, caller
  inputs (never injected) + overrides, labels + `promoted_from`; normal guardrails/budget; refs
  must be supplied (422 `promotion_needs_refs`).
- **Portal Fleet view:** tiles, drifted table split into **missing** (a delete in
  `resource_drift`, red) and **changed** (update only, amber; root change after the live demo
  showed emulator update noise on almost every resource), upgrades table, check-now/check-all,
  resource drift/upgrade/promote panels.

**Evidence:** `tests/test_drift_check.py` (18), `tests/test_upgrade_detection.py` (10),
`tests/test_promotion.py` (17), extended console tests; replay fixture
`drift_sweep_history.json`. Floci `tests/test_floci_fleet.py` (aws/azure/gcp: out-of-band delete
directly on the emulator → check → drifted with the deleted address and nothing recreated →
repair → upgrade detection on a new tag → upgrade → destroy): **root rerun 3 passed on Terraform 1.15.9 (272.00 s) and 1.16.5 (278.81 s)**, no leftover buckets. **Live demo:**
"Check all visible" in the portal ran 10 checks; deleting the demo bucket `checkout-assets-dev`
out of band (S3 204) → check → `drifted` with `aws_s3_bucket.test:delete`, bucket still 404;
promoted `checkout-ledger` dev → prod with a name override: same commit `ebe7dc26f088`, prod
placement, `promoted_from`, Azure readback 200; promoting a GCP resource into prod (no GCP target)
was refused. Default suite **960 passed, 29 skipped** in 4 consecutive clean runs; Ruff and
whitespace passed.

**Open/limits:** one full run (overlapping another agent's concurrent pytest session) failed
`test_drift_check` replay and `test_terraform_bounds` SIGINT; not reproduced in 4 quiet runs or
under full CPU load; cause unconfirmed. Two prod drift checks in the demo failed once with no
diagnostic and could not be reproduced in 11 later checks (class-name logging added). Floci
reports update-only drift on Azure/GCP and on a fresh S3 bucket (`tags`), so those show
"changed". Patterns keeping local state cannot be upgraded (existing rule). A zone that allows a
pattern but has no target for its cloud refuses with 503 (config error), not 4xx.

## 2026-10-03 — App failover and ordered teardown

- `POST /apps/{id}/failover` (capability `app_failover`, Idempotency-Key) `{"primary": n}` on a
  `ready` app: rebuilds the router intent from its current `input_refs` (`primary_*` → chosen
  replica, `secondary_*` → previous primary), accepts it through the normal path and returns the
  app to the router gate; `primary` changes only after the approved apply. Refusals:
  `app_failover_noop`, `app_not_ready`, `app_replica_not_ready`, `app_failover_unsupported`, 422
  out of range. A discarded/expired failover plan returns the app to `ready` on the old router.
- `POST /apps/{id}/destroy` (capability `app_teardown`, Idempotency-Key): new deterministic
  `AppTeardownWorkflow` (`forgeapi-<app_id>-teardown`, never applies) → router destroy gate →
  replica destroys accepted by the workflow → replica gate → `destroyed`. Guardrails: 403
  `policy_denied` with nothing accepted where `allow_destroy` is false (re-checked at the replica
  step); 409 `app_destroy_unavailable` while anything is unfinished; failed teardowns retry with a
  new key. `AppRolloutWorkflow` unchanged (its replay fixture still passes); new replay fixture
  `app_teardown_history.json` from a real run.
- Portal: PRIMARY/SECONDARY on members, "Make replica N primary" with two-step confirm,
  "Tear down app" that stays disabled until the app name is typed, teardown stepper, destroy gates
  with prominent destructive warnings, Idempotency-Key per pending action reused on retry.

**Root finding (emulator, not product):** the fail-back plan replaced both health checks with
`replace_paths` `request_interval`/`type` while the first failover updated them in place. Proven
by hand against Floci: after `UpdateHealthCheck` (fqdn + path), `GET` no longer returns `Type` or
`RequestInterval` (they were `HTTP`/`30` before). Real AWS keeps them. The Floci test now requires
pure in-place updates on the first failover and allows a replace on a later one only for exactly
those two attributes.

**Evidence:** `tests/test_apps.py` grows to cover failover/teardown end to end on real Temporal +
local Terraform (refusals, replays, guardrail 403, retry, replay fixture). Floci
`tests/test_floci_app_rollout.py` now does rollout → failover to Azure → fail back → app teardown
with independent Route 53/S3/Azure readbacks at every gate; **root rerun 1 passed on Terraform
1.15.9 (228.80 s) and 1.16.5 (228.77 s)**, no leftovers. **Live demo through the portal:**
`catalog-api` failed over to Azure (primary 1), failed back (primary 0), torn down with the typed
name (confirm disabled before typing), both destroy gates approved; app `destroyed`; zone gone,
S3 and Azure 404; audit `app.create`, `app.approve` ×2, `app.failover`, `app.approve`,
`app.failover`, `app.approve`, `app.destroy`, `app.approve` ×2; members `destroyed` with null
cost; payments/dev reserved back to 180. Default suite **910 passed, 26 skipped**; Ruff and
whitespace passed.

**Limits:** health is still operation state, not live probing (probing real-cloud hostnames from
the home lab is not allowed); failover approval of a destructive plan is not policy-blocked.

## 2026-10-03 — One-request HA apps: `POST /apps` with a Temporal rollout

The engineer approved phase 1 of HA ("keep going step 1"). Capability `app_rollouts`:
`POST /apps` (Idempotency-Key), `GET /apps`, `GET /apps/{id}`, `POST /apps/{id}/approve`
(+ `/v1`). 2–4 replicas are validated, budget-checked and accepted **all-or-nothing in one
transaction** (`ledger._accept` per replica; one refused replica rolls everything back). A
deterministic `AppRolloutWorkflow` (`forgeapi-<app_id>-rollout`) polls a read-only activity,
and once every replica has succeeded accepts the router through the normal policy/acceptance path
(idempotency key `<app_id>:router`, `replica_refs` → `input_refs`) and starts its plan as the same
`OperationPhaseWorkflow` id POST /operations uses. **It never applies.** Two approval gates, each
one call naming exactly that gate's planned operations with exact digests (409
`app_gate_mismatch` / `plan_digest_mismatch`, all-or-none in one transaction, audited
`app.create`/`app.approve`/`app.router`/`app.state`). App state is derived from member operations.
Portal: rollout stepper, gate panel listing exactly the plans and digests being approved with a
two-step confirm, rollouts in progress on Home.

Decisions: two gates (the router's plan cannot exist before the replicas' outputs do); `uncertain`
outranks `failed` in derived state; caller groups are snapshotted on the app for the later router
acceptance; a discarded replica fails the app while other planned replicas keep their reservation
until discarded; the rollout gives up after 7 days.

**Evidence:** `tests/test_apps.py` (27 tests: real Temporal + `build_worker` + local Terraform end
to end, wrong/partial digests refused with nothing applied, idempotent replay, atomic budget
refusal, visibility 404, discard → failed, router-activity idempotency/refusal, replay fixture
`tests/fixtures/recovery/app_rollout_history.json` from a real run). Floci:
`tests/test_floci_app_rollout.py` (plans only before gate 1 — S3/Azure/Route 53 404; partial
approval 409 with nothing created; after gate 1 replicas read back and the workflow planned the
router by itself; after gate 2 Route 53 PRIMARY/SECONDARY and per-replica health paths read back;
failover drill; no placement IDs; teardown 404). **Root rerun: 1 passed on Terraform 1.15.9
(204.66 s) and 1.16.5 (204.34 s)**, no leftovers. **Live demo:** one `POST /apps` for
`catalog-api` (AWS + Azure + router v1.1.0); both gates approved from the portal in headless
Chromium; app `ready`; audit `app.create`, `app.approve` ×2; S3 and Azure readbacks 200; Route 53
PRIMARY → `catalog-api-use1-0001.s3…`, SECONDARY → `catalogapieus0001.blob…`; no page errors, no
horizontal scroll at 400 px. Default suite **866 passed, 26 skipped**; Ruff and whitespace passed.

**Limits:** health is still Terraform/operation state, not live probing; no app-level discard or
destroy (members are destroyed individually, router first); failover remains a router update.

## 2026-10-03 — Multi-cloud HA app recipe and portal Apps view

The engineer asked for "the option for a multicloud HA app". Decision: build it from existing
primitives first (no new endpoint): replica patterns per cloud + a global failover router
pattern wired with `input_refs`, grouped by labels `app`/`app_role`; recipe in
[ha-apps.md](ha-apps.md). Emulator support probed first: Floci AWS supports Route 53 hosted
zones, health checks and failover records; Floci GCP has no Cloud DNS.

- Test patterns `examples/floci-ha-replica-aws` (S3 + `index.html` + `health`),
  `floci-ha-replica-azure` (RG + storage account + container; blobs impossible on Floci),
  `floci-ha-router-aws` (zone, two HTTP health checks, PRIMARY/SECONDARY failover CNAMEs).
- **Root review fix:** the router used one `health_path` for both health checks, so in a real
  cloud the other replica would always be unhealthy and failover could never happen. It now takes
  `primary_health_path`/`secondary_health_path` from each replica; the test asserts each check's
  path before and after the swap.
- Portal: Apps list (health pill healthy/degraded/down, N of M replicas ready, PRIMARY/SECONDARY,
  `app_fqdn`, cost), app page with topology, replicas, router refs/outputs, member operations and
  a failover review link; app chips on resource pages; Apps panel on Home.

**Evidence:** `tests/test_floci_ha_app.py` (real API → Temporal → worker → Terraform → Floci):
replicas read back (S3 index/health, Azure account/container), Route 53 zone, records and health
checks read back, destroying a referenced replica 409 with the replica intact, failover update
plans in-place updates only and reads back PRIMARY → Azure with the zone ID unchanged, label
filter returns exactly 3 members with cloud/region/managed objects and no placement IDs, ordered
teardown to 404. Root rerun after the fix: **1 passed on Terraform 1.15.9 (203.70 s) and 1.16.5 (204.23 s)**; afterwards no `forge*` buckets and only the demo hosted zone remained. **Live demo:** app `storefront` in payments/dev (AWS
replica, Azure replica, router); the failover plan (4 in-place updates, not destructive) was
approved from the portal's Apps page; Route 53 read back PRIMARY moved from
`storefront-use1-0001.s3…` to `storefronteus0001.blob…`; portal showed healthy, 2 of 2, Azure
PRIMARY. A root-killed rerun left two replicas behind; root destroyed them with Terraform in that
run's workspaces and confirmed no stray buckets or zones. Default suite **836 passed, 25 skipped**.

**Limits:** see [ha-apps.md](ha-apps.md): no automatic failover proof (Floci does not evaluate
health checks), Azure replica is a container, real cloud is never used from the home lab (engineer rule; work-env transfer only); the demo's
router still runs the pre-fix pattern version.

## 2026-10-03 — Developer portal: infra, jobs, history, budgets, projects, landing zones

The engineer clarified the goal: a page developers use to see the infrastructure they created,
in-progress jobs, history, budgets, projects and app landing zones. `/console` is now that portal
(Home, Infrastructure, Landing zones, Jobs, History, Catalog, resource and operation pages).
API additions to support it (additive v1 fields, OpenAPI snapshot updated, no workflow change):
- Resource: `cloud`, `region`, `estimated_monthly_cost` (null once destroyed; root decision after
  seeing a destroyed resource still counted), `owned_by_caller` (stored actor == caller; actor
  never returned), `created_at` (set on first creation, kept through updates), `managed_objects`
  (planning stores `planned_objects` = every managed `resource_changes` entry including no-ops
  and excluding pure deletes, inside the existing placement-ID check; copied to the resource on
  apply success; `[]` after destroy).
- Discovery business units: `clouds` per deployable environment (names only).
- Decision: "projects" are the `project` label; landing zones are business unit × environment.
  `owned_by_caller` follows the last successful operation's actor (an update by a colleague
  transfers it).

**Evidence:** `tests/test_portal_api.py` (real local Terraform lifecycle incl. no-op replan,
older bodies, tenancy cloud/region/cost/other caller, discovery clouds) and an extended
`tests/test_console.py`. Floci placed suite asserts cloud, region, cost, ownership and the exact
managed objects per cloud (`aws_s3_bucket.test`; Azure RG, storage account and container;
`google_storage_bucket.test`), `[]` and null cost after destroy, and `clouds` on discovery
(results below). **Live demo (root):** Floci AWS/Azure/GCP (compose project
`forgeapi-floci-portal20261003`), Temporal dev server, production worker, API and storage proxy
started from a scratch dir with a two-unit tenant mapping (payments dev/prod, analytics dev;
prod destroy-locked with protected types). Seeded through HTTP: seven applies across three
clouds and both units, one destroy, one prod plan awaiting approval, and one GCP apply Floci
rejected (`uncertain`, diagnostic `Bucket names must be lowercase`). Readbacks: S3 200, GCS 200,
Azure storage account 200, destroyed bucket 404; no placement IDs in `/resources`. Headless
Chromium rendered every view in dark and light without JS errors (favicon 404 only), 400 px
without horizontal scroll. **Approving the prod plan from the Jobs board** (Review plan → Apply →
Confirm) ran `operation.execute accepted` → applying → succeeded; Azure readback 200 and the
resource lists its three managed objects. Default suite **835 passed, 24 skipped**; Ruff and whitespace passed. Placed Floci suite **12 passed on Terraform 1.15.9 (439.20 s) and 1.16.5 (437.67 s)**.

**Limits:** overview counts and lists read at most 20 pages; costs are pattern estimates in the
budget's unit, not billing; managed objects reflect the last applied plan, not a live state
read (drift shows on the next plan); not run with Entra auth or on AKS.

## 2026-10-03 — Built-in web console

The engineer asked for a webpage for status and history. `GET /console` (+ `console.js`,
`console.css`; capability `web_console`; excluded from OpenAPI) is a dependency-free single page
over the existing JSON API: overview (capabilities, business units, budget meters, guardrails,
operation/resource counts by state), operations (state/resource filters, pagination, 5 s
auto-refresh), operation detail (error and `diagnostic` banner, change summary, change table,
drift, outputs by name, event timeline), resources (pattern/environment/state/label filters,
labels, `input_refs` links), patterns (input schema). On a `planned` operation: "Apply this
plan" (POSTs the exact `plan_digest`) and "Discard plan", each behind an in-page second click.
Static assets need no auth; data calls carry the caller's token (memory only). Strict CSP
(`default-src 'none'`, `script-src 'self'`…), `nosniff`, `no-referrer`, `no-store`; all API text
via `textContent`. Decision: no `console` link in `/agent` (`DiscoveryLinks` is a typed v1 model;
adding a field would change the snapshot); the capability advertises it.

**Evidence:** 9 tests in `tests/test_console.py` (headers, no auth on assets while data routes
401 under Entra, capability, absent from OpenAPI, every API path in the JS exists, no
`innerHTML`/storage/eval, no inline script/style). **Real stack, root:** isolated Temporal dev
server + `app.worker` + uvicorn started from a scratch directory (no `.env`, temp data dir,
local-only catalog); seeded through HTTP: two applied deploys, one destroy, one failed plan
(precondition; the console showed `terraform plan failed: … file names must start with team-
(got bad.txt)`), one planned. Headless Chromium (Playwright's cached build, isolated `uv run
--no-project`) rendered all views in dark and light, no JS errors (only `/favicon.ico` 404), no
horizontal scroll at 400 px. **Applying through the console** (Apply → Confirm apply) took the
planned operation to `succeeded`; its events show `operation.execute accepted` then applying →
succeeded, and the file on disk reads `awaiting approval`. Root fixed two cosmetic issues seen in
screenshots (`@?` for unversioned local patterns; addresses breaking mid-word). Default suite
**829 passed, 24 skipped**; Ruff and whitespace passed. All demo processes stopped (also one
orphan worker from an earlier killed repro run).

**Limits:** no favicon; counts on the overview read at most 20 pages of 100; EasyAuth mode
ignores pasted tokens; not run against Entra auth, AKS or Floci (it only calls endpoints already
proven there). The Chrome extension was not connected, so the check used headless Chromium.

## 2026-10-03 — Drift-test flake root cause, Floci cleanup, three-cloud composition, failure diagnostics

1. **Intermittent test failure: cause proven and removed.** Every test has its own data dir, so
   each `terraform init` downloaded providers (~80 inits per run). Verified directly that `init`
   contacts the registry even with a warm `TF_PLUGIN_CACHE_DIR` and a lock file. In 11 repro
   runs one failed (a different test, `test_pipeline::test_runs_the_commit_accepted_even_if_the_tag_moves`)
   with `terraform init failed: ... releases.hashicorp.com ... TLS handshake timeout` while
   installing `hashicorp/local`. Fix (tests only, `tests/conftest.py`): a session fixture builds
   a persistent filesystem mirror (`~/.cache/forgeapi-tests/terraform-providers`, rebuilt only
   when an example's `*.tf` hash changes) and sets `TF_CLI_CONFIG_FILE` (`filesystem_mirror` for
   the mirrored providers, `direct` for everything else). **Proof:** the full default suite passes
   with a dead proxy (`HTTPS_PROXY=http://127.0.0.1:9`): 820 passed. Suite time ~157 s → ~110 s.
   The drift test's own two failures predate its diagnostics, so their exact text is unknown;
   the proven class (network during per-test init) covered every init. First run on a new
   machine needs the network once.
2. **Floci cleanup on mid-test failure.** `tests.operation_support.destroy_leftovers()` runs in
   each Floci fixture's `finally`: any workspace still holding `work/.terraform` with resources
   in state is destroyed (two passes, consumers before sources); empty-state workspaces are
   skipped; anything left fails the test loudly. It refuses to run unless `data_dir` is under
   the temp dir. **Proof:** a deliberate failing probe applied a bucket (200) then raised; the
   fixture destroyed it (Floci readback 404). A new test applies a bucket and calls the helper
   directly (200 → [] → 404 → [] again). In `--floci` sessions tests share one plugin cache
   (symlink): copies of aws/azurerm/google per test had filled the 31 GB `/tmp` tmpfs (seen as
   `no space left on device`).
3. **Input refs consumer apply on Azure and GCP.** The placed suite now applies a real consumer on
   all three clouds: AWS object and GCP object (content read back), Azure a second storage
   container `consumer` in the referenced account (read back 200). Source destroy is 409
   `resource_referenced` while the consumer is ready; ordered destroys read back 404.
   **Limit:** Floci Azure answers `501 Not Implemented` to Set Blob Properties, which
   `azurerm_storage_blob` always calls after upload (azurerm 4.65.0), so the Azure consumer is a
   container, not a blob.
4. **Failure diagnostics (capability `failure_diagnostics`).** Operations that end `failed` or
   `uncertain` because a Terraform command failed carry `diagnostic`: the command and Terraform's
   first error, sanitized by one helper (`operation_activities.diagnostic`): only
   `terraform <cmd> failed:`/deadline text is accepted (other exception text never appears);
   credential/permission errors become a fixed sentence; placement IDs, GUIDs, 12-digit numbers,
   URLs and `/subscriptions/` paths become `[redacted]`; ≤400 chars. Additive v1 field (OpenAPI
   snapshot updated); no workflow change. **It immediately found two latent problems:** the
   Floci "failed plan" test had been passing vacuously (Terraform 1.15.9 and 1.16.5 reject a
   constant `condition = false` precondition at `init`; now references a variable), and the
   disk-full failures above.

**Incident (no damage):** a coder smoke-tested `destroy_leftovers()` with bare `uv run python`,
where settings load `.env` and `data_dir` is the real `.local/data`; it ran `terraform destroy` in
six retained workspaces. Verified read-only: every destroy failed (the lab certificate expired
2026-09-27), no file in those dirs changed, and the Azure activity log shows no operation in the
preceding 6 hours (the only deletes were the 2026-10-01 00:32–00:58 UTC lab teardown). The helper
now refuses non-temp data dirs; coder prompts forbid ad-hoc app code outside pytest.

**Evidence (root reran everything):** default suite **820 passed, 24 skipped (Floci)**, 110.57 s;
with a dead proxy 820 passed; Ruff and `git diff --check` passed. Floci, real Temporal +
production worker + Terraform: `test_floci.py` 7/7, `test_floci_features.py` 5/5,
`test_floci_placed_features.py` 12/12 on **Terraform 1.15.9 and 1.16.5** (placed: 447.83 s and 438.70 s).
Emulators ran as compose project `forgeapi-floci-s20261002b` and were removed afterwards.

**Limits:** the diagnostic is Terraform's first error only; Terraform masks sensitive values but
other input values it prints are shown; over-broad auth markers (e.g. "403" inside a number)
hide a non-auth error. Emulator evidence is not real-cloud evidence.

## 2026-10-02 — Placed-mode features proven on all three Floci clouds through real Temporal

New opt-in suite `tests/test_floci_placed_features.py` (`--floci`, 11 tests): tenant mapping with
units `work` (`dev`, and `locked` with `allow_destroy: false` and protected
`aws_s3_bucket`/`azurerm_storage_account`/`google_storage_bucket`, budget 100 each) and `other`;
patterns cost 30. Real API → Temporal dev server → production worker → Terraform → Floci, with
emulator readbacks:
1. **Discovery:** only the caller's unit is visible (`other` 403); guardrails and budgets exact;
   `reserved` +30 after an applied bucket and back after destroy; no subscription/account/
   project IDs in `/agent`, `/patterns`, pattern descriptions or resources.
2. **Labels, filters, budget enforcement [aws, azure, gcp]:** labelled resource found by
   `environment`+`label`+`state`; at reserved 90 the next intent is 403 `budget_exceeded`
   exactly as `available` predicts; discarding frees 30 and the next fits; unused names never
   created (404); destroy returns reserved to 0.
3. **Input refs [aws, azure, gcp]:** same-environment refs validate; refs into `locked` are 422 on
   validate and submit; on AWS a real consumer writes an object into the referenced bucket
   (readback 200, text checked); destroying the source is 409 `resource_referenced` while the
   consumer is ready; ordered destroys read back 404.
4. **Locked guardrails [aws, azure, gcp]:** create allowed; destroy 403 `policy_denied` with no
   operation; a rename forcing replacement of the protected type plans but execute is 403
   `policy_denied`; discard leaves the original intact and the new name absent.
5. **Accept-path plan expiry (aws):** with the clock +2 h a new intent on the same resource
   expires the old plan (failed, plan file not the old digest) and is accepted; budget reserved
   once (30, not 60); apply 200, destroy 404.

**Evidence:** coder run 11 passed, 443.9 s (Terraform 1.15.9); **root rerun 11 passed, 445.12 s
on Terraform 1.16.5**. Root replaced a racy "plan file absent" check with "absent or not the
expired plan's digest" (the new plan may already be writing that path). Emulators ran as
`forgeapi-floci-20261002` and were removed (zero containers/networks; five stopped containers
from 2026-09-30 are unrelated and untouched). Default suite: **807 passed, 22 skipped (all
optional Floci), 156.83 s**; Ruff and whitespace passed.

**Limits:** consumer apply proven on AWS only (Azure/GCP refs checked at validate/submit); no
`try/finally` emulator cleanup if a test fails midway; emulator evidence is not real-cloud IAM,
billing or parity. The intermittent drift-test failure above remains unexplained.

## 2026-10-02 — New features proven through real Temporal, worker, Terraform and Floci

The engineer pointed out that the day's features had only run through the recorded dispatcher.
New opt-in suite `tests/test_floci_features.py` (`--floci`) drives the HTTP API → real Temporal
dev server (`WorkflowEnvironment.start_local`) → production worker (`build_worker`) → Terraform →
Floci AWS, with independent HTTP readbacks of Floci (26 checks):
1. **Composition, labels, filters, destroy protection:** bucket A (labels) applied; `GET
   /patterns`, `?label=team=e2e&state=ready`, `?pattern=object` correct; B with `input_refs`
   bucket←A.name applied and the object exists in A's bucket in Floci; destroying A refused 409
   `resource_referenced` with the bucket still present; B destroyed (object 404), then A (404).
2. **Version upgrade with remote S3 state in Floci:** v1.0.0 tag `v1` read back from Floci; update
   to v1.1.0 planned `["update"]` on the same bucket, applied, Floci tag `v2`, resource
   `v1.1.0`; state object in the Floci state bucket, no `work/terraform.tfstate`; destroy removed
   the bucket and `work/.terraform`.
3. **Discard and plan expiry:** plan file present after planning, gone after discard, bucket
   never created; with expiry set and clock +2 h, execute 409 `plan_expired`, plan gone, bucket
   404; a new intent on the same resource planned, applied (200) and destroyed (404).
4. **Failed plan cleanup:** a precondition failure after init leaves `failed`, no `tfplan`.

**Evidence (root reran, not only the coder):** `pytest --floci tests/test_floci_features.py`:
**4 passed, 92.73 s with Terraform 1.15.9 and 4 passed, 98.47 s with 1.16.5** (binary copied from
local image `forgeapi:hardened-tf1165-20261002`). Existing `tests/test_floci.py` on today's code:
**7 passed, 193.93 s (1.15.9) and 7 passed, 194.30 s (1.16.5)**. Emulators ran as compose
project `forgeapi-floci-20261002` (pinned digests, loopback ports, no state volumes) and were
removed afterwards; zero containers and networks remained. Without `--floci` the suites skip.

**Intermittent failure (unresolved):** `tests/test_drift.py::test_deleted_file_out_of_band_shows_in_drift_and_a_recreate_in_changes`
failed in 2 of 10 full default-suite runs today (the replan ended `failed`); it passed 6/6 alone,
3/3 with all tests up to `test_d*`, and in the last 4 consecutive full runs (807 passed each,
about 163 s). The failing runs' tmp dirs had already rotated out, so the cause is unconfirmed.
Suspected: every test has its own data dir, so each downloads the `local` provider from the
Terraform registry and a transient network error fails the plan. The test now prints the
operation and the tail of its `terraform.log` on failure; worker logs record command and exit
code only (by design), so that pins the failing command, not the provider error text.

**Not covered on the real stack:** placed-mode features (budget/guardrail discovery,
cross-environment ref refusal, labels under tenancy), Azure/GCP for the new features, the
accept-path plan expiry, the late-activity ownership guard. Emulator evidence is not real-cloud
evidence.

## 2026-10-02 — Independent review of today's features and fixes

An Opus 5.5 read-only review of all features above found no high-severity defects (no double
apply, cross-unit leak, placement leak or v1 change for clients not using new fields; ref values
cannot bypass injected inputs or region rules; fingerprints stable). Three findings, all fixed
red-then-green by a Sonnet coder:
1. **Medium:** a pending consumer could be applied after its referenced source was destroyed.
   `ledger.execute` now refuses a deploy whose `input_refs` source is destroyed or has an
   unfinished destroy (409 `reference_not_ready`); `accept` rechecks reference rules inside its
   transaction. One helper (`ledger.reference_error`) serves policy, accept and execute.
2. **Low:** a late failing activity (operation already marked `uncertain`, reconciled, and a new
   plan written) could delete the newer plan. Cleanup now runs only while the activity still owns
   the operation (`operation_activities.owns`).
3. **Low:** an accept refused after expiring a blocker (budget or resource change) rolled back
   the database but had already deleted the blocker's plan. Accept now deletes it after commit.

**Evidence:** 5 new tests, each failing before its fix. **Full suite: 807 passed, seven optional
Floci skips, 163.06 s**; Ruff and whitespace passed.

**Limits:** the post-apply `tfplan` unlink after a successful apply is not ownership-guarded
(pre-existing); the destroy `.terraform` ownership guard is not separately exercised.

## 2026-10-02 — Opt-in lazy plan expiry

- `FORGEAPI_PLAN_MAX_AGE_HOURS` (unset = unchanged behavior; >0 enforced; AKS ConfigMap sets 24).
  An expired `planned` operation is failed inside existing transactions: execute commits the
  expiry (`expired`, `operation.expire` event, plan deleted, budget restored) then returns 409
  `plan_expired` (next action `validate_and_resubmit`, `refused` audit); a new intent blocked
  only by an expired plan expires it and is accepted. `planned_at` is recorded when planning
  finishes; older operations fall back to `updated_at`. Operations show `plan_expires_at`.
  Capability `plan_expiry`. Discard and expiry share `ledger._release_plan` (discard unchanged).
- Decision: lazy, not scheduled — no Temporal workflow change, so replay fixtures are untouched.
  An expired plan nobody touches keeps its reservation; budget discovery counts it.

**Evidence:** 8 tests in `tests/test_plan_expiry.py` (real local Terraform plans; clock
monkeypatched). AKS kustomization renders with the new key. **Full suite: 801 passed, seven
optional Floci skips, 153.31 s**; Ruff and whitespace passed.

**Limits:** not exercised on Temporal or AKS; ages use the API host clock.

## 2026-10-02 — Destroy protection for referenced resources

- Destroying a resource that a `ready` resource references via `input_refs` is refused at
  submission: 409 `resource_referenced`, `refused` audit, no operation. Pending consumers do not
  block (they applied nothing). A ref to a source with an unfinished destroy is 409
  `reference_not_ready`. Capability `reference_protection`.
- **Root review fix:** a resource whose update referenced its own output blocked its own destroy;
  `ledger.referenced` now excludes the resource itself. New test fails without it (409), passes
  with it.

**Evidence:** 5 new real-Terraform tests in `tests/test_input_refs.py` (17 total). **Full suite:
793 passed, seven optional Floci skips, 149.80 s**; Ruff and whitespace passed.

**Limits:** the destroy check and reference acceptance are separate transactions (narrow race);
no override for operators; checks run at submission, not again at execute.

## 2026-10-02 — Output references (composition)

- Deploy intents accept `input_refs` (≤16): input name → `{resource_id, output}`. At acceptance
  each source must be visible (404 otherwise), `ready` (409 `reference_not_ready`), in the same
  business unit/environment under tenancy (422), and expose the output publicly (withheld or
  missing → 422 naming the output, never a value). Values are copied into the operation's inputs
  and validated like caller inputs; a key in both `inputs` and `input_refs` is 422; destroy with
  refs is 422. Resources show `input_refs`. Capability `input_references`. Omitted from the
  canonical intent when absent (fingerprints unchanged).
- Decision: snapshot at acceptance, no live link or dependency graph; updates must resend refs.
- **Root review fix:** a referenced `location` was overridden by the unit's `region_default`
  (default injection only checked raw `inputs`). Fixed in `policy.validate`; new real-Terraform
  test fails without the fix (`location: westus` injected) and passes with it.

**Evidence:** 12 tests in `tests/test_input_refs.py` with real local Terraform (source applied,
consumer's `terraform.tfvars.json` holds the source output). The coder ran `ruff format` on
`app/`; it restyled only files already edited this session (ledger, operation_activities,
client, contracts, main) and now all `app/` passes `ruff format --check`. **Full suite: 788
passed, seven optional Floci skips, 141.78 s**; Ruff and whitespace passed.

**Limits:** destroying a referenced source is not blocked or warned; refs do not cross
environments; no Temporal run for composition.

## 2026-10-02 — Resource labels

- Deploy intents accept optional `labels` (≤16; key `^[a-z][a-z0-9_.-]{0,62}$`; value ≤128
  chars, no control characters). Stored on the resource, returned in `labels`, never in
  Terraform inputs. Update without labels keeps them; with labels (even `{}`) replaces them;
  destroy with labels is 422. `GET /resources?label=key=value` (parameterized `json_each`
  filter, composes with the others); client `resources(label=)` / `--label`. Capability
  `resource_labels`.
- Decision: `labels` is omitted from the canonical intent when absent, so label-less intents
  keep their fingerprint (pinned `80b9798f…`); labels are part of identity when set (same key,
  different labels → 409). A pending update shows the old labels until it succeeds.

**Evidence:** 23 label tests (incl. real local Terraform apply, tfvars free of labels, replay,
visibility, filter composition). Root changed `test_version_upgrade` to compare the captured
request material with `canonical_intent()` (the identity actually fingerprinted) instead of raw
`model_dump()`, which now has the extra `labels: None`. **Full suite: 776 passed, seven
optional Floci skips, 134.05 s**; Ruff and whitespace passed.

**Limits:** one label per filter query; labels are caller-asserted metadata, not authorization.

## 2026-10-02 — Budget discovery; client pattern listing and inventory filters

- **Budget discovery** (capability `budget_discovery`): `business_units[].budgets` keyed by
  deployable environments that have `budget_monthly`, with `monthly_budget`, `reserved` and
  `available` (clamped at 0). Admission's resource sum moved into `ledger._reserved`, which both
  admission (inside its write transaction, excluding the changed resource) and discovery use, so
  shown headroom equals what is enforced. Decision: corrupt stored amounts or invalid budget
  config make discovery return the same sanitized 503 as submission, rather than hiding it.
- **Client/CLI:** `Client.patterns()` / `patterns` (requires `pattern_listing` and the `catalog`
  link); `resources(pattern=, environment=, state=)` / `--pattern --environment --state`
  (requires `resource_filters`; values checked before any HTTP).

**Evidence:** 7 budget-discovery tests (limit/reserved/available, rise after acceptance, over-
headroom intent refused `budget_exceeded`, within-headroom accepted, destroyed and other-unit
resources ignored, legacy counted, `/v1`, sanitized 503s); client tests including a real HTTP
call. Root fixed one stale CLI test stub. **Full suite: 751 passed, seven optional Floci skips,
two existing warnings, 129.01 s**; Ruff and whitespace passed.

**Limits:** discovery scans the resources table per budgeted environment (as admission always
has); fine at lab scale, an index or SQL aggregate is the upgrade path. Budgets are author
estimates, not billing.

## 2026-10-02 — Catalog listing, inventory filters, guardrail discovery, disk cleanup, version upgrades

- **`GET /patterns`** (capability `pattern_listing`): name, cloud and describe link for each
  pattern the caller may use, from `patterns.yaml` only (no git). Discovery link `catalog`.
- **Inventory filters** (capability `resource_filters`): `GET /resources?pattern=&environment=&state=`
  in SQL inside the visibility condition, composing with `after`. Without a tenant mapping
  resources have no environment, so that filter matches nothing there.
- **Guardrail discovery** (capability `guardrail_discovery`): `business_units[].guardrails`
  keyed by deployable environment, with `allow_destroy` and sorted `protected_resource_types`.
- **Disk cleanup.** Discard deletes the saved plan inside its transaction; every `failed`
  outcome in the worker deletes the plan before `ledger.finish` releases the reservation (so a
  newer operation's plan at the same path is never touched); `uncertain` keeps it; a successful
  destroy deletes `work/.terraform`. Unlink errors are suppressed so cleanup never changes an
  outcome. Decision: plans left by earlier operations are not swept (no scheduler).
- **Pattern version upgrades.** An update may name another version of the same pattern; it plans
  against existing state at the new commit and executes only after review. Decision: kept
  `prepare`'s refusal for state inside `work/` (fails at planning, state byte-identical).

**Evidence:** new tests: 6 disk-cleanup (real Terraform 1.15.9 plan/apply/destroy; fake binary
only for the lock/stale/uncertain failure step), 4 upgrade (real Terraform with `file://` git tags
v1.0.0→v1.1.0: plan `["delete","create"]` against existing state, applied content changed, one
state file, resource shows new version/commit; no Temporal), pattern-list, filter, guardrail and
discard-cleanup tests. `test_cloud_targets` now asserts the unsafe plan is deleted. The v1
OpenAPI snapshot changed additively. **Full suite: 737 passed, seven optional Floci skips, two existing warnings, 127.67 s**; Ruff and whitespace passed. Root hardened both unlink sites with `contextlib.suppress(OSError)`. Opus 5.5 planned and
reviewed; Sonnet coders (Agent `sonnet` alias; exact Sonnet version not shown by the tool)
implemented.

**Limits:** upgrades are not exercised against the azurerm backend or Temporal here; no migration
of state from `work/` to a backend. The client CLI does not yet expose pattern listing or
inventory filters. No hosted run.

**Next:** done in the entry above.

## 2026-10-02 — Attribute-level plan detail and plan guardrails

- **Plan detail.** Each change carries `changed_attributes`, `replace_paths`, `action_reason`
  and `sensitive_attributes` (paths only, depth 3, at most 50 per resource; values never stored
  or returned; covered by the placement-ID scan). Real Terraform 1.15.9 for a changed
  `local_file.content`: actions `["delete","create"]`, `replace_paths` `[["content"]]`,
  `action_reason` `replace_because_cannot_update`.
- **Guardrails.** Tenant environments may set `protected_resource_types` (execute refused when
  the reviewed plan deletes or replaces one) and `allow_destroy: false` (destroy refused at
  submission, before any operation or reservation). 403, reason `policy_denied`, next action
  `revise_intent`, `refused` audit; a denied plan stays `planned`. Capability `plan_guardrails`.

**Evidence:** 11 change-detail tests (one with real Terraform) and 16 guardrail tests. The
guardrail coder could not run pytest in its environment; root ran it, found three `/v1` test
cases using an idempotency key containing `/` (rejected 422), fixed the test, then 16 passed.
**Full suite: 716 passed, seven optional Floci skips, two existing warnings, 123.07 s.** Ruff and
whitespace passed. Opus 5.5 planned and reviewed; Sonnet 5 implemented.

**Limits:** guardrails do not apply when a resource's environment has been removed from the
mapping (execution is then refused by placement checks anyway). Discovery does not yet show
which environments are protected. Map keys appear in attribute paths.

**Next (in order):** inventory filters (pattern, environment, state) and a `GET /patterns`
listing; workspace/plan-file disk cleanup; discovery of environment guardrails; then hosted CI
and AKS deployment when the engineer pushes.

## 2026-10-02 — Drift visibility, hardened image, Terraform 1.16.5

- **Drift.** Planned operations carry `drift` from Terraform's `resource_drift` (no extra run),
  checked for placement IDs like `changes`. Real Terraform 1.15.9 with `local_file`: deleting or
  editing the file out of band yields drift `["delete"]` and change `["create"]`; an untouched
  re-plan has no drift. Older operations return `drift: null`.
- **Image.** Base images pinned by digest; Debian packages upgraded at build; the default target
  has no Temporal binary (694 MB versus 874 MB); `engine` target for the lab; compose builds it.
  CI builds both targets and gates the default on a pinned Trivy scan (fixable CRITICAL/HIGH).
  Publishing adds `forgeapi-engine`, SBOM, provenance and `persist-credentials: false`.
- **Terraform 1.16.5.** Trivy found fixable CRITICAL/HIGH in Debian 12.13 (fixed by the upgrade
  step) and 12 HIGH in the Terraform 1.15.9 binary (gRPC, Go stdlib); the 1.16.5 binary has none.
  Applying a 1.15.9 plan with 1.16.5 was tested directly: Terraform refuses with "plan files
  cannot be transferred between different Terraform versions" and writes no state. That refusal
  is now recorded as `failed`, not `uncertain`.

**Evidence:** 6 real-Terraform drift tests; 1 red-then-green version-refusal test using the
observed text; release-workflow tests extended (pins, scan gate, digests, no Temporal in the
default stage, SBOM/provenance). Default image `sha256:6aa410ef…` (Terraform 1.16.5): Trivy
exit 0; `temporal` absent; read-only root smoke earlier gave `/healthz` 200 and `/readyz` 503
with ledger ok and Temporal unavailable. **Full suite: 689 passed, seven optional Floci skips,
112.91 s with Terraform 1.16.5 on PATH, and 689 passed, 113.37 s with local 1.15.9.** Ruff and
whitespace passed. Evidence: `.local/verification/image-hardening-20261002/`.

**Limits:** no GitHub-hosted run of the new CI jobs; `docker buildx` is not installed locally,
so SBOM/provenance publishing is unexercised. `apt-get upgrade` makes builds depend on the
Debian mirror state at build time. The local developer Terraform is still 1.15.9.

**Floci on 1.16.5:** with the pinned emulators from `compose.floci.yaml` (separate compose
project, loopback ports) and Terraform 1.16.5 on PATH, `pytest --floci tests/test_floci.py`
passed 7 tests in 201.58 s (AWS S3, Azure resource group and placed storage, GCP bucket
lifecycles through the API). The emulators and their network and volumes were removed; zero
project containers remained. Emulator evidence only.

## 2026-10-02 — AKS readiness: Temporal TLS, workload identity, manifest; filters, long-poll, request IDs

Platform (for the engineer's target: Temporal on AKS, API and worker on the same cluster):
- **Temporal TLS.** `FORGEAPI_TEMPORAL_TLS*` settings; one `connect_temporal()` helper used by
  dispatch, readiness, the worker and the legacy dispatcher. CA-only or mTLS; a half cert/key
  pair refuses to start. Exercised against the real `TLSConfig`/`Client.connect` signatures
  only: **no real TLS handshake has been tested.**
- **AKS workload identity.** `FORGEAPI_AZURE_USE_AKS_WORKLOAD_IDENTITY` sets
  `ARM_USE_AKS_WORKLOAD_IDENTITY` for providers and `use_aks_workload_identity=true` for the
  azurerm backend; exclusive with the other identity modes. Verified at the subprocess boundary;
  Terraform 1.15.9 accepted the backend argument offline (a bogus argument was rejected) and the
  cached azurerm 4.81.0 binary contains the variable name. Not run against a cluster.
- **`deploy/aks/`** kustomize base: single-replica StatefulSet (API and worker containers, 32 GiB
  managed-csi volume at `/data`, read-only root with `/tmp` emptyDir, non-root, no capabilities,
  RuntimeDefault seccomp, `/readyz`/`/healthz` probes), workload-identity ServiceAccount,
  ConfigMap, ClusterIP Service and NetworkPolicy; secrets referenced, never inlined. Rendered with
  `kubectl kustomize`; never applied.

API:
- **Filters.** `GET /operations?resource_id=&state=` (capability `operation_filters`).
- **Long-poll.** `GET /operations/{id}?wait=0..30` (capability `operation_long_poll`); async
  sleeps; returns at once for non-pollable states.
- **Request IDs and access log.** `X-Request-ID` echoed or generated; `request_id` in error
  bodies; one `forgeapi.access` JSON line per request with six fixed keys only.

**Evidence:** 46 new tests (11 TLS, 9 AKS identity, 11 filters, 11 long-poll, 4 request log).
Five existing exact-equality error assertions now ignore the per-request `request_id`, and one
client CLI fake gained the new keyword arguments. **Full suite: 680 passed, seven optional Floci
skips, two existing warnings, 108.56 s.** Ruff and whitespace passed. Opus 5.5 planned and
reviewed; Sonnet 5 implemented in two parallel, file-disjoint tasks.

**Next:** image hardening (pin base digests, keep the Temporal dev-server binary out of the
production image), CI that builds and scans the image, drift detection via a refresh-only plan,
and workspace/plan disk cleanup.

## 2026-10-02 — Older packaged image to current image upgrade probe

The baseline `forgeapi:temporal-agent` (`sha256:7ba18097…`, built 2026-09-30) was inspected
read-only: agent-v1 on Temporal, Terraform 1.15.9, Python 3.12.12, same `worker.py` and
`operation_workflow.py` bytes as the current tree, same ledger fingerprint function. The
current tree was built as `forgeapi:upgrade-probe-20261002`
(`sha256:840d2be80b4429b66776cd5149d82e1dabcfc83a60ead6e8217bbfa1994acda7`), same toolchain.

On one fresh labeled volume: the old API and engine accepted a local-file intent and planned it
(operation `op_76a025ab7d18470c97ae1ad64fae175d`, resource
`res_0a39790a1d964ba1a630c548ba141a73`, digest `b15c2622…ec57a85`), then were stopped and
removed. The current API and engine started on the same volume and paths. Root and `/v1` reads
returned the same operation, resource, digest and `planned` state; resubmitting the identical
body and key on both aliases returned the same operation; the three original events remained;
`/readyz` returned 200; the old operation gained `change_summary` and the new links; discovery
gained the capability block. Executing the exact digest twice (root, then `/v1`) produced
`succeeded` with one accepted create event, one accepted execute event, one plan receipt and one
apply receipt (no replan). The created file (31 bytes, SHA-256 `44d85213…a770ed6`) matched, and
`GET /resources/{id}` returned `ready` with its output. Every labeled container, volume and
network was removed and label queries returned nothing; `.local/data` was not mounted.

Evidence and harness: `.local/verification/upgrade-20261002/` (gitignored). Sonnet 5 ran the
probe; Opus 5.5 planned it and checked the evidence and cleanup.

**Limits:** one pending local-file plan on the bundled dev Temporal server; the old worker was
fully stopped first. No cloud provider, mixed-version workers, downgrade, discard/reconcile/
budget/tenancy paths, hosted Temporal or CI.

## 2026-10-02 — Readiness and OpenAPI quality

- **Readiness.** `GET /readyz` (unversioned, unauthenticated) checks a ledger read and a
  Temporal `check_health` call with a 2 s timeout; 503 names the failing check without error
  text. `/healthz` is unchanged liveness. The pve-desktop manifest's API readiness probe now uses
  `/readyz`. A ledger read still creates the data directory/schema on first use, as every GET does.
- **OpenAPI.** Every route has a summary, description (mutation, idempotency, next step) and
  tag; every public model field has a description; request and key response models have
  placeholder examples; the app has a description. Operation IDs are unchanged and pinned by a
  test. Cloud-target conflicts now carry reason `placement_changed`.

**Evidence:** 14 new tests: readiness against a real local Temporal server, a real refused
connection, an unusable data directory and a no-write check; OpenAPI completeness walked
recursively over `/v1/openapi.json`, examples validated against their own models.
**Full suite: 634 passed, seven optional Floci skips, two existing warnings, 102.80 s.** Ruff and whitespace passed. Opus 5.5 planned and reviewed; Sonnet 5
implemented. The coder ran one ad-hoc script with default settings, which opened the retained
`.local/data/operations.sqlite` (schema no-op, no rows changed, `forgeapi.db` untouched).

**Next:** the older-image upgrade probe (session-handoff), then Temporal TLS/API-key settings
once the AKS Temporal policy is known.

## 2026-10-02 — Operator reconciliation, plan summaries, error reasons

- **Reconcile.** `POST /operations/{id}/reconcile` (`outcome` `succeeded`/`failed`, `reason`;
  capability `operator_reconciliation`; client `reconcile`) is the audited exit from
  `uncertain`. The tenant mapping gains top-level `operators` groups; without a mapping the
  creating caller may reconcile. It runs no Terraform. `succeeded` requires an operation that
  reached apply and marks the resource `ready`/`destroyed` without outputs. The reason stays on
  the operation, out of responses and audit events. A late worker result cannot overwrite it.
- **Plan summary.** Every operation has `change_summary` (create, update, delete, replace,
  `destructive`), computed from stored changes, so existing operations get it too.
- **Error reasons.** Error bodies keep the frozen `code` and add `reason` (11 values, e.g.
  `resource_busy`, `plan_digest_mismatch`, `revision_moved`, `budget_exceeded`) and a matching
  `next_action`. `resource_busy` returns the blocking operation ID and status URL.

**Evidence:** 58 new tests (29 reconcile, 7 summary, 22 reasons). The coder did not follow strict
test-first for every file and said so. Review changed the reconcile state event's actor from the
worker to the operator. **Full suite after that fix: 619 passed, seven optional Floci skips,
two existing warnings, 102.09 s.** Ruff and whitespace passed. Opus 5.5 planned and reviewed;
Sonnet 5 implemented.

**Limits:** reconcile trusts the operator; the API performs no state or cloud read to confirm
the chosen outcome. Reconciling `failed` after a partial apply leaves whatever was created in
the cloud and in Terraform state. Cloud-target conflicts raised in `app/cloud_targets.py` carry
no `reason` yet. Engineer decisions taken by the agent on instruction: operators are mapping
groups; legacy workflows stay registered on the worker; nothing is committed.

**Next:** readiness checks (ledger, Temporal), OpenAPI quality for agent tool generation, then
the older-image upgrade probe.

## 2026-10-02 — Resource inventory

`GET /resources` and `GET /resources/{resource_id}` (root and `/v1`, capability
`resource_inventory`, client `resources` / `resource` commands) expose what a caller owns.
Visibility matches operations (creating actor, or business-unit membership); missing and
invisible share 404. Public fields are an explicit allowlist: id, state
(`pending`/`ready`/`destroyed`), pattern, version, commit, business unit, environment, last
applied outputs, withheld output names, latest operation ID, updated time and links. Operations
gain a `resource` link. Listing pages by resource ID with an `after` cursor because acceptance
rewrites the row and changes its rowid. No schema change.

**Evidence:** 28 new tests, including exact public field sets on list and read, placement-ID
absence, cross-actor/unit isolation and traversal stability across a mid-page insert and a
mid-page update. `tests/fixtures/v1/openapi.json` regenerated for the additive routes; the
capability assertion in `tests/test_versioning.py` gained the flag. **Full suite: 568 passed, seven optional Floci skips, two existing warnings, 98.95 s** (two consecutive runs).
Ruff and whitespace passed. Opus 5.5 planned and reviewed; Sonnet 5 implemented.

**Limits:** no filters (state, pattern) yet. A resource whose only plan failed or was discarded
stays `pending`. Resource state is the ledger's record of the last apply, not a cloud read;
there is no drift detection. The coder saw the real-Terraform stale-plan test
(`tests/test_terraform_bounds.py`) fail once in three full-suite runs; it passed in 12 isolated
repeats and two further full runs. Cause unknown; treat as a possible flake, not as resolved.

**Next:** audited operator reconcile for `uncertain` (needs the engineer's rule for who is an
operator), then specific 409 error codes and richer plan summaries.

## 2026-10-02 — Child environment filtering and fail-closed unauthenticated mode

The engineer confirmed the work target: Temporal on AKS, the API on any suitable Azure host;
the lab topology is for development only. The fitting host for the current code is the same
AKS cluster, one replica with the API and worker sharing a managed-disk volume. Container Apps
would need the parked remote ledger. Temporal TLS/API-key settings wait for that cluster's policy.

- **Child environment.** Terraform and git children no longer inherit any `FORGEAPI_*` variable
  (the GitHub App private key and token arrived that way). Other variables pass through. The
  git token is added only for `init`; plan/apply no longer mint a GitHub App token per call.
- **Unauthenticated mode fails closed.** With `FORGEAPI_AUTH_MODE=none`, non-loopback callers get
  401 unless the new `FORGEAPI_ALLOW_UNAUTHENTICATED_REMOTE=true` is set. `compose.yaml` and
  `deploy/emulator/pve-desktop.yaml` set it (lab only; the placement overlay inherits it).
  Running lab deployments are unaffected until redeployed from these manifests.

**Evidence:** red first: 3 child-environment tests showed both secrets in a real child process
environment (fake `terraform` executable that dumps its environment); auth tests failed on the
missing setting. `tests/test_tenancy.py` had one `_env` fake widened for the new argument.
Both compose files validate. **Full suite: 540 passed, seven optional Floci skips, two existing warnings, 96.98 s.** Ruff and whitespace passed. Opus 5.5
planned and reviewed; Sonnet 5 implemented. `AGENTS.md` now names this routing and Ponytail 4.10.1.

**Limits:** provider credentials Terraform legitimately needs (`ARM_*`, the short-lived OIDC
token) still reach providers and `local-exec`. The loopback check sees the direct peer, so a
same-host reverse proxy would pass it. Hosted identity remains unverified.

**Still open from the review:** the worker registers the legacy workflow (force-unlock, replan);
removal waits on an engineer decision because the older vault in `.local/data` uses it. Next:
audited operator reconcile for `uncertain`, and `GET /resources`.

## 2026-10-02 — No wedged resources: discard, bounded Terraform, refusal classification

A three-lens project review (API contract, execution engine, path to work hosting) ranked
permanent resource locks from ordinary use as the top gap. Four causes are fixed.

- **Discard.** `POST /operations/{id}/discard` (root and `/v1`, capability
  `discard_planned_operation`, client `discard` command). Only `planned` can be discarded. V1
  states are frozen, so the operation ends `failed` with error `plan discarded before execution`
  and an `operation.discard` accepted event; the resource is released and the budget reservation
  returns to the pre-acceptance amount. Repeats return the same operation with no second event.
  Operations accepted before this change keep their reservation.
- **Provider-cache lock.** A plan or apply used to hold a shared read gate for the whole
  subprocess, so another resource's `init` waited behind a long apply until its own plan timed
  out as `uncertain`. Only `init` takes the lock now.
- **Terraform deadline.** Each operation phase gives Terraform 8 (plan) or 28 (apply) minutes,
  then SIGINT to its process group, 60 s grace, then SIGKILL. Plan deadline is `failed`; apply
  deadline is `uncertain`. Legacy calls pass no deadline and are unchanged.
- **Refusals.** An apply refused for a stale saved plan or a held state lock is `failed`, not
  `uncertain`. No unlock, retry or replan.

**Evidence:** new tests failed first (discard: 14 red; engine: 7 red, including `init` blocked
1.01 s behind a concurrent plan). The stale-plan test uses real Terraform 1.15.9 with the
local-file pattern; the lock-refusal and deadline tests use a fake binary and prove plumbing
only, not real provider behavior. `tests/test_recovery.py` fakes moved from `subprocess.run` to
`subprocess.Popen`; `tests/fixtures/v1/openapi.json` was regenerated for the additive route and
the capability assertion in `tests/test_versioning.py` gained the new flag. **Full suite: 524
passed, seven optional Floci skips, two existing warnings, 96.26 s.** Ruff and whitespace
passed. Opus 5.5 planned and reviewed both diffs; Sonnet 5 implemented.

**Limits:** deadlines were exercised in seconds with a fake binary, not at 8/28 minutes against
a cloud. A dead worker still cannot kill its Terraform child. Stale/lock detection matches
Terraform's error titles. Nothing was committed, published or deployed.

**Review findings not yet addressed, in proposed order:** (1) Terraform and git children
inherit the whole environment including the GitHub App key; `auth_mode` defaults to `none`
while the image binds `0.0.0.0`; the worker still registers the legacy workflow that
force-unlocks and replans. (2) No audited exit from `uncertain`; no `GET /resources`.
(3) All 409s are `conflict` with a fixed `next_action`; plan summaries lack counts and a
destructive flag. (4) No TLS/API-key settings for external Temporal; `/healthz` is constant.
(5) The older-image upgrade probe below is still unstarted. The work hosting shape (single host
versus a remote ledger) is an open engineer decision.

## 2026-10-02 — README architecture guide

Rebuilt README as the current system guide: navigable overview, local startup and complete
intent/CLI example, endpoint/configuration/recovery tables, state and storage explanations,
tenant/identity/budget/output boundaries, backward/forward compatibility and evidence limits.
Five native Mermaid diagrams cover architecture, the two-phase request sequence, operation
states, durable data and placement admission. The existing `#http-client` anchor is preserved.
Work deployment documents link to this guide; no runtime, contract or dependency changed.

The guide distinguishes durable acceptance from dispatch acknowledgement, repeated responses
from new work, polling-terminal uncertainty from released reservations, local versus remote
Terraform state, and fresh-image smoke evidence from an unperformed older-image upgrade.
Astra planned/reviewed; Sol edited README; root rendered and checked the result.

**Verification:** all five diagrams parsed/rendered with Mermaid 11.4.1 and were visually
inspected after correcting an initial label-format/layout issue. Settings and endpoint names
match the implementation; README file links and heading anchors, shell-block syntax and the
example intent validated. Three documentation tests passed (0.24 s); whitespace passed.
Rendered SVG/PNG previews are in gitignored `.local/verification/readme-20261002/`.
The prior full runtime checkpoint remains **502 passed, seven optional Floci skips**; the
runtime suite was not rerun for this documentation-only change.

**Next:** the older packaged Temporal runtime inspection/isolated upgrade probe described in
session-handoff remains unstarted. No test process, infrastructure mutation or cleanup is pending.

## 2026-10-02 — Current packaged runtime smoke and 8% quota handoff

The user reports **8% weekly quota remaining** and requests current transfer documentation.
All implementation is frozen and verified. **502 tests passed, seven optional Floci skips,
two existing warnings, 87.23 s**; final Astra reviews, Ruff and whitespace checks passed.
No further implementation milestone started after the quota update.

The existing Dockerfile built locally as `forgeapi:local-check-20261002-502`, image
`sha256:2ff6b29460288abe49204d439ebae9656e10a754f5e39b1084d6b2f2bb8581d9`.
A reviewed temporary harness ran isolated API and Temporal engine containers sharing a fresh
owned volume; only the API had an ephemeral loopback port. No existing compose, retained data,
credential mounts or cloud patterns were used. Both root and v1 clients submitted the same key
and executed the exact saved digest twice, retaining one operation/resource and one actual apply.

**Passed evidence:**

- Operation `op_06cfca8120154d89875f7bf9df70257a`, resource
  `res_9c5403a42f8c485fa6bd06d2c96cca2f`, final state `succeeded`.
- One accepted create and one accepted execute audit event; one plan and one apply command receipt.
- Real local file readback: 28 bytes, SHA-256
  `d415b14c18b4a2c0a45ef5a77a710ec37133bb28fba78c6141f304c96fb79337`.
- Image runtime: Python 3.12.12, Terraform 1.15.9. Docker client 29.8.2.
- Every owned container, volume and network removed; post-run label queries returned no objects.
  The image remains available. Initial sandbox Docker access failed before resource creation;
  the approved rerun succeeded. No application failure or code correction was needed.

Safe evidence/harness copies: `.local/verification/image-smoke-20261002-502/evidence.json`
and `smoke.py` (gitignored), plus the original `/tmp/forgeapi-current-image-smoke*` files.
This proves a fresh local packaged lifecycle, not hosted CI, publication, cloud execution or
an upgrade from an older image. No commit, push or existing-environment deployment occurred.

**Next, not started:** inspect the locally available older Temporal image
`forgeapi:temporal-agent` (`sha256:7ba180975fc9c22212e3d161f5750d4aea05bf107b1dcc13ca0931ef7ab38fc8`)
as a candidate for a disposable real packaged-upgrade probe preserving a pending saved plan.
Read the specific scope/constraints in session-handoff; do not mutate any retained deployment.
Hosted release gates and work-cloud identity remain unverified. No test or cleanup is pending.
Final handoff review passed after clarifying the historical-image wording; three documentation
tests, relative-file links across nine current documents, artifact-copy verification and
whitespace checks passed.

## 2026-10-02 — Client operation-page consistency

Focused fixtures reproduce acceptance of inconsistent operation-list pages: duplicate IDs,
a returned cursor anchor, unrelated/empty next_before, and wrong/coerced/empty next_offset.
Eight red cases failed to raise ClientError while three valid old/current pages passed (0.15 s).
The client-local fix ties continuation to the returned page, retains raw additive fields and
older pages omitting next_before, and adds no automatic traversal or retry. Non-null next_before
must match the final ID; next_offset must be a literal integer equal to offset plus page length
and cannot appear in cursor mode. Empty pages cannot advertise continuation.

**Evidence:** 95 focused client/transport/pagination/version/upgrade tests passed in 5.35 s.
Final Astra review, repository Ruff and whitespace checks passed. **Combined full suite:
502 passed, seven optional Floci skips, two existing warnings, 87.23 s**, including the
ledger-directory slice. No server schema/route changed. Next: isolated current-image lifecycle
verification, without publishing or using retained data/credentials.

## 2026-10-02 — Ledger directory failure boundary

Creating the ledger directory now catches `OSError` locally and raises the existing fixed
ledger-unavailable 503. Reads remain sanitized. A failed submission whose refusal audit also
cannot access storage returns the existing audit-unavailable 503, with no acceptance/dispatch.
The API does not attempt to replace a conflicting file or repair storage automatically.

**Evidence:** a real temporary regular file at the configured data-directory path reproduced
`FileExistsError` through both aliases before the fix (two failures, 0.08 s). After the narrow
catch, **89 focused ledger/read/audit/request/operation/version tests passed in 5.76 s**.
Tests preserve the file contents, then restore a directory and prove same-key acceptance
creates one operation/resource/event and dispatch. Final Astra review and Ruff passed.
Next: enforce coherent operation-page continuations in the client without changing the API.
The subsequent combined full suite passed 502 tests with seven optional Floci skips; it includes these cases.

## 2026-10-02 — Catalog Git text decoding

Git text streams now replace undecodable bytes before existing version filtering. Unrelated
non-UTF-8 tags no longer prevent valid version discovery or default-version submission;
valid SemVer ordering and peeled commit pins are unchanged. Nonzero Git errors remain
sanitized even when stderr contains invalid bytes. No retry or new dependency was added.

**Evidence:** a real annotated nonversion tag was accepted by Git and its raw invalid bytes
were verified in `ls-remote` output. Before the fix, it and a local failing Git executable
with invalid stderr raised `UnicodeDecodeError` (two failures, 0.08 s). After the one-argument
fix, **54 focused encoding/startup/deadline/operation/version tests passed in 7.75 s**.
Final Astra review, repository Ruff and whitespace checks passed. **Full suite: 489 passed,
seven optional Floci skips, two existing warnings, 102.96 s.** Next: a real ledger-directory
file collision currently bypasses structured storage errors; test and contain this narrow boundary.

## 2026-10-02 — Catalog Git startup failures

Missing/unexecutable Git now receives a fixed catalog error instead of escaping process
startup. Credential/environment preparation remains outside the narrow Popen `OSError` catch;
timeout, nonzero-exit and API handlers are unchanged. Root/v1 describe and validation return
sanitized 502 without audit; refused submissions record one refusal before returning, without
creating operations/resources or dispatch. Restoring PATH permits the original key to succeed.

**Evidence:** four pre-fix cases failed with real `FileNotFoundError`/`PermissionError` (0.10 s).
**48 focused startup/deadline/parser/operation/GitHub tests passed in 6.94 s** after the fix.
Final Astra review, repository Ruff and whitespace checks passed. **Full suite: 487 passed,
seven optional Floci skips, two existing warnings, 98.74 s.** No credential, retry or deployment
behavior was added. Next: first reproduce strict Git decoding of an unrelated non-UTF-8 tag.

## 2026-10-02 — Read-failure response boundary

Six root/v1 status/list/event cases now verify the existing SQLite error handler. Injected read
failures containing secret/path markers return the fixed sanitized 503 envelope without any
refusal-write attempt, ledger-row change or dispatch. This is regression coverage for existing
behavior, not a production bug fix or actual database corruption experiment.

**Evidence:** 101 focused read-failure/request/audit/authentication tests passed in 2.28 s.
Final Astra review, repository Ruff, relative-file links and whitespace checks passed.
**Combined full suite: 483 passed, seven optional Floci skips, two existing warnings, 86.21 s**,
including the HTTP contention proof. Next: contain Git process startup errors in the catalog.

## 2026-10-02 — HTTP reads during audit contention

A real HTTP regression now proves status/events through root and v1 return committed snapshots
while an invalid mutation waits for refusal audit behind a real SQLite writer reservation.
The held transaction changes stored operation JSON and adds an uncommitted event; all four
reads complete before lock release. After rollback, the normal 422 has exactly one refusal
record, with unchanged operation/resource and dispatch counts. No Terraform runs or production
changes were needed. This covers one reserved writer, not exclusive locks or saturated pools.

**Evidence:** 19 focused HTTP/ledger/index/pagination tests passed in 0.85 s. Final Astra review,
repository Ruff and whitespace checks passed. The 476-test full-suite result predates this
new test. Next: direct read-failure sanitization coverage.

## 2026-10-02 — Public output serialization boundary and handoff checkpoint

Final filtered public outputs now pass strict JSON serialization before success is stored.
An invalid decoded numeric value uses the existing uncertain path and reservation; no retry or
recovery endpoint is added. Sensitive/placement filtering precedes validation.

**Evidence correction:** normal Terraform `1e400` produced a Python integer with 401 digits,
exactly `10**400`, confirmed by inspecting the temporary fixture ledger. It is valid and stays
supported. The earlier succeeded-vs-uncertain expectation was wrong and is withdrawn as defect
evidence. Real Terraform positives now cover that exact integer, finite 42 and sensitive output.

The defensive rejection test explicitly injects an overflowing output JSON response **after**
the real Terraform apply/output commands. It verifies original exact-integer output and injected
nonfinite float before testing uncertainty, safe audit/status, retained reservation and no second
apply. Removing the guard made that injected case fail in 0.94 s; the guard was restored before
green verification. This is fault-injection evidence, not normal Terraform emitting invalid
numbers. **37 focused output/placement/operation/upgrade tests passed in 13.03 s**. Corrected
final Astra review, repository Ruff and whitespace checks passed. **Full suite: 476 passed,
seven optional Floci skips, two existing dependency warnings, 86.18 s.** Relative-file links
are valid across nine current documentation files. No verification run or implementation edit
remains pending at this checkpoint; all work remains uncommitted and unreleased.

**Then-queued milestone (now completed above):** add one real HTTP read-during-write regression in
`tests/test_audit_concurrency.py`. Create a queued operation, hold an uncommitted SQLite writer,
start an invalid mutation whose refusal audit waits, and require root/v1 status/events to finish
with committed data. Release the writer in `finally`, then verify the normal refusal, one audit
event and no new operation/dispatch. Reuse the existing server fixture; no Terraform execution
or production changes are expected. This connects the completed ledger-read proof to routing,
authorization, response serialization and audit contention.

The user last reported 25% usage remaining; this checkpoint includes a copyable resume prompt,
exact evidence/limitations, the shared dirty-tree warning and the next bounded task. The agent
cannot see the usage meter. Continue from this working tree and keep the handoff current.

## 2026-10-02 — Ledger read transaction contention

Ordinary get/resource/replay/list/event reads now use explicit deferred transactions rather
than reserving SQLite's single writer slot. The default `connect` mode and every mutation
retain `BEGIN IMMEDIATE`; cursor-anchor/page reads share one snapshot. Existing schema
initialization remains in place, and first-read initialization is covered. No WAL switch or
storage redesign occurred; exclusive locks and schema/index creation may still block reads.

**Evidence:** a local isolated schema/deferred-read probe succeeded while a second connection
held a writer reservation. Five actual ledger reader regressions timed out before the fix,
while first-read initialization passed (7.71 s). After the fix, **88 focused concurrency,
admission, pagination, audit, index and upgrade tests passed in 7.17 s**. The held writer
changes operation JSON, resource data and events; readers must return original committed data
before rollback releases it. Final Astra review, repository Ruff and whitespace checks passed.
**Full suite: 472 passed, seven optional Floci skips, two existing warnings, 82.80 s**.
Documentation relative-file links also passed. No test process remains running at this
checkpoint. Next bounded slice: nonfinite Terraform output values can serialize into the ledger
without raising; validate final public outputs before success, preserving the existing uncertain
outcome path. The subsequent probe produced a valid exact integer; see the corrected output-boundary
evidence above rather than treating it as a normal-Terraform defect.

## 2026-10-02 — Bounded event-history lookup

Event pagination now has an idempotent SQLite `(operation_id, seq)` index. The single schema
addition leaves queries, visibility, cursor semantics and audit triggers unchanged. Existing
ledgers create the missing index on first open; large histories can lengthen that initialization.
No data rewrite, API field or dependency was introduced.

**Evidence:** before the change, the actual sparse event reader exceeded a 2,000-instruction
SQLite budget with 8,000 unrelated rows, and opening an existing database did not create the
index (two expected failures, 0.05 s). After the fix, **95 focused event/pagination/operation/
client/upgrade/audit tests passed in 9.67 s**. The reader test includes a sparse second page and
empty tail. Astra found no production issues and requested stronger complete-row and append
sequence assertions in the populated-database test; the corrected test passed final review.
Repository Ruff and whitespace checks passed. **Full suite: 466 passed, seven optional Floci
skips, two existing warnings, 94.19 s.** No hosted scalability claim is made. Next: narrow read
transaction contention; a temporary local probe confirmed existing schema initialization and
an explicit deferred read can complete while another connection holds a writer reservation.

## 2026-10-02 — Client submission response identity

The client now compares a submission response with the intent's action, pattern and explicit
resource target. Contradictions raise `AmbiguousMutation` without adopting the returned operation
ID. Existing malformed-response handling remains unchanged, and no retry or follow-up is added.
The posted body keeps its original fields/bytes; defaults are used only for comparison. Valid
create/update/destroy responses and additive response fields remain supported. This is client
response validation, not evidence that the current server misroutes operations.

**Evidence:** three coherent contradictory-response cases failed before the fix, while six
positive cases passed (0.17 s). **84 focused client/transport/versioning/upgrade/pagination tests
passed in 5.52 s**. Final Astra review, repository Ruff, three documentation tests and whitespace
checks passed. **Full suite: 464 passed, seven optional Floci skips, two existing warnings,
93.98 s.** A follow-up read-only token-expiry inspection found the existing decoder already
requires `exp`, `iss` and `aud`; no production change was needed or claimed. No test process
remains running at this checkpoint.

## 2026-10-02 — Size-specific estimate shape

A present selected size estimate must now be a mapping before reading the environment key.
Malformed null/scalar/boolean/list entries receive fixed 503; missing sizes and valid mapping/
environment fallbacks remain unchanged. Requests without a size ignore unrelated entries.
The shared resolver also fixes the legacy failure path without expanding its compatibility
promise. No general configuration schema or fallback semantics changed.

**Evidence:** 16 regressions failed with the expected `AttributeError`, while seven valid
selection/fallback cases passed (0.53 s). After the fix, **114 focused tests passed in 8.26 s**,
covering resolver, operation policy, historical accounting, tenancy and operations. Operation
fixtures use an actual configured size and verify root/v1 sanitized 503, refusal audit and no
admission/dispatch. A legacy fixture confirms controlled 503 without deployment/dispatch.
Final Astra review, repository Ruff, three documentation tests, relative-file links and whitespace
checks passed. **Full suite: 455 passed, seven optional Floci skips, two existing warnings,
82.44 s.** Next bounded task: verify client submission responses match the submitted intent.

## 2026-10-02 — Pattern text parsing errors and handoff checkpoint

Malformed HCL and text-decoding failures now use fixed catalog errors. Both variable and
backend inspection use one small HCL reader catching the installed parser's precise
`LarkError` family and `UnicodeError`; the YAML boundary also catches decoding failures.
No dependency, nested configuration schema, execution retry or valid-document behavior changed.
Real versioned local repositories cover root/v1 description, validation and submission with
safe 502 responses, no acceptance/dispatch, and one refusal event only for a submission.
Backend-reader tests independently exercise malformed HCL and invalid encoding.

**Evidence:** eight pre-fix regressions failed with parser/decoding exceptions in 1.32 s.
**78 focused tests passed in 8.80 s; full suite: 432 passed, seven optional Floci skips,
two existing dependency warnings, 82.46 s.** Final Astra review, repository Ruff, three
documentation tests and whitespace checks passed. No hosted run or deployment occurred.

The engineer reported 25% usage remaining and requested a transferable handoff before it runs
out. The session handoff now separates verified state, dirty/untracked work, exact checks,
limitations and next work; the work brief/prompt include parsing behavior and current dates.
No test process remains running at this checkpoint. Next bounded task: a selected size-specific
cost entry that is scalar/null/list can still reach `.get(environment)` and raise; add a narrow
regression and controlled failure while preserving valid fallback behavior.

## 2026-10-02 — Pattern configuration error boundary

Malformed pattern `config.yaml` previously escaped the catalog boundary. Parser errors and
invalid root types now use the existing catalog 502 path. Missing/empty files and valid mapping
content are preserved; nested configuration validation is unchanged. Real versioned local
repository fixtures verify root/v1 responses, audit ordering and no acceptance/dispatch.
Pre-fix evidence: six rejection cases failed and four positive cases passed in 0.74 s; malformed
YAML raised parser errors and nonmapping documents were accepted. **90 focused tests passed
in 7.94 s** after the fix, including catalog deadlines, legacy API, operation policy and
operations. Final Astra review, Ruff and whitespace checks passed. Latest full suite predates
this slice (414 passed, 7 optional skips). Next: HCL and decoding error boundaries.

## 2026-10-02 — Catalog Git deadlines

Catalog Git subprocesses previously had no deadline; a stalled command could occupy an API
thread and, during checkout, its cache lock indefinitely. Commands now have a fixed 60-second
deadline, with owned-process-group termination and up to five seconds for cleanup. Successful
commands and nonzero-exit behavior are preserved; timeout maps to sanitized 502, without retry,
Terraform changes, or a total request/credential-acquisition/lock-wait deadline. Tests use isolated local executable fixtures with finite fallback exits.
Pre-fix probes failed both expectations in 1.33 s: a stalled command outlasted the intended
deadline, and a child retained stdout after its parent exited. Final coverage includes a stalled
fetch while holding the real cache lock, no ready marker, and successful checkout afterward;
it does not establish repair of arbitrary internal Git lock files. A child-spawn marker verifies
the inherited-pipe scenario occurred; its delayed output marker remains absent after cleanup.

**Evidence:** 45 focused tests passed in 6.67 s with shortened test deadlines. Final Astra review,
Ruff and whitespace checks passed. **Full combined suite: 414 passed, 7 optional Floci skips
in 80.49 s**, with two existing dependency warnings. Next: malformed pattern configuration.

## 2026-10-02 — Annotated nonversion Git tags

Catalog enumeration ignored lightweight nonversion tags, but admitted peeled annotated
nonversion tags and crashed while sorting them as SemVer. The parser now filters normalized
tags before insertion/sorting, preserving annotated version commit peeling and supported
version syntax. This is shared catalog behavior; no pattern repo or tag outside temporary
tests changed.

**Evidence:** the real annotated `stable` fixture reproduced the exact sorting exception
(one failure in 0.25 s). After the fix, **50 focused tests passed in 5.55 s** across operation
and legacy APIs, versions, upgrades and GitHub integration. Root/v1 discovery and default-version
submission retain the correct peeled commit and one idempotent operation. Final Astra review
and Ruff passed. Next: bounded Git command execution.

## 2026-10-02 — Real Temporal cross-alias concurrency

The existing real Temporal test already races repeated submissions after initial acceptance
and same-alias execution requests. That scenario now races the first admission across
root/v1 aliases and uses both aliases for exact-digest execution, with explicit single durable
acceptance, resource and real apply-receipt counts. It retains no-worker acceptance, worker restart
and late-dispatch proofs. No production code or infrastructure changed.

**Evidence:** 28 focused tests passed in 6.43 s. Review requested ten-second bounds around
all concurrent request groups in the scenario; after that correction the real Temporal test
passed in 1.15 s. Ruff and whitespace checks passed. The recorded histories contain one
scheduled activity per phase, receipts contain one plan/apply, and output readback matches.
Latest full suite predates this and the strict-capability slice (404 passed, 7 optional skips).
Next: inspect the next concrete reliability gap in the current architecture.

## 2026-10-02 — Strict capability booleans

Discovery previously coerced nonboolean capability values such as `1` or `"yes"` into `True`,
which could grant optional-feature permission despite the explicit-boolean gate. Capability
values now use the existing dependency's strict boolean type. Missing-metadata baseline behavior,
unknown correctly typed capabilities and the OpenAPI boolean schema remain unchanged.

Pre-fix evidence: three coercible-value cases failed and two already-invalid cases passed
in 0.11 s. **75 focused tests passed in 5.10 s** after the fix, including client transport,
pagination, canonical OpenAPI and historical upgrade contracts. Tests prove malformed discovery
stops before the optional page request and literal true plus an unknown boolean capability
works. Ruff, whitespace and final Astra review passed. Latest full suite predates
this slice (404 passed, 7 optional skips).

## 2026-10-02 — Placement identifiers in plan summaries

Terraform resource addresses can contain `for_each` keys. Planning previously published those
addresses before the existing apply-output filter, allowing an injected subscription/account/
project ID to appear in a public summary. Three real local Terraform regressions reached
`planned` before the fix (three failures in 2.34 s), using Azure/AWS/GCP-shaped placement and
built-in `terraform_data`, without any cloud call or apply.

The planner now rejects a disclosing summary before storing public changes or an executable
digest, using the same known placement IDs as the output filter. Identifiers remain in private
placement records and protected plan/state files where execution needs them. Addresses are not
rewritten; historical records, workflow retry rules and execution rules are unchanged.

**Evidence:** 37 focused tests passed in 11.09 s across cloud targets, operations, Temporal and
workflow replay. Tests verify absent public/stored changes and digest, unchanged private target,
clean status/list/events/command receipts, and refusal even with the actual saved-plan digest.
Normal planning and output withholding still pass. Final Astra review and Ruff passed. **Full
suite: 404 passed, 7 optional Floci skips in 78.91 s**, with two existing dependency warnings.
Next: strict boolean capability negotiation.

## 2026-10-02 — Refusal audit responsiveness

Review found async exception handlers call synchronous refusal audit writes on the event loop.
A contended SQLite writer could therefore delay unrelated requests. All five async exception
handlers now await the existing threadpool utility for response/audit work, preserving headers,
audit-before-response, sanitized failures and HTTP contracts. Storage, database timeouts, retry
behavior and admission ordering are unchanged.

Pre-fix regression: health timed out while the refusal waited on the real SQLite lock
(one failed contention case, one passing sanitized audit-failure case, 1.33 s). The test uses
the same uvicorn event loop for both socket requests and always releases its isolated lock;
it does not replace database I/O with a sleep or mock. **157 focused tests passed in 7.34 s**
after the fix, including request/auth boundaries, operations, policy and version contracts.
Final Astra review, Ruff and whitespace checks passed. **Full combined suite: 401 passed,
7 optional Floci skips in 76.20 s**, with two existing dependency warnings. This proves responsiveness during one contended refusal, not throughput under threadpool saturation.

## 2026-10-02 — Client HTTP credential boundary

The existing redirect tests substitute the opener. New loopback TCP tests show the default
client does not follow 301/302/303/307/308 redirects or send bearer credentials to a second
origin. They also exercise cross-origin links in real discovery responses, using dummy
credentials, bounded local servers and no external requests.

**Evidence:** 53 client tests passed in 3.83 s with the real opener. Review requested a timeout
on accepted server connections so shutdown cannot wait indefinitely on a partial request; after
that correction all six transport tests passed in 0.25 s. Each origin observed the expected
dummy bearer while the second server received zero requests; diagnostics omitted credentials
and response markers. No production defect or code change was needed. This is local HTTP,
not hosted TLS/proxy evidence. Latest full suite predates this tests-only addition (393 passed,
7 optional skips). Next: refusal audit responsiveness under SQLite contention.

## 2026-10-02 — Authentication principal validation

Review found malformed Easy Auth structures can crash parsing, while identityless principals
fall back to a shared `unknown` caller ID. The shared parser now validates identity and claim
shapes before constructing a caller, returning generic 401 on failure. Valid JWT `oid` precedence,
`sub` fallback, Easy Auth identity order, groups and additive unknown claims remain supported.
No new authentication mode, authorization feature or hosted identity configuration was added.

Pre-fix evidence: **36 rejection cases failed and one valid-caller case passed in 2.05 s**.
Malformed Easy Auth envelopes raised parsing exceptions; identity/group defects otherwise
reached operation acceptance. Tests use isolated data and a recording dispatcher, with no
Terraform execution. **120 focused tests passed in 3.60 s** after the fix, including legacy
placement and request boundaries. Tests verify refusal audit, no acceptance/dispatch, raw token
and header redaction, valid distinct callers and existing group-overage refusal. Ruff, whitespace
and final Astra review passed. **Full suite: 393 passed, 7 optional Floci skips in 76.50 s**,
with two existing dependency warnings. These are local fixtures,
not live Entra or hosted proxy evidence. Shared legacy authentication also rejects malformed
principals; this security correction does not extend the legacy API compatibility guarantee.
Next: prove redirect and cross-origin credential boundaries with the real client HTTP transport.

## 2026-10-02 — Historical budget accounting

Old malformed estimates can still distort new admissions: negative entries can offset valid
costs, and NaN can invalidate comparisons. Per-contributing-row and aggregate validation now
runs inside admission, plus read-only validation of legacy cost rows. It preserves filtered placement,
destroyed exclusions, failed/uncertain reservations, same-key replay and cleanup. Refuse with
sanitized 503; do not rewrite history, repair records or migrate a database. Stored NULL remains
unknown/zero; data types already lost through SQLite affinity cannot be reconstructed.

**Evidence:** 71 focused tests passed in 5.63 s; Ruff and production review passed. Tests cover
masked negatives, NaN, invalid types and aggregate overflow, unchanged operation/resource rows
and legacy database on refusal, exact-key replay after corruption, and destroy acceptance.
Review corrected a test expectation: validation-only can return 200 for bad operation-ledger
accounting because the check runs during transactional submission; legacy checks can fail at
validation. **Full combined suite: 348 passed, 7 optional Floci skips in 75.52 s**, with two
existing dependency warnings. Next: malformed authentication principals.

## 2026-10-02 — Pattern cost validation

Operation policy now rejects invalid numeric resolved estimates and selected budget limits
before admission with sanitized 503. Pure probes confirmed the former negative/NaN reservation
bypass and overflowing conversion. Zero, None/unlimited, missing-estimate refusal, ordinary
finite prices and existing environment fallback retain their behavior; destroy paths bypass
new-cost admission as before. Shared legacy configuration behavior is unchanged.

**Evidence:** **123 focused tests passed in 7.58 s** across operation policy/lifecycle, request
boundaries and tenancy. Tests use real versioned pattern configuration and check no acceptance,
resource or dispatch on refusal, one submission refusal event and audit-free validation. Valid
zero, exact budget, over-budget, fractional, unlimited and missing-estimate cases pass. Ruff,
whitespace and final Astra review passed. A fixture initially failed because an unchanged cost
had nothing to commit; its temporary Git commit now permits that case. The later combined accounting suite passed 348 tests with 7 optional skips.
Next: historical accounting guards (completed above).

Existing estimate resolver fallback and malformed-container behavior remain unchanged; this
slice does not add a new resolver or silently repair historical records.

## 2026-10-02 — Hosted older-server client reads

The current bundled client passed a read-only check against the retained older `agent-v1` API
through loopback 38000. Discovery has **no API-major, capability or software-release metadata**;
the client used baseline offset listing and existing event pagination without optional cursors.
One operation page returned three entries with continuation. Each of the three retained Azure,
AWS and GCP create operations matched its expected resource ID, remained `succeeded`, and
returned two matching events with continuation. IDs remain in the handoff's retained table.

Preflight: API healthy; pod `forgeapi-emulator-6498c7fc84-mjh5t` 5/5 Running, zero restarts.
API and engine reported runtime image ID
`sha256:2b946bce45583e0e3b128d94436e67df80711f2cb01e5366098820e8e2150ad6`.
The one-off script was reviewed by Astra before execution and used only GET-based client
methods. No submit/execute, traversal, retry, cleanup or deployment occurred. This proves
compatibility with this observed older hosted runtime, not every historical version. A
repeatable opt-in snippet is in [the compatibility policy](api-versioning.md#observed-older-server-check).
Next: inspect pattern cost validation for a concrete numeric admission defect.

## 2026-10-01 — Finite numeric inputs

A real isolated HTTP probe reproduced four unsupported numeric inputs reaching validation
success, durable acceptance and dispatch: `NaN`, `Infinity`, `-Infinity`, and `1e400` (which
decodes to infinity). All four were persisted as non-finite floats. No Terraform was run.
The shared operation-Intent input validator now uses standard JSON serialization with
`allow_nan=False`, returning valid inputs unchanged. Permanent tests cover nested values,
redacted refusal, no dispatch, normal finite numbers and strings. Legacy models and canonical
finite request representation are unchanged; the temporary diagnostic has been removed.

Review caught an unrelated unknown input masking the new regression. After correcting the
fixture to permit that field, **all 10 rejection cases failed with the guard removed** (200
instead of 422); the exact guard was restored and final Astra review passed. **Full suite:
303 passed, 7 optional Floci skips, 74.04 s**, with two existing dependency warnings. Ruff and
whitespace checks passed. Next: a read-only new-client / retained hosted older-server check,
with no resource changes.

## 2026-10-01 — Publishing action pins

The regression workflow pins external actions, but the publishing workflow still used mutable
major tags. Checkout/login/metadata/build-push are now pinned to verified upstream release
commits within the existing major versions, retaining tag/version checks and manual SHA-only
behavior. **Three focused workflow tests passed**, including a full-commit-reference guard
for both workflows; Ruff, whitespace and final Astra review passed. No Actions run, build or
publication was performed. Next: investigate numeric input handling for a reproducible API defect.
Base-image tags remain mutable; this does not establish byte-for-byte reproducible images.

Verified sources: [checkout v4.2.2](https://github.com/actions/checkout/releases/tag/v4.2.2),
[login v3.7.0](https://github.com/docker/login-action/releases/tag/v3.7.0),
[metadata v5.10.0](https://github.com/docker/metadata-action/releases/tag/v5.10.0), and
[build-push v6.19.2](https://github.com/docker/build-push-action/releases/tag/v6.19.2).
Full commits selected: `11bd71901bbe5b1630ceea73d27597364c9af683`,
`c94ce9fb468520275223c153574b00df6fe4bcc9`,
`c299e40c65443455700f0fdfc63efafe5b349051`, and
`10e90e3645eae34f1e60eeb005ba3a3d33f178e8`, respectively.


## 2026-10-01 — Request-boundary compatibility

Added root/v1 proof for malformed JSON, wrong body shapes, unknown fields and invalid
headers/digests/queries. Rejected submit/execute requests record one refusal, preserve existing
operation/resource counts and dispatch queues, and keep submitted secret values out of responses
and ledger bytes. Validation-only and read requests leave audit/state unchanged. The valid-shaped
digest case verifies conflict on a queued operation, not a saved-plan digest mismatch.

**Evidence:** **32 focused tests passed**, Ruff/whitespace clean, final Astra review passed
after adding the execution audit-redaction assertion. No production defect emerged; this slice
changes tests only. **Combined suite: 287 passed, 7 optional Floci skips, 73.52 s**, including
client event pagination; two existing dependency warnings remain. Next: pin publishing actions
to verified upstream release commits, preserving the current major versions.

## 2026-10-01 — Client event pagination

`Client.events(operation_id, after=0, limit=20)` and CLI `events --after --limit` now expose
the server's existing event pages. Argument bounds are checked before HTTP, returned events
must match the operation and advance the sequence, and continuation must match the last item.
Older discovery and unknown audit action/outcome strings remain readable; raw extra fields
are preserved. There is one explicit page fetch, no traversal/polling/retry or server change.

**Evidence:** **47 client tests passed**, including real HTTP root/v1 traversal with appended
events, maximum/empty positions, malformed pages, invalid arguments without HTTP, old discovery
and CLI forwarding. Ruff, whitespace checks and final Astra review passed. Combined verification
will follow the next tests-only slice; the last full suite was 236 passed with 7 optional skips.
Next: request-boundary compatibility and refusal audit/redaction proof.

## 2026-10-01 — Release-tag consistency

The image workflow now checks pushed tags before registry login: the ref must exactly match
`v` plus the project version, already tested against runtime/discovery. It reads the ref from
the environment rather than interpolating it into shell code. Manual builds remain SHA-only,
including dispatches on a tag. The required regression gate is preserved.

**Evidence:** release-workflow and versioning checks **8 passed**; tests execute the actual
inline guard with matching, mismatched and nonversion refs, and check ordering/failure behavior.
Final Astra review and repository Ruff passed. **Combined suite: 236 passed, 7 optional Floci
skips, 70.83 s**, with two existing dependency warnings. Includes the pagination-bound fix.
No Actions run, publication or deployment was performed. Next: explicit client event-page controls.

## 2026-10-01 — Pagination position validation

Both operation `offset` and event `after` now validate the inclusive range
`0..9223372036854775807` before SQLite binding, for root and `/v1`. Larger values previously
raised an uncaught overflow; they now receive the existing structured 422 envelope. This
fixes formerly unserviceable requests; valid positions retain their behavior. Legacy is unchanged.

**Evidence:** four regressions reproduced `OverflowError` before the fix. Afterward,
**33 focused tests passed**, including normal, maximum and oversized positions for both routes.
Ruff, whitespace checks and final Astra review passed. The canonical OpenAPI snapshot adds
only the exact integer maximum to the two parameters. The last combined suite predates this
fix (231 passed, 7 optional skips). Next: release-tag/version consistency.

## 2026-10-01 — Client operation listing

The client now exposes one operation page through `Client.operations` and the CLI.
Cursor requests must require the advertised stable-pagination capability; baseline listing
and offset remain usable with older discovery and responses. No automatic traversal or retry
is added. Root/server pagination and the release gate are complete locally.

`operations --limit 20 [--offset N | --before OPERATION_ID]` validates its arguments before a
page request, uses discovery's same-origin link and validates each returned operation. Raw
response fields are preserved. Older pages may omit `next_before`. Missing/false capability
refuses a requested cursor without a page call; it never silently changes pagination modes.

**Focused checks:** **28 passed**, covering real HTTP root/versioned links, an insertion
between pages, older discovery/responses, capability refusals, invalid arguments/items and CLI
forwarding. Ruff and whitespace checks passed. Final Astra review found no issues.
**Combined suite: 231 passed, 7 optional Floci skips, 70.66 s**, with two existing dependency
warnings. This includes the release-workflow regression. Next: reject oversized operation/event pagination positions before SQLite
binding; they currently raise an overflow instead of a structured validation error.

## 2026-10-01 — Release regression gate

The existing image workflow publishes tag/manual builds without running the regression suite.
The workflow now adds reusable read-only checks for pull requests, main-branch pushes and
the image workflow, with publication depending on their success. It uses the existing runtime
toolchain and default local tests; no cloud credentials, Floci services, publication or workflow
dispatch is involved in local verification. GitHub-hosted execution remains unverified.

`.github/workflows/check.yml` installs Python 3.12, uv 0.12.21 and Terraform 1.15.9 with its
wrapper disabled. Newly introduced actions are pinned to verified upstream release commits.
It runs `uv sync --locked`, then frozen Ruff/pytest, rejecting stale locks instead of silently
resolving dependencies different from the image. Checkout does not persist its credential.
The existing tag/manual image triggers are unchanged; the publishing job depends on the shared
check and is the only job granted package-write permission. Checks inherit no secrets.

**Checks:** local locked sync passed (59 resolved, 57 checked); the workflow policy regression
passed; repository Ruff and whitespace checks passed. The regression guards required checks,
conditional/continue-on-error bypasses, permissions and triggers. Final Astra review found no
remaining issues. No Actions dispatch, image publication, commit or push was performed.

Verified upstream tags: checkout v4.2.2 (`11bd71901bbe5b1630ceea73d27597364c9af683`), setup-uv
v10.1.0 (`bec219d24cd3e171d82865faccec33120bb574f4`), setup-terraform v4.0.0
(`5e8dbf3c6d9deaf4193ca7a8fb23f2ac83bb6c85`). The next slice is client operation listing.

## 2026-10-01 — Stable operation pagination

Current descending row-ID offset pages can repeat entries if new operations arrive between
reads. The API now adds opt-in `before=<operation_id>` traversal, a nullable
`next_before` response field and a discovery capability, preserving existing offset behavior.
Anchor lookup uses the same caller visibility filter as page lookup. This is stable traversal,
not a frozen state snapshot. No endpoint, schema migration or dependency is added.

Existing offset requests retain ordering, `next_offset` and errors. Cursor continuation uses
`rowid < anchor` internally and returns `next_offset: null`; missing and invisible anchors
share the existing 404 envelope. Malformed IDs and nonzero-offset combinations receive 422.
First/offset pages also provide `next_before` when another page exists. Operation updates do
not reorder traversal, and both aliases preserve their existing link families. Older response
bodies still parse without the optional field. No client/CLI changes were included in this slice.

**Focused checks:** pagination, versioning, operations and policy tests: **36 passed**, with
two existing dependency warnings. Ruff clean. Astra reviewed the visibility queries and
continuation behavior; the OpenAPI diff against the pre-change snapshot contains exactly two
additions: optional `before` and optional nullable `next_before`. Existing required fields,
defaults and routes are unchanged.

**Final combined checks:** **217 passed, 7 skipped** (optional local Floci), **70.40 s**, with
two existing test-client dependency warnings. Repository Ruff and whitespace checks passed.
This includes all three replay fixtures and stable pagination. No deployment, commit or push.

After this slice, add a regression gate to the existing image release workflow: it currently
publishes images from tag/manual runs without lint or tests. No workflow execution, image
publication, commit or push is authorized by this work.

## 2026-10-01 — Apply and failure replay coverage

The engineer explicitly requested continuous iteration across milestones. The current slice
added captured completed-apply and controlled activity-failure histories to the existing plan
replay baseline. Captures use the real Temporal server and production workflow with unchanged
timeouts/retry settings. The failure source is test-only; it exercises the real uncertainty
writer and is not a new worker-kill or cloud-failure claim. The successful apply used the normal
worker and actual local-file Terraform. The failure activity claimed the accepted operation,
raised a fixed safe application error, and let the production workflow record uncertainty.

The new fixtures contain 11 and 17 actual server events. Only host identities, sticky queue
names and the failure stack's local path prefix are normalized, as documented in provenance.
The parametrized test checks phase, activity order/outcomes, normal 600/1800/30-second timeouts,
one execution attempt and complete histories before replay. **3 focused tests passed; Ruff
clean; final Astra review had no findings.** Sol implemented with Ponytail full. Temporary
capture code was removed. No production workflow changed; no pre-versioning, released older
worker, actual timeout history or hosted upgrade is claimed. Stable pagination is next.

## 2026-10-01 — Quiesced backup/restore verification

`tests/test_backup_restore.py` uses the real local Temporal dev server with its persistent
SQLite database, the normal worker, HTTP ASGI application and local-file Terraform pattern.
It first creates/applies a resource and plans an update. With the update still unexecuted,
it closes the client, worker and Temporal server, copies the complete temporary data directory,
renames the original aside and restores solely from that copy at the same absolute paths.
An offline hash/symlink inventory proves restored file equality, including the operation ledger,
Temporal database, Terraform state and pending binary plan. The original digest is preserved.

A fresh server and worker read the restored data. Completed workflow run IDs, results and
complete event hashes survive, as do operation/resource identities and audit rows.
Identical request retries add no new
acceptance, and competing work is refused while the update reserves the resource. Explicitly
executing the original digest changes the local file once. Actual Terraform commands after
restore are only `apply` and `output`: no init, replan or force-unlock. Duplicate execution
returns the completed operation without another apply. No runtime code, endpoint or backup
utility was added.

**Focused checks:** the new test passes; focused Ruff is clean. Astra reviewed the whole-tree
restore, writer shutdown, preserved identities and once-only execution proof with no blockers.
Sol implemented with Ponytail full. Final refinements explicitly assert the Terraform state
file and local output belong to the snapshot and compare complete Temporal event histories.

**Final combined verification:** full `uv run pytest -q`: **211 passed, 7 skipped** (optional
local Floci), **72.01 s**, with two existing test-client dependency deprecation warnings.
Repository Ruff, local documentation links and diff whitespace checks passed. Final Astra
review of the strengthened state/history assertions found no remaining issues.

**Limits:** a quiesced single-host restore at the same paths, configuration and toolchain, with
no execution/external changes after capture. The local-file resource is inside the copied tree;
this does not restore cloud/emulator state or prove live backups, cross-host portability,
arbitrary version changes, stale-snapshot recovery or external production Temporal recovery.
Restoring older records after later applies can erase execution evidence and requires operator
reconciliation. The deployment brief now makes the all-writers stop and these limits explicit.

Read-only preflight: the existing placement API reports `agent-v1` healthy; the placement pod
is 5/5 running with zero restarts, and its latest listed storage-consumption destroy operation
remains succeeded. Retained demos and `.local/data/` are outside the test.

**Next bounded task:** extend captured workflow replay coverage to completed apply and failure
paths before changing workflow behavior. Operator reconciliation automation, real work identity
and distributed storage remain separate milestones. No deployment, commit or push.

## 2026-10-01 — Acceptance/dispatch crash recovery verification

`tests/test_dispatch_crash.py` starts a real TCP API subprocess, normal Temporal worker,
isolated SQLite ledger and local-file Terraform pattern. A test-only dispatcher hook pauses
after durable acceptance and before dispatch. The test SIGKILLs the API, then restarts it
against the same data with normal dispatch. Before retry, the operation remains queued, its
resource rejects a different-key intent, and Temporal still has no workflow for it.

Retrying the identical key/body returns the same operation/resource IDs and completes one
planning activity with one attempt. Acceptance is recorded once, and changed-body key reuse
still returns 409. No apply workflow is started. The test does not add an outbox, automatic
startup dispatch, retry engine or recovery endpoint; accepted work in this window still needs
the caller's original request. Test marker publication is atomic and cleanup covers owned
process groups even if their parent has already exited.

**Focused checks:** **1 passed in 1.98 s**; Ruff clean. Sol implemented Astra's scoped plan
and review corrections. This is real local HTTP/process/SQLite/Temporal/Terraform evidence,
not a hosted crash or cloud recovery test. Production code and existing resources were untouched.

**Final combined verification:** full `uv run pytest -q`: **210 passed, 7 skipped** (optional
local Floci), **80.46 s**, with two existing test-client dependency deprecation warnings.
Repository Ruff and diff whitespace checks passed. This run includes the final client guards,
both interruption scenarios, the recorded planning replay and acceptance/dispatch crash test.
Final Astra review found no remaining code findings. No deployment, commit or push.

**Next bounded task:** coherent quiesced single-host backup/restore verification for the ledger,
Temporal history and protected Terraform plans/state, using temporary local fixtures only.
Do not infer that stopping the API stops all writers. Uncertain-result reconciliation,
real work executor identity and distributed storage remain separate milestones.

## 2026-10-01 — Current planning workflow replay baseline

`tests/fixtures/recovery/plan_history.json` contains an actual completed planning workflow
captured through `/v1/operations`, the normal Temporal worker and the local-file pattern.
All 11 events are retained; host-specific identity fields and the sticky task queue name are
normalized. Payloads contain only the opaque operation ID, phase and boolean/null results.
The fixture contains no Terraform plan, state, inputs or credentials.

`tests/test_workflow_replay.py` asserts completion, planning phase, exactly one phase activity,
the production 600-second timeout and one attempt, then replays through Temporal's `Replayer`.
**Focused verification: 1 passed; Ruff clean.** The replay itself starts no server or Terraform.
This is a baseline captured from the current milestone, not pre-versioning or released-worker
history. Apply/failure paths need their own history coverage before changing those paths.
No runtime workflow changed. Astra reviewed the fixture and requested the completion/shape
guards; Sol implemented them. Temporary capture code was removed.

## 2026-10-01 — Worker interruption verification

`tests/test_worker_interruption.py` now covers two real Temporal/Terraform scenarios using
`tests/interruption_worker.py` and the local-only `examples/interrupted-apply/` gate. One
SIGKILLs the worker after a real side effect starts, confirms Terraform survived, and starts a
replacement worker. The other leaves the worker alive and records its actual late success
write after the timeout. Both retain `uncertain`, reject execution/replacement work, show
exactly one Terraform plan and apply, and preserve one uncertainty event with no success event.

A test-only activity interceptor asserts the production 30-minute apply timeout and schedules
five seconds instead. Real Temporal history records a start-to-close timeout and one activity
attempt. Production workflows, activity implementations and retry policies are unchanged.
The gate's finish marker proves local-exec continued after release; it does not prove Terraform
successfully committed state after the worker died. An initial time-skipping attempt reached
the gate and killed the worker, but hit its clock-advance deadline with an activity in flight;
the final tests use the real local dev server and accelerated timeout instead.

**Checks:** both focused scenarios passed in **21.41 s**; focused Ruff passed. Astra reviewed
the final atomic late-write receipt and audit assertions with no remaining findings. Sol
implemented; Ponytail full was applied. Temporary worker groups and fixture paths are owned
and cleaned by the tests. Existing cloud/emulator resources and protected local state were
untouched. No image publication, deployment, commit or push.

**Limits:** local process and accelerated-timeout evidence, not a full 30-minute wall-clock or
hosted-cloud failure test. Uncertain outcomes still require manual reconciliation. The work
brief now explains preserving identities/evidence and inspecting surviving Terraform processes
before any recovery; no unlock, ledger edit, automatic retry or uncertain-clearing endpoint was
added. A current planning-history replay baseline and acceptance/dispatch crash verification
are the next bounded checks; coherent backup/restore remains separate.

## 2026-10-01 — Thin agent HTTP client

**Scope:** the engineer asked to continue improving the API. The next documented milestone
was a real consumer of the versioned contract. `app/client.py` now exposes `Client` and a
JSON CLI for discover, describe, validate, submit, status, execute and events. It uses Python's
standard-library HTTP transport and the existing contract models; no runtime dependency,
server endpoint, ledger format or workflow was added.

Submission requires the caller's body and idempotency key. Execution requires an operation ID
and the digest of the inspected plan; status/events do not mutate. The client does not generate
keys, persist inputs/tokens, automatically execute plans or retry mutations. A transport failure
after a mutating request is explicitly ambiguous. A server `dispatch_unconfirmed` response
retains safe recovery identity so the caller can repeat the original request.

Discovery bootstraps through `/agent` and follows advertised links. Missing capability metadata
permits the original baseline; explicitly disabled features and unfamiliar execution instructions
are refused. URLs require HTTPS outside loopback, stay on the configured origin, contain no
embedded credentials, and cannot redirect. Inherited proxy configuration is disabled. Caller
bearer tokens come only from an importable argument or the runtime client-only
`FORGEAPI_CLIENT_TOKEN` variable, with sanitized diagnostics and no credential persistence.

**Evidence:** the client talks over an ephemeral loopback TCP socket to the actual FastAPI app.
The test discovers/describes/validates, submits twice with one key, runs a real local-file
Terraform plan, rejects an incorrect digest, queues duplicate execution requests once, applies
once and independently reads the resulting file. Events and late execution replay are checked.
This focused client test uses the existing recording dispatcher to run real activities; it is
not a new hosted or Temporal integration run. Existing Temporal tests remain in the full suite.

**Checks:** full `uv run pytest -q`: **205 passed, 7 skipped** (optional local Floci),
45.73 s, with two existing test-client dependency deprecation warnings. Repository Ruff,
documentation links and diff whitespace checks passed. The initial concurrent test run loaded
the client before its final transport fix and caught four error-handling failures; the saved-code
rerun above passed.
After the final `503` error-body timeout guard and strict recovery-ID assertion, all **15 focused
client tests passed**. Final Astra review has no remaining findings.

**Limits:** a thin HTTP consumer, not autonomous infrastructure decision-making. Polling,
review and same-request retry remain the caller's responsibility. No new model SDK, auth flow,
distributed storage, hosted rollout or production-readiness claim. Existing state and retained
emulator resources were preserved. Astra planned/reviewed and Sol implemented with Ponytail
full. No commit or push.

**Next task:** controlled interruption and recovery verification, including process loss during
execution, conservative uncertainty and late-result fencing. Preserve the no-reapply/no-replan
contract and record actual infrastructure evidence. Work executor identity and distributed
storage remain separate, conditional milestones.

## 2026-10-01 — Operation API v1 compatibility

**Scope:** the engineer selected compatibility for the new operation API only. The older
`/deployments` application and its state are unchanged and outside this guarantee.

**Implemented:** `/v1` business routes and `/v1/openapi.json`, with existing root routes bound
to the same v1 handlers and ledger. Root OpenAPI paths and operation IDs remain available.
Links, Location and dispatch-ambiguity status URLs follow the requested route family;
`/healthz` remains unversioned. Discovery now advertises API major, application release and
capabilities. Typed discovery, pattern, validation, events and error schemas complete the
published contract. The canonical OpenAPI snapshot makes future contract changes reviewable.

The original default-expanded intent fingerprint is preserved across route aliases. Adding
intent fields requires explicit canonicalization rather than silently changing old hashes or
dropping new instructions. Requests still reject unknown fields. The
[compatibility policy](api-versioning.md) defines additive changes, baseline fallback when
discovery metadata is absent, capability checks before using optional features, safe refusal
on unknown execution states/actions, and separate API, application and pattern versioning.

**Upgrade evidence:** old-discovery/client fixtures exercise advertised links, capability
gating before mutation and unfamiliar state/action rejection. These are fixture tests, not a
claim of running a released older server binary. Historical SQLite schema, rows and the literal
request fingerprint were captured from the pre-versioning working tree. Tests restore that
populated database before opening it with current code, retry its request through both route
families without new acceptance, and execute a real temporary saved Terraform plan exactly
once with no replanning. Binary plans/state are not committed; the plan is generated with the
unchanged helper and installed toolchain before the simulated upgrade boundary.
Workflow code is unchanged. A recorded pre-upgrade Temporal history fixture was not captured;
historical replay is not claimed. Existing real Temporal integration tests remain the evidence
for asynchronous dispatch and worker replacement.

**Checks:** final full `uv run pytest -q`: **191 passed, 7 skipped** (optional local Floci),
44.52 s. After the fixture-loading correction, the two focused upgrade tests also passed.
Repository Ruff and diff whitespace checks passed. The suite emitted two existing test-client
dependency deprecation warnings.

**Limits:** local verification only; no image publication, hosted rollout, work deployment,
legacy migration, arbitrary downgrade or mixed-version worker guarantee. Pending plans still
require a compatible Terraform/provider toolchain. Existing hosted examples and protected
state were untouched. GPT-6 Astra planned and reviewed; GPT-6 Sol implemented; Ponytail full
was applied. No commit or push.

**Next task:** an actual agent HTTP client using discovery links and capabilities, stable intent
keys and explicit exact-plan execution. Hosted rollout, interruption/recovery and work executor
identity remain separate milestones; distributed storage remains conditional on hosting needs.

## 2026-10-01 — Hosted storage consumption complete

Added opt-in `--consume-objects` to `deploy/emulator/verify.py`, using only Python's standard
library and the existing placement API. It persists safe exact request bodies, idempotency
keys, operation/resource IDs, plan digests, statuses, lengths and hashes incrementally.
Ambiguous API responses retry the identical request within a bounded window; ownership
changes, uncertain operations, mismatched bytes or failed object cleanup stop mutation.
The API, worker, Terraform patterns and deployed image were not changed for this milestone.

**Real remote evidence:** 2026-10-01 03:36–03:39 UTC, desktop → loopback SSH tunnel →
`forgeapi-placement` on `k3s-server-01`. Each provider created fresh storage through the API,
uploaded **1,068 mixed text/binary bytes directly to its emulator**, independently read and
compared exact bytes/length/SHA-256, deleted the object and observed 404, then executed a
reviewed destroy plan through the API and observed infrastructure 404. All six operations
succeeded. Azure additionally confirmed private container presence and final absence.

| Cloud | Create operation | Destroy operation | Object upload/read/delete/absence |
| --- | --- | --- | --- |
| Azure | `op_3f2be3d6e8384ad1ac77addcacadbe41` | `op_16d92266bb2d41d78416392b782c435f` | 201 / 200 / 202 / 404 |
| AWS | `op_017282f6a3564c0b95e430977842ea90` | `op_780b214d8d94406ea6105de5b92d0958` | 200 / 200 / 204 / 404 |
| GCP | `op_06b3f65cda3641e5a6c865a4f8714d85` | `op_e33eba7a92ff47f7a44c43ee200120c4` | 200 / 200 / 204 / 404 |

Matching upload/read SHA-256 values:

- Azure: `8e2e071eaae44a5c2dc71926f32b336ac0c2072fe355b3362b929cc9499ff881`
- AWS: `3ca066bbd20e2e4b2a5a972f928a846b593ecfaac4f9a9ea04d707e4b9e1a7c1`
- GCP: `eb0752c210688438e84f44a4de8026b4931f5529217c1f41bcfa1e1804954757`

Evidence (gitignored): `.local/pve-placement/evidence/consumption-20260930.json` contains
resource IDs, safe request identities and proof. `consumption-baseline.json` and
`consumption-retained-after.json` confirm every retained placement example still returned
200, with unchanged operation/resource identities; exactly the six expected operations were
added. Existing demos and `.local/data/` were preserved. The placement pod was 5/5 ready with
zero restarts; runtime API/engine image ID was
`sha256:2b946bce45583e0e3b128d94436e67df80711f2cb01e5366098820e8e2150ad6`.
The disconnected placement tunnel was restored; no rollout or cloud identity was needed.

**Checks:** full `uv run pytest -q` before final verifier hardening: **173 passed, 7 skipped**
(optional local Floci tests), 43.64 s. Final verifier and documentation checks: **21 passed**
(18 verifier, 3 docs). The hosted run above independently exercised all three real emulators.
Ruff and diff whitespace checks passed. GPT-6 Astra planned
and reviewed; GPT-6 Sol implemented the verifier/tests with Ponytail full. No commit or push.

**Limits:** this proves object bytes against the pinned emulators, not real-cloud authorization,
storage feature parity, emulator persistence, production hosting or work-Mac behavior.
**Next task:** an actual agent HTTP client against the existing contract; interruption/recovery
and work executor identity remain separate milestones. Distributed ledger/plan storage is
conditional on a distributed hosting requirement. Stop here for this milestone.

## 2026-09-30 — New-session handoff and next milestone

The dated [session handoff](session-handoff.md) records the uncommitted agent-first redesign,
single-host Temporal safety contract, protected and retained demo resources, actual verification
evidence, source map, and implementation-ready storage-consumption acceptance criteria. No code,
live resource, identity or deployment changed for this documentation handoff. The last verified
placement demo remains the one described below; a new session must check its health again.
Handoff verification: `tests/test_docs.py` **3 passed**; edited-doc local links resolved; `git diff --check` clean.

**Next task:** provision new Azure/AWS/GCP storage through the hosted placement API, independently
upload/read/compare/delete object bytes through each Floci data plane, and then review and execute
destroy plans through the API for only those new resources. Preserve accepted operation/resource
IDs and safe evidence even on partial failure. Actual agent client and interruption tests follow
in separate milestones; remote ledger/plan storage is conditional on distributed hosting. This
supersedes older controller and distributed-storage-as-immediate-next notes.

## 2026-09-30 — Multi-cloud placement and storage patterns

**Engineer direction:** proceed with the work simulation; first pattern family is storage across
Azure, AWS and GCP. The API and Terraform execution remain on `pve-desktop`; this desktop is
the HTTP client.

**Implemented:** optional catalog `cloud` selector and environment `targets` for Azure
`subscription_id`, AWS `aws_account_id`, or GCP `project_id`, each with fixed `region`.
Required Terraform inputs are platform-injected and omitted from the caller schema. Overrides
are refused. Targets are recorded privately; execution/update/destroy reject a moved target or
changed catalog cloud. Output values containing any target ID are withheld. Existing
subscription-only mappings remain supported; the HTTP lifecycle and Temporal workflows are unchanged.
AWS's pattern uses `allowed_account_ids` against emulated STS; an incorrect account fails planning.
This is an account guard, not role assumption or real-cloud credential selection.

**Storage fixtures:** Azure Storage account + private `data` blob container + resource group;
AWS S3 bucket; GCP storage bucket. They are isolated root modules under `examples/floci-placed-*`,
registered as tagged local pattern repositories by the demo bootstrap. Existing fixtures and
retained resources remain unchanged. Mapping example: `deploy/emulator-placement/targets.example.yaml`.

**Deployment:** isolated namespace `forgeapi-placement` on existing `k3s-server-01`, single pod
pinned to that guest on `pve-desktop`. API port **38000**, Temporal UI **38233** through SSH.
Local data `/var/lib/forgeapi-placement`; image `forgeapi:floci-placement`, ID
`d49f9197c9dde522993824549fa25737b7aae2346c05ff0c6a5dff0dca78a40a`. Kustomize overlay reuses the
existing emulator deployment. Original demo remains at 28000 with its own state and resources.
No VM/database provisioning, live cloud mutation, state migration, registry push or commit.

**Verification to date:**

- Full suite before expanding Azure from a resource group to Storage: **167 passed**, 173.27 s,
  including placed/unplaced three-cloud lifecycles and legacy regressions.
- Final Azure Storage account/blob container lifecycle with independent ARM + data readback:
  **1 passed**, 95.43 s.
- Wrong AWS executor account: **1 passed**, 7.42 s; planning failed and no bucket existed.
- Cloud placement and docs consistency: **12 passed**, 2.87 s; includes caller isolation,
  override denial, hidden outputs, missing-target refusal and mapping-move refusal.
- Emulator router refuses unrelated destinations and CONNECT tunnels: **4 passed**, 2.06 s.
- Remote deployment: **5/5 running**, no restarts; approximately **484 MiB** memory during startup.

**Emulator findings and fixes:** Floci-AZ's ARM container URL returned 200 for a nonexistent
container. The fixture now uses its supported data-plane container path and verifies both
account and container independently. Storage metadata returns standard Azure DNS names, causing
the first apply to time out before routing was configured. An emulator-only loopback HTTP router
sends only matching Storage hostnames to Floci, preserving Host and rejecting unrelated targets;
it logs no requests/credentials and has no CONNECT support. The failed test's account and resource
group were explicitly cleaned up; the original retained demo resource was untouched. Kustomize
validation also caught a base/overlay nesting and namespace patch mismatch, fixed before rollout.

**Known limits:** trusted pattern authors must wire injected inputs correctly; the API validates
declarations rather than interpreting provider semantics. Region is fixed per target in this
milestone. Reconfiguration does not revoke already dispatched activities. AWS/GCP real workload
identity, cloud authorization, storage service parity and production hosting are not proven.
Emulator cloud state remains disposable; operation/state disks persist. A changed target blocks
pending execution and mutation until the mapping is restored or explicitly migrated.

**Next task:** exercise storage consumption (write/read/delete objects) against the hosted
contract. An actual agent client follows in a separate milestone, then the work executor
identity model. No automatic retries,
new scheduler, MCP server, or home-lab infrastructure provider is part of this milestone.

### Remote storage lifecycle evidence

Verified from this desktop through the SSH tunnel to the new server API. All three providers
passed override rejection, private target schema, duplicate calls, exact-plan execution,
withheld placement outputs, accepted-before-execution events, create readback and destroy.
Azure independently checked both the storage account and its private blob container.

| Cloud | Create operation | Destroy operation | Independent readback |
| --- | --- | --- | --- |
| azure | `op_ebe454fc74be4a48a52b2bb5a449675b` | `op_95b39ea1349e489e80f221edf197f552` | 404 → 200 → 404 |
| aws | `op_9e5182a46ca245b4964ebddcb88f7be6` | `op_b1d40e8fa2984c06871f079c1b79c72a` | 404 → 200 → 404 |
| gcp | `op_c7dbff9fcaf749b5b06913861a224e71` | `op_5bc9919176754ebc97a99895358ddb39` | 404 → 200 → 404 |

Evidence: `.local/pve-placement/evidence/lifecycle.json`. Final documentation check: **3 passed**;
Ruff and diff whitespace checks clean. Temporary desktop AWS/GCP containers stopped; both remote
API stacks remain running. The earlier desktop Azure resource remains intact.

### Retained storage examples

| Cloud | Storage name | Successful operation |
| --- | --- | --- |
| azure | `forgeazure97e662fb4de5` | `op_97b86d4dad2e44beb5e7f576b38adac1` |
| aws | `forgeawsb27ca40e14bf` | `op_51f6f38e75664557a94bf8a65f68f1f0` |
| gcp | `forgegcpff8eea71a68b` | `op_37cb74de7fa04d0fa3991806007476aa` |

Azure's account includes private container `data`. Independent provider readback returned 200
for all retained resources. Evidence: `.local/pve-placement/evidence/retained.json`.
Final server state: 5/5 containers running, zero restarts; persistent data approximately 1.2 GB.
API and Temporal UI remain accessible over the open desktop SSH tunnel. No commit or push.

## 2026-09-30 — Work cloud simulation hosted on pve-desktop

**Engineer correction:** this is a work infrastructure API for Azure, AWS and GCP. The home
lab is its hosting location; the engineer's desktop is the HTTP client. The earlier idea of
provisioning a PostgreSQL VM was abandoned before creating any VM, database or credential.

**Delivered:** API → Temporal → Terraform → Floci Azure/AWS/GCP on the existing
`k3s-server-01` guest (`10.0.20.10`, VM 200) on `pve-desktop`. Namespace/deployment
`forgeapi-emulator`; one pod pinned to that node, five containers. Image
`forgeapi:floci-work-demo`, ID `aadce860ba6094986ce9f3d78408ccfa102037625b5e478544db0a92562fd6a1`,
built from this uncommitted working tree and imported directly. No registry push or ArgoCD
change. Deployment/bootstrap/client scripts are under `deploy/emulator/`.

**Desktop access:** SSH + Kubernetes port-forward. API `http://127.0.0.1:28000/docs`;
Temporal UI `http://127.0.0.1:28233`. API binds pod loopback, no Service or Ingress, ingress-deny
NetworkPolicy, no service-account token or cloud credentials. Reconnection command is in
`deploy/emulator/README.md`. The API/worker share `/var/lib/forgeapi-emulator` (0700, UID 1000);
emulators use separate disposable volumes. No Terraform or worker is needed on the caller desktop.

**Implementation:** added a GCP storage-bucket example (Google provider 7.36.0, explicit dummy
access token and local storage endpoint), Floci GCP 0.9.0 pinned by digest, and expanded the
real lifecycle test matrix/credential environment cleanup. Core API/Temporal behavior did not
need a provider-specific route or workflow.

| Remote lifecycle, called from this desktop | Create operation | Destroy operation | Independent readback |
| --- | --- | --- | --- |
| azure | `op_74f42c9fe76d469a9812ab21bbc54c37` | `op_22ce53828399476280b82cf2426709ce` | 404 → 200 → 404 |
| aws | `op_dd7bb5c9f5be40f2aaf60802220e7db1` | `op_1bf6d1392bb24474b28443c8ba94e1d2` | 404 → 200 → 404 |
| gcp | `op_5d570792c8fe46b2b54d88644d7590d2` | `op_57e7223b59a449d79d1501fa07258ba7` | 404 → 200 → 404 |

Remote verification used real HTTP through the tunnel and standard-library client
`deploy/emulator/verify.py`: validation, asynchronous 202, duplicate intent and execute calls,
saved-plan digest, outputs, ordered audit, and independent readback. Evidence is gitignored
at `.local/pve-floci/evidence/lifecycle.json`.

| Additional verification | Result |
| --- | --- |
| Local real Temporal/Terraform Floci matrix | **3 passed**, 66.02 s; Azure/AWS/GCP create + destroy |
| Documentation consistency + Temporal regressions | **7 passed**, 2.25 s; two upstream deprecation warnings |
| Remote rollout | **5/5 running**, zero restarts after the startup fix |
| Remote resource usage during verification | approximately **566 MiB** memory |
| Ruff | clean |

**Startup defect found and fixed:** running emulator images as UID 1000 without writable
`/app/data` caused Azure TLS-certificate and AWS storage initialization failures. Separate
`emptyDir` mounts with the pod group fix those permissions. Retried the deployment before
any cloud operations. The image and runtime state were preserved.

**Limits:** one emulated pattern per provider; no real-cloud IAM/RBAC or provider-parity proof.
Temporal is a development service. Cloud emulator state is disposable; ForgeAPI ledger,
Temporal history and Terraform state persist. After emulator state loss, inspect and replan
using the existing resource ID. The earlier desktop-only Azure demo at port 18000 is untouched;
temporary desktop AWS/GCP test containers were stopped. No real cloud changes, project commit
or push.

**Next task:** exercise the work team's actual cloud patterns and placement/identity contracts
against this server-hosted simulation. AWS account and GCP project policy/real executor identity
still need design and verification; current tenant placement is Azure-oriented. Distributed
storage is a later hosting requirement if work needs independent replicas, not a prerequisite
for this single-host emulator setup.

### Resources retained for inspection

Created with the same remote API and independently read back as HTTP 200:

| Cloud emulator | Resource name | Successful operation |
| --- | --- | --- |
| azure | `forge-work-azure-7fd9cf2134` | `op_171da5938157439ca5e53eef16607c3f` |
| aws | `forge-work-aws-41b5e92b4f` | `op_20d7d0eab062438a879da2a0f7f62d17` |
| gcp | `forge-work-gcp-05821f6b59` | `op_0642cbcfe0c64899822fc19e1aa4518a` |

Evidence: `.local/pve-floci/evidence/retained.json`. These are emulator resources only.
The server stack and desktop SSH tunnel are left running.

## 2026-09-30 — Retained Azure Floci demo and local storage proof

**Engineer request:** proceed locally, explain storage needs, and create something in the Azure emulator.

**Done:** started three isolated containers (`forgeapi-floci-demo-azure`, `forgeapi-floci-demo-engine`, `forgeapi-floci-demo-api`) with no real-cloud credentials. Created **`rg-forgeapi-agent-demo` in `eastus`** through real HTTP acceptance → Temporal → Terraform saved-plan execution. The fixture is its own local git repo/tag, copied from `examples/floci-azure`, registered only in the ignored demo catalog. Ports are loopback-only: API **18000**, Temporal UI **18233**, Azure emulator **4577**. Containers and resource are left running for inspection.

**Storage:** `.local/floci-demo/data` is a persistent host directory mounted at `/data` for both API and engine, owned by UID/GID 1000. It contains the ledger/audit, Temporal development history, Terraform state/plans, receipts and provider cache. Usage after the run: **227 MB**. `.local/floci-demo/azure` is a separate emulator mount (84 KB). The disk had approximately 515 GB available. All demo artifacts are gitignored; existing `.local/data` and live cloud state were untouched.

| Check | Evidence |
| --- | --- |
| Initial acceptance and exact-plan apply | `op_bf1f32702efa4d82910dd86ed037eab3` reached `succeeded`; `202` responses preceded completion; plan contained one `azurerm_resource_group.test` create. |
| Independent emulator readback | HTTP `404` before apply; HTTP `200` after, group name/location matched and `provisioningState=Succeeded`. |
| Duplicate calls | Same intent key returned the same operation; repeating execute returned `succeeded`; no additional activities. |
| API, engine and emulator restart | Ledger survived. Original Temporal plan/apply histories remained readable and complete with one activity each. **Floci-AZ 0.13.0 lost the ARM resource group despite WAL configuration: GET returned 404.** This is an observed emulator persistence limitation. |
| Restore after emulator state loss | Fresh plan/execute `op_25efe6c357d2464a904b5da328490db6` on the same resource `res_59dec822d8b1423584bab54cf9e08d4f` reached `succeeded`; independent emulator GET returned 200 again. No replay of the original plan. |
| Final Temporal history check | Both operations' plan/apply histories complete, one scheduled activity per phase. |
| Docs consistency | `uv run pytest tests/test_docs.py -q`: **3 passed**; `git diff --check` clean. |

Current operation: [localhost:18000/operations/op_25efe6c357d2464a904b5da328490db6](http://localhost:18000/operations/op_25efe6c357d2464a904b5da328490db6). Raw non-secret demo evidence is under `.local/floci-demo/evidence/`, with the restored run under `restore/` and the restart summary in `restart-check.json`. README, work deployment brief and work prompt document storage and the restart finding together. No project commit/push or real-cloud mutation.

**Known limit:** a successful historical operation does not continuously verify cloud existence. Keep this emulator running for inspection; if it restarts, inspect/replan against the existing resource ID. Do not claim ARM resource persistence from Floci's global storage mode. The next architecture milestone remains shared transactional operation storage and durable exact-plan artifacts for distributed hosting; this demo did not implement that migration.

## 2026-09-30 — Temporal selected for the asynchronous operation API

**Engineer direction:** “go with temporal.” This supersedes the custom-controller runtime described in the preceding implementation entry below. The solution remains an HTTP/OpenAPI API with Terraform; no MCP server.

**Changed:** removed the polling controller. The API commits an accepted operation and audit event, then starts a Temporal phase workflow with a stable ID. It returns `202` after dispatch acknowledgment, before Terraform completion. Planning and explicit saved-plan execution have separate deterministic workflows. Python activities claim only the specified operation/phase, execute the pinned Terraform plan, and record safe results. Both Terraform phases have `maximum_attempts=1`; only idempotent status recording retries. The worker retains the old workflow types for legacy regression compatibility and runs at most four activities concurrently.

**Failure behavior:** concurrent/repeated calls reuse the same operation and workflow. A lost/failed dispatch response returns `503 dispatch_unconfirmed` with operation ID, status URL and `retry_same_request`; accepted work remains recorded. There is no outbox: a crash between acceptance and dispatch requires the caller to retry the same request. Activity failure/loss can become `uncertain`, keeping the resource blocked. Late activity results cannot overwrite a recorded timeout. Worker startup does not mark unrelated running operations interrupted. Plan timeout is 10 minutes; apply timeout is 30 minutes. Temporal timeouts do not terminate a still-running Terraform subprocess; reconciliation remains manual.

**Runtime:** Temporal is a normal dependency again. Native startup uses `app.devserver`, `app.worker`, and `app.main:app`. Docker/compose use API and engine roles; the engine runs a dev server plus worker, or worker-only when an external Temporal address is supplied. Native and packaged dev servers persist history in `DATA_DIR/temporal.db`. These are development servers. API and worker still need the same durable local ledger/plan disk; separate stateless Container Apps and Azure Table remain unsupported for the new operation ledger. Work deployment brief, prompt, architecture and README were updated together.

| Evidence | Result |
| --- | --- |
| `uv run pytest --floci -q` | **PASS: 153 tests**, 98.81 seconds, two upstream deprecation warnings. |
| `uv run pytest tests/test_temporal_operations.py -q` after adding the final failure regression | **PASS: 4 tests**, including one additional test beyond the full-suite run. |
| Real Temporal asynchronous acceptance | **PASS:** `202` with no worker running; no Terraform workspace exists yet; starting the worker produces a real plan. |
| Worker replacement and duplicates | **PASS:** first worker stopped after planning; execution accepted while no worker exists; replacement worker applies once. Completed workflow IDs cannot be restarted; history verifies one activity scheduled per phase with retries disabled. |
| Dispatch uncertainty | **PASS:** before-start failure and lost acknowledgment after successful dispatch; retries retain operation/workflow IDs. Both plan and apply paths tested. |
| Controlled Temporal activity failure | **PASS:** one attempt only; real workflow/status activity records uncertainty and refuses replacement work. Late completion fencing is also unit-tested. This is controlled failure evidence, not a process-kill/timeout test. |
| AWS Floci 2.1.0 / AWS provider 6.14.1 | **PASS:** HTTP → real Temporal → real Terraform S3 plan/apply/destroy, independent absence/presence/absence readback, repeated request checks. |
| Azure Floci 0.13.0 / AzureRM provider 4.65.0 | **PASS:** same lifecycle for a resource group using emulator TLS and managed identity. |
| Packaged runtime restart | **PASS:** image `ad30b3ad29ba`, isolated local-file operation `op_bb6785cbb3d34f8a8def4342195b2e94`. Real uvicorn + engine processes; stop engine after planning, execute returns `503`, restart engine, retry succeeds. Persisted plan history survives, and plan/apply histories each contain one activity. No real cloud resources. |
| Final documentation, discovery/OpenAPI and Temporal regression check | **PASS: 9 tests**, 2.25 seconds. Local image refreshed as `7ba180975fc9` after description/docstring updates; the runtime restart proof above used `ad30b3ad29ba`. |
| `uv run ruff check .`; `git diff --check` | **PASS** |

The image smoke initially needed fixes to its temporary test harness (import path and catalog YAML shape); neither failure involved Terraform execution. Emulator containers were stopped after verification. Existing local state and live cloud resources were untouched. Nothing committed, pushed or deployed to the work environment.

**Next milestone:** shared transactional operation storage and durable exact-plan artifacts for distributed hosting, with explicit migration of existing resource/state ownership. Do not restore a custom scheduler or add GitHub Actions/MCP to this runtime.

## 2026-09-30 — Agent-first HTTP architecture replacement

**Engineer direction:** replace the existing architecture entirely, center it on agents calling an API, and verify infrastructure behavior against AWS and Azure Floci. The engineer explicitly rejected an MCP server. There is no MCP implementation or dependency in the delivered solution.

**Built:** `app.main:app` now exposes the agent-v1 HTTP/OpenAPI contract: discovery, pattern schema, intent validation, idempotent operation acceptance, operation/event pagination, and explicit execution by saved-plan digest. A transactional SQLite operation ledger replaces Temporal dispatch for the default runtime. The separate `app.controller` plans once, waits for execution, and applies the exact saved plan once. Create/update/destroy use stable resource IDs. Duplicate submissions and execute calls cannot duplicate accepted work. Interrupted or failed execution becomes `uncertain` and blocks the resource; no automatic apply retries, force-unlock or replanning.

**Preserved:** tenant placement and current permission checks, injected-input protection, cost reservations, append-only accepted/refused audit, external git-tagged patterns pinned to commits, existing identity paths and sensitive-output withholding. Configured subscription IDs in outputs are withheld too. New logs persist command/exit receipts rather than raw provider/auth diagnostics. Legacy deployment databases and live Terraform state were neither migrated nor deleted. Old API routes live at `app.legacy:app`; old tests explicitly exercise that compatibility module. The default Docker image and compose configuration run the new API/controller without Temporal.

**Findings fixed during verification:** cold concurrent catalog requests could observe an unfinished git checkout; the cache now uses a per-commit file lock and completion marker. Reading legacy budgets through the old store could race its lazy schema migration (`duplicate column name: version`); the new policy reads existing legacy cost data in read-only mode and never creates or migrates that database. Admissions reserve cost transactionally, including pending plans, and simultaneous over-budget requests correctly accept only one.

| Evidence | Result |
| --- | --- |
| `uv run pytest --floci -q` | **PASS: 150 tests**, 97.37 seconds. Two upstream Starlette/httpx deprecation warnings. |
| `uv run ruff check .`; `git diff --check` | **PASS** |
| Floci AWS **2.1.0**, Terraform AWS provider **6.14.1** | **PASS:** HTTP validation/intent → real Terraform S3 plan → confirm bucket absent → execute → independent readback → duplicate-call checks → destroy plan/execute → independent 404. |
| Floci Azure **0.13.0**, Terraform AzureRM provider **4.65.0** | **PASS:** same lifecycle for a resource group, with TLS and emulated managed identity. No Azure client secret. |
| Idempotency/concurrency | **PASS:** same-key acceptance once, changed-body conflict, execute-once under concurrent calls, atomic budget admission, cold catalog cache concurrency. |
| Failure/safety | **PASS:** moved-tag refusal, altered-plan refusal, saved-plan restart survival, interrupted/apply-error uncertainty without replay, tenant isolation and revoked execution rights, audit failure refusing acceptance, append-only audit triggers, secret/subscription output withholding. |
| OpenAPI | **PASS:** typed intent and operation responses, state enum, required idempotency header, unknown fields rejected. |
| `docker build -t forgeapi:agent-redesign .` | **PASS:** image `a881b7637649` built locally, not published. |
| Disposable image smoke with real HTTP API + controller processes | **PASS:** local-file operation `op_0f8827261fe44042921016b239e15c84` reached `succeeded`, file content matched, duplicate acceptance/execution remained the same operation. Neither `mcp` nor `temporalio` is installed in the runtime image. |

Floci images are pinned by digest in `compose.floci.yaml`; Terraform fixtures are under `examples/floci-aws` and `examples/floci-azure`. Tests create temporary tagged pattern repos and an isolated catalog/data directory, scrub inherited cloud identity variables, and perform actual emulator HTTP readback. The lab catalog was not changed. Temporary emulator containers were stopped after verification. No real Azure or AWS resources were created, changed or deleted. Nothing committed or pushed.

**Current limits:** one host with durable local disk and one controller. SQLite on network shares, the old Azure Table operation store and separate stateless Container Apps are unsupported. No automated reconciliation of uncertain operations, cancellation of unexecuted plans, or pattern-version migration. Existing hosted resources continue to use the previous release pending a verified migration. No new-runtime deployment to real AWS/Azure or the work Mac is claimed. Provider identity paths were retained, but emulator tests do not prove real-cloud authorization, networking, billing or parity.

**Next milestone:** a transactional remote operation ledger, resource fencing, durable exact-plan storage and explicit state migration before distributed work hosting. `docs/agent-architecture.md`, README, the work brief and work prompt describe the implemented architecture and these limits; old deployment guides are retained under `docs/archive-temporal/`.

## 2026-09-20 — Rewrite: M0–M5 built and run on the home lab

The engineer chose to build ahead on the home lab rather than gate each milestone on the work Mac. Nothing below has been run on the work machine.

**Changed:** Go implementation, compose stack, Makefile, CI and scripts removed on branch `fastapi-rewrite`; Go-era docs moved to `docs/archive-go/`. New Python app (~540 lines in `app/`):

| Milestone | Behavior |
| --- | --- |
| M0 | uv project, FastAPI, `GET /healthz` |
| M1 | `POST /deployments` (202), `GET /deployments/{id}`; SQLite; per-pattern input validation, unknown fields rejected |
| M2 | One Temporal workflow per deployment (`DeployWorkflow`: plan → apply, `mark_failed` on error). `python -m app.devserver` runs the dev server with no CLI install |
| M3 | Real Terraform per deployment workspace under `.local/data/deployments/<id>/`; saved plan then apply; outputs stored; `GET /deployments/{id}/logs` |
| M4 | `key-vault` pattern (unchanged `main.tf`); tenant/subscription/RG/location/identity are server settings, never caller input; 503 when unconfigured |
| M5 | `FORGEAPI_AUTH_MODE=entra`: RS256 signature via tenant JWKS, issuer, audience, expiry; 401 without echoing token or error. Default `none` |

**Evidence (home-lab Linux, Python 3.12, Terraform 1.15.9, temporalio 1.33.0):**

| Check | Result |
| --- | --- |
| `uv run pytest` | PASS: 23 passed. Includes real dev-server + worker + Terraform success and failure runs, and `terraform validate` of both patterns |
| `uv run ruff check .` | PASS |
| Live three-process run, `local-file` | PASS: `accepted → planning → succeeded`; Terraform-written file content matched; Temporal UI 200 on :8233 |
| Live three-process run, `key-vault` | PASS: `dep_8746b23034224f578f3673faef382770` `accepted → planning → applying → succeeded` in ~27 s; created `kv-forgeapi-py-7f75` in `forgeapitestRG01` |
| Independent readback `az keyvault show -n kv-forgeapi-py-7f75` (human CLI identity, not the executor) | PASS: `Succeeded`, standard SKU, RBAC on, public network access `Disabled`, expected tags |
| Terraform log scanned for certificate/key material | PASS: none |

**Identity:** reused the existing dedicated app registration `ForgeAPI Lab Terraform` (Key Vault Contributor on the one RG only). Engineer-approved new seven-day certificate appended; **expires 2026-09-27 20:56 UTC**. Private key generated locally, only in `.local/executor/client.pfx` (0600, gitignored). The expired 2026-09-07 certificate entry and `.local/lab-executor/` evidence were left intact.

**Live resources now in `forgeapitestRG01`:** `kv-forgeapi-0907-a8c2` (Go spike, state under `.local/deployments/`), `kv-forgeapi-py-7f75` (this run, state under `.local/data/deployments/dep_8746…/work/`), `uami-forgeapi-terraform-01`. Both vaults have `prevent_destroy`, are empty and cost nothing. There is no delete endpoint; removal is a manual, separately approved step.

**Not verified / known limits:**

- Nothing run on the work Mac. M5 tested with a locally generated signing key only; no real Entra token has been validated by this code.
- Dev server history is in-memory: a deployment in flight when the dev server stops stays in its last state. API/worker restarts alone are survived by Temporal but this was not exercised.
- Activities run once (no retries) by design; a transient Azure error fails the deployment.
- No destroy/update, idempotency, cancellation, ownership/authorization, or tracing — all deliberately parked.

**Next task options (engineer to pick):** run M0–M3 on the work Mac; validate M5 with a real Entra token; or add `DELETE /deployments/{id}` (terraform destroy) so demos clean up after themselves.

## 2026-09-20 — Docker packaging

`Dockerfile` (uv Python 3.12 + Terraform 1.15.9 from the official image, runs as uid 1000) and `compose.yaml` (Temporal dev server, worker, API; loopback-only ports). `.local/data` is bind-mounted, so Docker and native runs share the database and Terraform state. The executor certificate is mounted read-only into the worker only.

| Check | Result |
| --- | --- |
| `docker compose up --build -d --wait` | PASS: all three services up, Temporal healthy |
| `local-file` deployment via `curl localhost:8000` | PASS: `succeeded`; file written on host as the host user |
| Earlier native deployment `dep_8746…` read through the containerized API | PASS: shared state visible |
| Certificate visible in worker, absent in API container | PASS |

**Not verified:** a `key-vault` deployment through the Docker stack (would create a third vault; not run). Certificate readability and settings inside the worker were checked; Azure sign-in from the container was not.

## 2026-09-20 — Patterns from git tags, remote state

**Engineer decisions:** patterns live only in their own GitHub repos (tags = versions); only composite, runnable root-module patterns are exposed; state in a dedicated storage account; executor gets Contributor + RBAC Administrator on the lab subscription.

**Changed:** `app/patterns.py` and `patterns/` removed. `app/catalog.py` + `patterns.yaml`: tags via `git ls-remote`, tag→commit pinned at acceptance, input schema parsed from the pattern's `variable` blocks at that commit (cache keyed by commit). Worker runs `terraform init -from-module=git::…?ref=<commit>`, then `init` with per-deployment azurerm backend config. `GET /patterns`, `GET /patterns/{name}`; `version` + `commit` on deployments; Terraform's own error text in `error`. Docker image has git; token passed at run time.

**Azure/GitHub changes made:**

- Storage account `stforgeapitf6c68` (forgeapitestRG01): shared-key off, no public blobs, TLS 1.2, versioning + 14-day soft delete; container `tfstate`.
- Executor SP roles added: Contributor and Role Based Access Control Administrator on the subscription; Storage Blob Data Contributor on the `tfstate` container only.
- `AzSkyLab/terraform-azurerm-resource-group`: added `pattern/` (naming + resource group, own provider/backend), tagged **v1.1.0**.

**Evidence:**

| Check | Result |
| --- | --- |
| `uv run pytest` | PASS: 27. Real git repos with tags: newest-first versions, annotated tag → commit, per-version schema, moved tag still runs accepted commit, Terraform validation message surfaced, missing state settings fail clearly |
| Live `GET /patterns/key-vault` and `/resource-group` (private GitHub repos) | PASS natively and from the Docker API container |
| Live `resource-group` v1.1.0 deployment `dep_47d92300a5724c62a820eb68079e9493` | PASS: `succeeded`, commit `44c8607` recorded, created `rg-forgeapi-resource-group-dev` (centralus). ~3.5 min, mostly first-run azurerm provider registration |
| `az group show` (human identity) | PASS: Succeeded, pattern tags present |
| Remote state | PASS: log shows azurerm backend configured; `terraform state list` as the executor reads it back; no state file in the workspace |
| Token leakage | PASS: no token in compose logs; backend-config args not logged |

**Graph permissions (engineer-approved, done):** executor app has application permissions `Group.Create` and `User.Read.All`, admin-consented; both app-role assignments verified via Graph.

**Pending, engineer to run:** the first `key-vault` deployment. The assistant's attempt was blocked by the tool permission policy (IaC apply). Command is in the session notes / README curl form. There is still no destroy endpoint, so a partial failure leaves resources behind.

**Not verified:** `key-vault` deployment; any deployment through the Docker worker against Azure; the `csGIT34/...` module sources inside key-vault `pattern/` work only via GitHub's redirect.

**Live resources:** `rg-forgeapi-resource-group-dev` (remote state), `kv-forgeapi-py-7f75` (local state in `.local/data`), `kv-forgeapi-0907-a8c2` (Go spike), `stforgeapitf6c68`, `uami-forgeapi-terraform-01`.

**Next:** `DELETE /deployments/{id}` (terraform destroy from remote state); then key-vault once Graph consent exists; repoint key-vault `pattern/` sources to AzSkyLab and tag.

**Default version pin:** `default_version` in `patterns.yaml` sets the tag used when a request names none (explicit `version` still wins; a pin that does not exist is a 422). Shown as `default_version` on `GET /patterns/{name}`. Tests: 29 passing. Compose build race fixed (only `api` builds the shared image).

## 2026-09-20 — Input discovery

**Why:** callers could see names/types but not allowed values, what a pattern is for, or an example; bad values only failed minutes later in Terraform.

**Changed:** `app/schema.py`. `GET /patterns/{name}` now returns `about` (the pattern repo's `config.yaml` at that tag), per-input allowed values/ranges/regex with the author's `error_message`, and a generated `example`. `GET /patterns/{name}/schema` returns JSON Schema. Validation moved from generated Pydantic models to that schema (one source of truth); lifted rules are enforced at POST with the author's message, all problems reported together, submitted values never echoed. `POST /deployments?dry_run=true` validates only. Zero per-pattern code. Executor app Graph permissions granted (engineer-approved).

| Check | Result |
| --- | --- |
| `uv run pytest` | PASS: 44. Lifting tested on the exact condition strings from the real repos; `||`, ternaries, other-variable and invalid-regex rules are not lifted; operators inside string literals handled; an unliftable rule still fails in Terraform with its message |
| Live against private `key-vault` v1.1.3 (Docker API) | PASS: `about` from the repo's `config.yaml`; `environment`/`sku_name` allowed values, `tier` 1–4, `owners` minItems 1; example body generated |
| Live dry run with `environment=staging`, `owners=[]`, `tier=9` | PASS: one 422 listing all three with the pattern's own messages; nothing created |

**Finding for the pattern repo:** key-vault's `environment` description says "(dev, staging, prod)" but its rule allows `prototype, dev, tst, stg, prd`. The API now shows the real list; the description should be fixed in the pattern.

**Still pending:** first `key-vault` deployment (engineer to run); `DELETE /deployments/{id}`.

## 2026-09-20 — First key-vault composite run: partial failure (resolved below)

Pattern fix merged and tagged in the pattern repo (`AzSkyLab/terraform-azurerm-key-vault` PR #1, **v1.1.4**); the API served v1.1.4 as latest with no API change.

`dep_ad66998a7b284b40b942a9f158609a06` (key-vault v1.1.4, commit `4756f452`, **through the Docker worker**): `planning → applying → failed` after ~3.5 min.

- **Proved:** Docker worker → private GitHub fetch at pinned commit → azurerm remote state → Azure and Entra with the certificate identity.
- **Failure:** `azuread_group` created both groups, then Graph returned 403 `Authorization_RequestDenied` reading the groups' owners. `Group.Create` + `User.Read.All` is not enough for the azuread provider to read back group owners; it also needs `Group.Read.All` (or `Group.ReadWrite.All` instead of `Group.Create`). Terraform's error text reached the deployment's `error` field intact.
- **Left behind (all recorded in remote state `deployments/dep_ad66998a….tfstate`, verified with `terraform state list`):** `rg-forgeapi-keyvault-dev`, vault `kv-forgeapi-lab2d-d` (purge protection off), the executor's Secrets Officer role assignment, Entra groups `sg-forgeapi-dev-secrets-readers` and `sg-forgeapi-dev-secrets-admins`. Not created: the two group RBAC assignments.
- **No API path to recover:** there is no retry or destroy endpoint. State is intact, so either is possible once built.

**Next:** engineer decision on the extra Graph permission; build `DELETE /deployments/{id}` (destroy) and a retry so partial failures are recoverable through the API.

## 2026-09-20 — Retry and destroy; key-vault composite finished

**Changed:** `POST /deployments/{id}/retry` (failed only; same pinned commit, inputs and state, so Terraform finishes what is missing) and `DELETE /deployments/{id}` (succeeded/failed only; `DestroyWorkflow` runs `terraform destroy` from the deployment's state; record kept, states `destroying → destroyed`). A prepared workspace is reused; otherwise the pattern is re-fetched, which remote state makes safe on any worker. Each run gets its own Temporal workflow ID. Executor app now has Graph `Group.ReadWrite.All` (engineer instruction; `Group.Create` alone failed reading group owners with azuread 2.53.1).

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 48 / clean. Real end-to-end destroy (file removed, `Destroy complete`), real fail-then-retry, 409/404 guards |
| Retry of `dep_ad66998a…` through the Docker worker | PASS: `accepted → planning → applying → succeeded` in ~90 s; Terraform replaced the two tainted groups and added the two missing role assignments |
| Independent readback (human identity) | PASS: vault `kv-forgeapi-lab2d-d` Succeeded, RBAC on, purge protection off; three role assignments (executor Secrets Officer, readers group Secrets User, admins group Secrets Officer); exactly two `sg-forgeapi-dev-*` groups (no duplicates) |

**Incident worth remembering:** the first retry failed with "missing or corrupted provider plugins". A native worker left running on the host from an earlier session was polling the same Temporal queue as the Docker worker; one ran `plan`, the other `apply`, and provider-cache symlinks are absolute paths that differ between host and container. Run either the native stack or the Docker stack against one Temporal, never both.

**Live destroy (engineer-approved), through the Docker worker:**

| Check | Result |
| --- | --- |
| `DELETE` key-vault deployment `dep_ad66998a…` | PASS: `destroying → destroyed` in ~60 s; `Destroy complete! Resources: 7 destroyed` (3 role assignments, vault, 2 Entra groups, resource group) |
| `DELETE` resource-group deployment `dep_47d92300…` (created natively, destroyed from Docker via remote state) | PASS: `destroyed` in ~45 s |
| Independent readback (human identity) | PASS: both resource groups gone, zero `sg-forgeapi-dev-*` groups, vault not left soft-deleted |

**Live resources now:** `kv-forgeapi-py-7f75` (pre-catalog run, local state in `.local/data`), `kv-forgeapi-0907-a8c2` (Go spike), `stforgeapitf6c68` (state), `uami-forgeapi-terraform-01`, all in `forgeapitestRG01`.

**Not verified:** anything on the work Mac; M5 with a real Entra token; retry/destroy when the original workspace is gone (re-fetch path is covered only by the fresh-workspace deploy path).

**Next:** run on the work Mac with the work pattern repos; GitHub App token and managed identity when hosted on Container Apps.

## 2026-09-20 — Hosting groundwork (branch `aca-hosting`, nothing created in Azure)

Plan: [hosting-plan.md](hosting-plan.md) — Container Apps, scale-to-zero, managed identity, Table Storage for records, GHCR for the image. Engineer note: work may host its Temporal workers on ACA too, so lab-only shortcuts are marked in the plan.

**Built (steps 1–2 of the plan):**

- `app/db.py` is now a three-function facade over a store; SQLite unchanged and default. `app/db_table.py` stores one entity per deployment in Azure Table Storage with Entra auth (`FORGEAPI_DB_BACKEND=table`).
- `app/azure_identity.py`: managed identity when `FORGEAPI_AZURE_USE_MANAGED_IDENTITY=true`, else the approved certificate, else the default chain. With it set, Terraform gets `ARM_USE_MSI=true` and the backend `use_msi=true`, and the certificate is not handed over.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 57 / clean |
| Store contract (round trip, missing record, outputs kept across later updates, error replaced) against SQLite **and** Table Storage | PASS. Table Storage = Azurite emulator in Docker, started per test session and removed after; visibly skipped if Docker is absent |
| Identity → Terraform env/backend mapping | PASS (unit level) |

**Not verified, cannot be off-Azure:** Table Storage against the real account with Entra auth; Terraform obtaining tokens from a Container Apps managed identity (`IDENTITY_ENDPOINT`, not the VM metadata address). That is the first thing to prove once hosted; fallback is a federated credential trusting the worker's identity.

**Next (needs the engineer):** create the GitHub App for pattern-repo reads (step 3); approve writing and applying the `terraform-pattern-forgeapi-host` pattern (step 4), the first step that creates hosted resources.

## 2026-09-20 — Host pattern written (not applied)

- New private repo `AzSkyLab/terraform-pattern-forgeapi-host`, tag **v0.1.0**: Consumption Container Apps environment (no Log Analytics), `api` (public HTTPS, 0–1 replicas), `worker` (no ingress, 0–1), optional lab `temporal` (internal TCP 7233; omitted when `temporal_address` is given, the work case). System-assigned identities; API → Storage Table Data Contributor only; worker → table, `tfstate` container, and `worker_roles` on `worker_scope`. Optional pattern-repo token via Key Vault reference, never through Terraform state or the API. Terraform ignores `min_replicas` on worker/temporal so the on/off switch is not fought.
- Registered as `forgeapi-host` in `patterns.yaml`. `.github/workflows/image.yml` publishes the image to GHCR on `v*` tags or manual run. `scripts/aca.sh up|down|status`.

| Check | Result |
| --- | --- |
| `terraform fmt` / `init -backend=false` / `validate` on the pattern | PASS |
| `GET /patterns/forgeapi-host` (Docker API, private repo) | PASS: v0.1.0, `about` and costs from its `config.yaml`, 7 required inputs, generated example |
| Dry run with `image=forgeapi:latest` | PASS: 422 with the pattern's own message; nothing created |
| `uv run pytest` | PASS: 57 |

**Not done / not verified:** no `terraform plan` or apply of the host pattern; image never built or pushed (workflow not yet on the default branch or run); `scripts/aca.sh` only syntax-checked; ACA managed-identity token acquisition by Terraform and internal TCP ingress for Temporal remain the two hosting unknowns; Graph permissions for the worker identity are a manual directory-admin step after apply.

**Next (engineer):** push `aca-hosting`, publish an image (tag or manual workflow run, then make the GHCR package public), then approve deploying `forgeapi-host`.

## 2026-09-20 — Image published

`aca-hosting` pushed; tag **v0.1.0** triggered `.github/workflows/image.yml` (run 35542407062, success). Published `ghcr.io/azskylab/forgeapi` with tags `v0.1.0`, `latest`, `sha-5ab4ae7…`. Manual dispatch is not available until the workflow is on the default branch.

**Open:** the package is **private** (org default); anonymous `docker manifest inspect` returns `unauthorized`. GitHub has no API for package visibility, so the engineer must set it to public once in the package settings before Container Apps can pull it without a credential.

## 2026-09-20 — Entra auth set up and verified with a real token

GHCR package made public by the engineer; anonymous `docker manifest inspect` of `ghcr.io/azskylab/forgeapi:v0.1.0` now succeeds.

App registration **ForgeAPI Lab API** (single tenant, no secrets, no certificates): application ID URI `api://<client-id>`, access token version 2, one delegated scope `access_as_user`, Azure CLI pre-authorized so `az account get-access-token --scope api://<client-id>/access_as_user` works without a consent prompt. With v2 tokens `aud` is the client ID, so `FORGEAPI_ENTRA_AUDIENCE` / the pattern's `entra_audience` is the **client ID**, not the URI. IDs are in the engineer's tenant, not in this public repo.

First real-token test of `FORGEAPI_AUTH_MODE=entra` (throwaway local API, no worker):

| Request | Result |
| --- | --- |
| `/healthz`, no token | 200 (open by design) |
| `/patterns`, no token / garbage token | 401 / 401 |
| `/patterns`, valid Entra token for a **different** audience (ARM) | 401 |
| `/patterns`, real token for this API (`ver 2.0`, correct `iss`, `scp access_as_user`) | 200 |
| API log scanned for token or validation detail | none |

Closes the long-standing "M5 tested with a local key only" gap. Any signed-in user in the tenant can obtain this token; per-user authorization is still parked.

Dry run of the final `forgeapi-host` request (with `entra_audience`) is valid. **Pending, engineer to send:** the real POST.

## 2026-09-20 — forgeapi hosted on Container Apps (deployed through its own API)

`dep_a24111c44c6d49b39447762e7be82e73`, pattern `forgeapi-host` v0.1.0 (`3d1abc12`), engineer-approved.

- **First attempt failed in 35 s:** `MissingSubscriptionRegistration` for `Microsoft.App` (subscription had never used Container Apps). Only the resource group was created. Registered the provider (one-time, free), then `POST …/retry` finished the same deployment: `succeeded`. A second retry sent while it was already applying was correctly refused with 409.
- **Created:** `rg-forgeapi-forgeapi-host-dev`, environment `cae-forgeapi-host-dev` (Consumption, no Log Analytics), apps `ca-forgeapi-api-dev` (external), `ca-forgeapi-worker-dev` (no ingress), `ca-forgeapi-temporal-dev` (internal), all min 0 / max 1. Roles verified: API identity → Storage Table Data Contributor only; worker identity → table, `tfstate` blob container, Contributor + RBAC Administrator on the subscription.

| Check against the hosted API | Result |
| --- | --- |
| Public image pull from GHCR without a credential | PASS (app started) |
| `/healthz` from zero replicas | PASS: 200 in 0.29 s |
| `/patterns` without a token / with a real Entra token | PASS: 401 / 200 |
| Deployment lookup = Table Storage through the API's **managed identity** | PASS after propagation: first call 500 `AuthorizationPermissionMismatch` (role assignment seconds old), 404 on the next try ~1 min later; the identity created the `deployments` table itself |

**Proved:** `ManagedIdentityCredential` works inside Container Apps for the Python SDK. **Still unproven:** Terraform (`ARM_USE_MSI`) with the worker's identity, internal TCP to Temporal, `scripts/aca.sh`. Worker and Temporal are still at zero replicas; nothing can deploy from the hosted copy yet.

**Known gap:** the hosted API has no GitHub token, so `/patterns/{name}` for private repos will return 502 until the Key Vault token reference is set up.

**Pattern follow-up:** `forgeapi-host` should tolerate the role-propagation delay better (the API returned a 500 rather than a clear 503), and its README should list `Microsoft.App` registration as a prerequisite.

**Next:** Key Vault + read-only GitHub token reference; `scripts/aca.sh up`; deploy `resource-group` through the hosted API as the Terraform-managed-identity test; Graph permissions for the worker identity.

## 2026-09-20 — Hosted worker proven: update endpoint, federation, full hosted run

**Built:** `PUT /deployments/{id}` (new inputs and/or pattern version, applied against existing state; refused for local-state patterns on a version change); `examples/azure-identity-check` (creates nothing; proves Terraform sign-in + remote state without any repo access); Azure store errors → 503; Terraform sign-in through **workload identity federation** (`FORGEAPI_AZURE_FEDERATED_CLIENT_ID`); `scripts/aca.sh` starts Temporal before the worker. Images `v0.1.1`, `v0.1.2`. Pattern `forgeapi-host` **v0.2.0** (user-assigned worker identity, no deploy rights on it). Tests: 66 passing.

| Check (hosted unless noted) | Result |
| --- | --- |
| `PUT` image bump on the live host deployment (local API → Azure) | PASS: `0 to add, 4 to change, 0 to destroy` |
| `scripts/aca.sh up/status/down` against real apps | PASS |
| `local-file` through the hosted API | PASS: API → Table Storage → Temporal over internal TCP → worker → Terraform, `succeeded` in ~15 s |
| `azure-identity-check` with `ARM_USE_MSI` | **FAIL as feared:** `ManagedIdentityAuthorizer … 169.254.169.254:80: connection refused`. Terraform cannot read a Container Apps managed identity |
| `PUT` to pattern v0.2.0 + image v0.1.2 (version change in place) | PASS: `2 to add, 4 to change, 4 to destroy`; the 4 destroyed are the old worker identity's role assignments, including subscription Contributor and RBAC Administrator |
| Federated credential `forgeapi-worker-lab` on the Terraform app (subject = worker identity) | created (engineer asked for all steps) |
| `azure-identity-check` with federation | **PASS:** `signed_in_object_id` = the Terraform app's service principal, remote state written, no secret anywhere |
| `DELETE` of both hosted test deployments through the hosted API | PASS: `destroyed` |

**Security posture now:** API identity and worker identity can each touch only the deployments table. Deploy rights, state access and Graph permissions exist on exactly one principal (the Terraform app), reachable locally by the short-lived certificate and hosted by federation from one named identity.

**Created outside Terraform:** `Microsoft.App` provider registration; Key Vault `kv-forgeapi-host-38b9` in `forgeapitestRG01` (RBAC, empty) with Secrets Officer for the engineer, to hold a read-only pattern-repo token. Kept out of the host resource group on purpose so destroying the host does not trip over an unmanaged resource.

**Pending, engineer:** create a fine-grained read-only GitHub token and store it in the vault; then `PUT` the host deployment with `github_token_secret_id` + `github_token_key_vault_id`, `aca.sh up`, and deploy `resource-group` through the hosted API (first real pattern from a private repo, hosted). Worker and Temporal are currently **down** (free).

## 2026-09-20 — Last leg: private pattern deployed entirely from Azure

- Engineer stored a fine-grained, read-only, 30-day GitHub token in `kv-forgeapi-host-38b9` (`pattern-repo-token`); the assistant only ever listed the secret's metadata.
- First `PUT` with the Key Vault inputs **failed twice**: deadlock in pattern v0.2.0. The API's identity was system-assigned, so the Key Vault role depended on the app, and the app could not update without reading the secret. Fixed in **v0.2.1**: both identities user-assigned, roles granted first, 90 s propagation wait, apps last. `PUT {"version":"v0.2.1"}` from the failed state: `5 to add, 4 to change, 1 to destroy`, `succeeded`. Worker identity unchanged, so the federation trust still held.

| Check (hosted) | Result |
| --- | --- |
| `GET /patterns/{resource-group,key-vault,forgeapi-host}` | PASS: tags read from the private repos using the Key Vault-referenced token |
| `resource-group` v1.1.0 (`44c86077`) deployed through the hosted API | PASS: `succeeded` in ~65 s. Private repo + private module fetch, federated sign-in, remote state, all from Container Apps |
| `az group show` (human identity) | PASS: `rg-forgeapi-resource-group-dev` Succeeded with pattern tags |
| Token in hosted deployment logs | none |
| `DELETE` through the hosted API | PASS: `destroyed`; resource group gone |
| `aca.sh down` | worker and Temporal at 0 replicas (free) |

**Hosting plan status:** every step done and verified except a GitHub App (the lab uses a Key Vault-held read-only token instead). Open question for work: pattern-repo access without anyone holding a key; see the options given to the engineer (GitHub App owned by the platform team, or publishing pattern release artifacts to Azure storage from GitHub Actions via OIDC so the worker never talks to GitHub).

**Live now:** `rg-forgeapi-forgeapi-host-dev` (environment, 3 apps, 2 identities; idle ≈ $0), `stforgeapitf6c68`, `kv-forgeapi-host-38b9`, plus the two old demo vaults and unused UAMI the engineer still has to delete by hand. Certificate for local runs expires 2026-09-27; the token expires in 30 days.

## 2026-09-20 — Work deployment brief; direct managed identity; GitHub App tokens

Engineer decision: skip local dev at work; deploy with the organisation's MCP server into an existing private ACA environment (Easy Auth, Key Vault per app, MCP-built images). Temporal starts on ACA there too (AKS Temporal later). Wants to reuse an existing user-assigned identity (as used by self-hosted runners). Egress is restricted; a GitHub App or machine credential exists; a state storage account exists.

**Built:** `app/msi_shim.py` (loopback VM-metadata token endpoint so Terraform can use an attached managed identity directly; no app registration or federation needed); `app/github_app.py` (hourly installation tokens; GHES host/API settings); `deploy/temporal/Dockerfile`; **`docs/work-deployment.md`** (task brief for Claude at work: rules, inputs, steps, env vars, verification order, troubleshooting from real lab failures, known limits). Image `v0.1.3`. Tests: 71 passing.

| Check | Result |
| --- | --- |
| Real Terraform (azurerm provider **and** azurerm backend) through the shim, locally, shim backed by the lab certificate identity | PASS: init, plan, apply, remote state, destroy |
| Same inside Container Apps, worker in direct managed-identity mode (federation blanked, temporary Reader + state-blob roles on the worker identity) | PASS: `signed_in_object_id` = the worker's **user-assigned identity** `e7144186…`, remote state written, `succeeded` in ~45 s |
| Cleanup | test deployment destroyed; temporary roles removed (identity back to table + Key Vault only); host `PUT` back to declared config on `v0.1.3` (`0 add, 4 change, 0 destroy`, federation setting restored); worker and Temporal at 0 replicas |

**Not verified:** GitHub App tokens against a real App (unit tests only); anything in the work environment; Easy Auth in front of the API; provider downloads under restricted egress (provider mirror not built); Temporal UI behind Easy Auth.

**Newly documented limit:** the worker must run as exactly one replica, because plan and apply are separate activities sharing a local workspace.

## 2026-09-20 — Business units: placement, injected inputs, sizes, ownership (branch `tenancy`)

Engineer requirement: end users must not know where resources go; the platform maps callers to a business unit and the business unit + environment to a subscription, set up when access is granted. Decisions and rules: [tenancy.md](tenancy.md).

**Built:** `app/tenants.py` (YAML mapping behind functions, to move to a database at work), `app/placement.py`, caller identity with Entra groups from the token, from Easy Auth's `X-MS-CLIENT-PRINCIPAL` (`FORGEAPI_AUTH_MODE=easyauth`) or `FORGEAPI_DEV_GROUPS` locally. Request gains `business_unit` (only when the caller has several), `environment`, `size`. The platform injects BU values, network values, `environment`, the size's values and the BU's default region, but only into variables the pattern declares, and removes those from the caller's schema. `location` is limited to the BU's regions. Terraform runs with the environment's subscription; state stays in the platform subscription. Records carry BU, environment, subscription, size, injected values and requester in both stores. Other BUs get 404; changing a deployment needs the environment's deploy right; `PUT`/`retry` re-read the mapping but a deployment never moves subscription. New: `GET /me`, `GET /deployments`, filtered `GET /patterns`, caller-specific pattern page and schema. Subscription IDs are never returned. Off unless `FORGEAPI_TENANTS_PATH` is set.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 86 / clean. 13 tenancy tests incl. a real Terraform run proving injected values and the target subscription reach Terraform; store contract for the new fields and scoped listing on SQLite **and** Table Storage (Azurite) |
| Bugs the tests caught | subscription leaked as `null` in responses; one Terraform call ran without the target subscription; Table SDK filter parameter parsing |
| Live, local, real `key-vault` v1.1.4 with a lab mapping (nothing deployed) | PASS: `/me` correct; pattern page hides `environment`, `business_unit`, `cost_center`, `sku_name`; dry run shows them injected with `location` = BU default |

**Finding for the pattern repos:** key-vault's `config.yaml` sizes use `dev/staging/prod` but environments are `prototype/dev/tst/stg/prd`, so no size is offered in `stg`/`prd` until the keys match. Patterns also need to declare `private_endpoint_subnet_id` (etc.) to receive network values.

**Not verified:** real group claims from Entra or Easy Auth (unit tests only); two real subscriptions; anything hosted. **Not built:** quotas, prd approvals, per-version limits, admin API/database for the mapping.

## 2026-09-20 — Business units proven end to end on the hosted lab copy

**Setup:** image `v0.2.0`; host pattern **v0.3.0** (`tenants_yaml` → `FORGEAPI_TENANTS_YAML` on the API only; added because the mapping is gitignored and cannot be in a CI-built image); key-vault **v1.1.5** (PR #2: sizing keys `dev/stg/prd`); Entra security groups `forgeapi-lab-platform-devs` (the engineer), `forgeapi-lab-platform-release` (empty on purpose), `forgeapi-lab-hr-devs` (the lab service principal); API app registration `groupMembershipClaims = SecurityGroup`. Host updated in place with `PUT` (`0 add, 4 change, 0 destroy`). One subscription backs every environment in the lab.

| Check (hosted API, real Entra tokens) | Result |
| --- | --- |
| Token issued before group claims were enabled (Azure CLI cache) | no `groups` claim → `/me` empty, no patterns. Correct fail-closed behaviour; a fresh token carried 23 groups, no overage |
| `/me` and `/patterns` as the engineer | `platform`, deploy to `dev` only (not in release group), 4 patterns |
| Refusals | no environment 422; `prd` 403; caller-set `cost_center` 422; `westeurope` 422 with the BU's region message; `business_unit: hr` 403 |
| Real `resource-group` deployment, `dev` | `succeeded`; request contained no BU, cost centre or region. **Azure shows tags `BusinessUnit=platform`, `CostCenter=CC-0001`, location `centralus`** (BU default, not the pattern's `eastus`). Subscription absent from the API response |
| Second real caller: service principal token (app-only, `groups` claim present) | `/me` → `hr`; sees only `resource-group`; lists 0 deployments; GET / logs / DELETE of platform's deployment → **404**; `key-vault` → 403; dry run injects `hr` / `CC-2000` |
| Sizes, key-vault v1.1.5 | page offers `small, medium, large` in `dev` and hides `environment`, `business_unit`, `cost_center`, `sku_name`; no size 422; `xl` 422; size + own `sku_name` 422 |
| Real `key-vault` deployment, `size: small` | `succeeded`; **Azure shows SKU `standard`** and the platform tags |
| Cleanup through the API | both `destroyed`; resource groups and Entra `sg-` groups gone; worker and Temporal at 0 replicas |

**Observations:** Terraform outputs such as resource IDs naturally contain the subscription ID; only the platform's own fields hide it. One status poll during destroy returned a non-JSON body (next poll fine); not investigated. Machine callers work: a service principal in a BU group is a valid caller, which is how pipelines would use the API.

**Not verified:** two genuinely different subscriptions; Easy Auth's header in a real Easy Auth deployment; group overage handling against a real over-limit user; a caller in two BUs with real tokens (unit-tested).

**Left in the lab:** the three `forgeapi-lab-*` Entra groups and the group-claims setting on the API app registration (needed for further demos).

## 2026-09-20 — Merged to main; budgets (branch `quotas-budgets`)

**Housekeeping:** PR #1 merged `tenancy` (which contained `aca-hosting` and `fastapi-rewrite`) into `main`. `main` had one Go-era docs commit from another machine (`dc1ffdd`, Windows WSL handover); it is merged, with its progress notes applied to `docs/archive-go/progress.md`. Go implementation preserved at tag `go-archive`. The three merged branches were deleted after a containment check.

**Budgets (engineer decisions in [tenancy.md](tenancy.md#budgets)):** `app/budgets.py`; `budget_monthly` per BU/environment; cost from the pattern's `estimated_costs` at the pinned commit, stored on the record; committed = everything not `destroyed` (failed counts); over-budget requests refused 403 with numbers; dry run reports impact; `PUT`/`retry` do not double count; unpriced patterns refused where a budget exists; examples declare cost 0. Visible in `/me`, the pattern page and dry runs.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 92 / clean. Budget tests: refusal with exact figures, dry-run refusal and report, failed still counts, destroy frees, no double count on update/retry, unpriced pattern refused only where budgeted, budgets separate per BU and environment; cost field (including a real zero) round-trips on SQLite and Table Storage |

**Not verified:** budgets on the hosted copy or against the real key-vault pattern's `estimated_costs` (unit-level only so far). **Known limits:** estimates are not bills; concurrent requests can overshoot; no counts, per-pattern caps, expiry or actual-spend reporting.

**Also explained to the engineer, not built:** the single-worker-replica limit (plan and apply are separate activities sharing a local workspace). Recommended fix: make apply re-create its workspace and re-plan (small), and store the saved plan in blob storage once approvals exist.

## 2026-09-20 — Budgets proven on the hosted lab copy

Image `v0.3.0`; lab mapping gave `platform/dev` `budget_monthly: 1.5`; host updated in place (`0 add, 4 change, 0 destroy`). Costs came from the **real** key-vault v1.1.5 `config.yaml` (dev: small 1, medium 5, large 15). Real Entra token.

| Check (hosted API) | Result |
| --- | --- |
| `/me` and pattern page | budget 1.5 / committed 0 / available 1.5; cost per size shown |
| `resource-group` (its repo has no `config.yaml`) | 403: declares no estimated cost. **Finding:** that pattern needs `estimated_costs` before it can be used in any budgeted environment |
| key-vault `medium`, dry run | 403 with figures (this request 5, available 1.5) |
| key-vault `small`, dry run | 200, reports cost 1 and budget impact |
| Real key-vault `small` deployment | 202; budget shows committed 1 **while still in flight**; `succeeded` |
| Second `small` | 403 (committed 1, request 1, available 0.5) |
| `PUT {}` on the live deployment | 202 and `succeeded`: its own cost was not counted twice |
| `PUT {"size":"medium"}` | 403: the +4 does not fit |
| `DELETE` | budget stayed committed while `destroying`, freed to 0 once `destroyed`; Azure clean; worker and Temporal back to 0 replicas |

Follow-up from the run: an over-budget **update** reported `committed: 0`, which is correct (it excludes the deployment itself) but reads oddly; the response now also carries `this_deployment_now`. Tests: 92 passing.

## 2026-09-20 — Multi-replica workers, concurrency, hosted logs (branch `multi-worker`)

PR #2 (budgets) merged to `main`; this branch starts from there.

**Goal:** remove the single-worker-replica limit. **Fix:** `terraform.apply` checks for a saved plan made from exactly this source, variables and subscription (a fingerprint written at plan time); if this replica does not hold one, it rebuilds the workspace and re-plans. A saved plan is deleted once applied. This also closed a single-worker hazard: a plan left by a request that never reached apply could have been applied to a later request.

**Two further defects found by proving it on the hosted copy with two replicas and eight simultaneous deployments:**

1. **Terraform's shared provider cache is not safe under concurrency.** First run: 4 of 8 failed (`cached package … does not match`, `text file busy`). One deployment's `init` wrote the cache while another's `plan` executed the same provider. It affects a single worker too; every earlier test ran deployments one at a time. Fix: a reader/writer gate, `init` alone, everything else parallel, writers first. A local test with a cold cache and four parallel deployments failed most runs before and passed 12 of 12 after. (A first attempt at this fix silently did not apply because a text replacement did not match; the test caught it.)
2. **`GET /deployments/{id}/logs` was always empty when hosted**: the API read its own disk while the worker, a different container, wrote the file. The earlier hosted "no token in the logs" checks were therefore vacuous. Fix: `app/logs.py` also writes chunks to a second table (`<table>logs`) in the same storage account, and the API reads from the store.

Also: host pattern **v0.3.1** adds `worker_max_replicas` (scaling the worker by hand to 2 had made the next apply fail with `ContainerAppInvalidScaleSpec`, because Terraform pinned max 1 while ignoring min); `scripts/aca.sh` now changes only the minimum. Images `v0.3.1`, `v0.3.2`.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 97 / clean. New: apply on a replica that did not plan; stale plan never applied; cold-cache concurrency; logs in both stores incl. a 70 KB write |
| Hosted, 2 replicas, image v0.3.1 (before the cache and log fixes) | 4 of 8 failed; logs empty |
| Hosted, 2 fresh replicas (cold caches), image v0.3.2, 8 simultaneous deployments | **8 of 8 succeeded; 4 of 8 had plan and apply on different replicas** and re-planned on the applying one |
| Logs through the hosted API | present (71–131 lines each); scanned 8 real logs for tokens/keys: none |
| Cleanup: 16 destroys at once across 2 replicas | 15 × 202, 1 × 409 (already destroyed); all `destroyed`; budget back to 0; worker and Temporal at 0 replicas |

**Operator error worth recording:** a first cleanup attempt did nothing because zsh does not word-split unquoted variables, so one DELETE went to a garbage URL; the non-JSON reply seen in that run came from that. Two earlier non-JSON replies during status polling remain unexplained; status codes are now captured when polling.

**Still true:** patterns that keep local state need a single worker. In-memory Temporal history. Approvals would want the saved plan stored in blob storage so that what was reviewed is what is applied.

## 2026-09-20 — PR #3 merged; "down" did not actually stop the hosted worker and Temporal

PR #3 (`multi-worker`) merged to `main`; branch deleted after a containment check. 97 tests pass on `main`.

**Defect (cost):** asked whether the lab was off, the assistant found the worker (2 replicas) and Temporal (1) still **running** although `scripts/aca.sh down` had set min replicas to 0 each time. Those apps have no ingress-driven scale rule, so Container Apps never scales them in, and every `down`/`up` update also created a new revision with fresh replicas. Earlier "back to 0 replicas" statements in this log checked the **min-replicas setting, not running replicas**, and were wrong: those two apps most likely ran continuously from the first `up` until now (roughly seven hours, about 0.75 vCPU / 1.5 GiB in total). The API app did scale to zero (it has an HTTP scale rule).

**Fix:** `down` now sets min 0 **and deactivates the active revisions**; `up` activates the latest revision and sets min 1 (Temporal first); `status` reports replicas actually running. Verified with a real cycle: `up` → 1 + 1 running, `down` → 0 / 0 / 0 running.

**Cost:** the Cost Management query was rate-limited (429), and usage takes 8–24 h to appear, so the actual figure is unknown. The Container Apps monthly free grant (180,000 vCPU-s, 360,000 GiB-s) likely covers it: ~7 h × 0.75 vCPU ≈ 19,000 vCPU-s and ≈ 38,000 GiB-s. To be checked in the portal tomorrow.

**Everything in the subscription now:** host RG (environment, 3 apps at 0 running replicas, 2 identities: no charge while stopped); `forgeapitestRG01` (state storage account: pennies; three Key Vaults and one unused identity: no standing charge); another project's `rg-cg26091947f163` (FlexConsumption function app with 0 workers, two storage accounts, App Insights) and the default Log Analytics workspace (pay per GB ingested), none created by this work.

## 2026-09-20 — Audit trail (branch `audit-log`)

PR #4 (`aca.sh down` fix) merged to `main`. Engineer chose the audit log from the list of next features.

**Built:** `app/audit.py` and hooks in every mutating route, in access checks and in the worker. Design, guarantees and limits: [audit.md](audit.md). Accepted actions are recorded before dispatch and fail closed (503, nothing started); refusals, denied access and worker outcomes are recorded best-effort with a logged warning on failure; dry runs are not recorded; sensitive input values are redacted; auditors (mapping `auditors:` groups) read every unit's events and nothing else; `GET /deployments/{id}/events`, `GET /events`.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 109 / clean. 11 audit tests: event content and injected values, redaction of a `sensitive` variable, refusals with the caller-visible reason (permission, invalid input, budget figures), dry runs leave no trace, full lifecycle order, worker success and failure outcomes from real Terraform runs, probing another unit's deployment is filed under its owners and invisible to the prober, auditor scope, fail-closed when the store is down, every `events` route is GET-only; event store contract on SQLite **and** Table Storage (Azurite) |

**Known gaps recorded in audit.md:** storage is not immutable; deployment records still hold sensitive inputs in clear; cross-process ordering within milliseconds; no retention/export. **Not verified:** the hosted copy.

## 2026-09-20 — Audit trail proven on the hosted lab copy

Image `v0.4.0`; lab mapping gained `auditors:` (the hr group, so the lab service principal doubles as an auditor). Events are stored in the `deploymentsevents` table of the state storage account, written by two different containers (API and worker). Two real callers with real Entra tokens.

| Check (hosted) | Result |
| --- | --- |
| Refused create (`prd`, not in the release group) | recorded: 403, environment `prd`, reason exactly as the caller saw it |
| Dry run | 200 and **no event** |
| One deployment's trail, read by its owner | 8 events in order: create accepted (inputs, injected) → worker succeeded → two `deployment.access` refusals by the hr service principal (read, then change attempt; both saw 404) → update accepted (`inputs_changed: true`) → worker succeeded → destroy accepted → worker destroyed |
| `/events` as a business-unit member | own unit's events |
| `/events` as the auditor | sees `platform`'s events although not a member; **still 404 on the deployment itself** |
| Changing events | `DELETE /events` and `PUT …/events` → 405 |
| Shutdown | `aca.sh down`, then `status`: worker 0, Temporal 0 running replicas (API returns to 0 on its own after its idle cooldown) |

**Not verified:** the fail-closed path against a real storage outage (unit-tested only); retention/export; immutability at the storage layer (not built).

## 2026-09-21 — Outputs and secrets (branch `outputs-and-secrets`)

Engineer direction: dev-only API (no prd approvals), no CI on the lab repo; declined expiry, provider mirror, keyless patterns, drift and the mapping database for now. Asked how outputs such as a database connection string get back to callers and whether they need storing.

**Finding:** non-sensitive outputs were already stored and retrievable, but outputs marked `sensitive` were **silently dropped**, so a connection string or password would never have reached anyone.

**Decisions:** secrets live in the pattern's own Key Vault; the API returns references only, no reveal endpoint; consumers (apps, pipelines, humans) read the vault with their own identity. Convention and limits: [outputs.md](outputs.md).

**Built:** `withheld_outputs` (names of sensitive outputs, values never stored, returned, logged or audited) and `secret_references` (Key Vault secret IDs found anywhere in the outputs); `links.events` on deployments. Stored in both record stores.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 110 / clean. Real Terraform run of a pattern with a sensitive output and two reference outputs: name withheld, references surfaced (top-level and nested), the secret value absent from the record, the response and the log; store round-trip incl. outputs kept across later state changes |

**Not verified:** against a real pattern that creates a vault and a secret (`terraform-pattern-web-backend` does, but it deploys paid resources); not on the hosted copy.

## 2026-09-21 — Outputs and secrets proven end to end on the hosted lab copy

PR #6 merged; image `v0.5.0`. Pattern change (engineer-approved): `AzSkyLab/terraform-azurerm-key-vault` PR #3, tag **v1.2.0**: optional `generated_secret_names` creates random secrets in the pattern's own vault and outputs only versionless references. A read-only `terraform plan` first confirmed that looping over a map with sensitive values works.

| Check (hosted API, real Entra token) | Result |
| --- | --- |
| Pattern page | v1.2.0 shows the new input and its description with no API change |
| Real deployment with two generated secrets, `size: small` | `succeeded`; `outputs.secrets` and `secret_references` hold `https://kv-…vault.azure.net/secrets/database-url` and `…/api-key`; `withheld_outputs: []` because the pattern follows the convention |
| Reading the secret as the engineer | joined the pattern's `sg-…-secrets-readers` group as its owner, then `az keyvault secret show --id <reference>`: 32 characters. The value was never printed |
| Does the API ever hold the value? | both real values searched for in 28 KB of API output (deployment, logs, the deployment's events, the unit's events): **0 occurrences** |
| Cleanup | left the readers group; `DELETE` → `destroyed` (vault, secrets, groups, resource group gone); hosted worker and Temporal stopped, running replicas verified |

**Lab quirk, not a platform property:** the "other business unit" caller in this lab is the same service principal Terraform runs as, which the key-vault module makes Secrets Officer on every vault it creates, so it *can* read these secrets. At work the executor identity and API callers are different principals.

**Non-JSON poll responses:** seen twice more in this run, both within a minute or two of updating the hosted API to a new image. A fully instrumented destroy afterwards gave 29 of 29 polls `200` with JSON, so it did not reproduce. Best guess, unconfirmed: requests landing during the API app's revision switchover (single replica). Status codes are now captured when polling.

**Not verified:** `withheld_outputs` against a real pattern in Azure (only in the local end-to-end test, by design: no real pattern here outputs a sensitive value).

## 2026-09-21 — Work deployment brief brought up to date, and tied to the code

The engineer asked whether the work brief had been maintained. It had been updated for business units, Easy Auth and multi-replica workers, but **not** for budgets, the audit trail, outputs and secrets, the three storage tables, delivering the mapping as configuration, the dev-only scope, or the current image; it still said 97 tests and implied one subscription per worker.

**Done:** `docs/work-deployment.md` rewritten against `main` (image `v0.5.0`): "current as of" line; how a request flows now; platform vs target subscriptions; mapping delivered by `FORGEAPI_TENANTS_YAML` (Key Vault reference) on the API app only, with a warning that an unconfigured mapping means no placement, ownership or budgets; verification now checks `/me` budgets, non-empty logs, audit events, hidden platform inputs, injected tags and secret references; ten new troubleshooting rows from real lab failures; updated known limits (identity choice and secret reach, audit storage, stopping apps); and a **pattern repo checklist**, since sizes, costs, injection and secret handling live in the pattern repos.

**Guard against drift:** `tests/test_docs.py` fails if the brief names a setting that does not exist, omits a setting that does, or omits an endpoint. `AGENTS.md` now requires the brief to be updated in the same PR as any change that affects deployment. Tests: 113 passing.

**Not verified:** the brief has never been executed; nothing has been deployed in the work environment.

## 2026-09-21 — One-container mode and recovery from interrupted runs (branch `all-in-one`)

**New facts from the engineer about the work MCP server:** it builds one image and deploys it as **one HTTP app with Easy Auth**; only environment variables and Key Vault references can be set through it; the app has a **system-assigned** identity and anything else about identity is manual; callers are browsers and pipelines/service principals. The three-app layout in the earlier brief cannot be deployed that way.

**Built:**

- `app/allinone.py`, now the image's default command: Temporal dev server + worker + API in one container; exits if any child stops; honours `$PORT`; skips the local Temporal when `FORGEAPI_TEMPORAL_ADDRESS` is set. Image gains the Temporal CLI, a real user entry and a writable `/data`. Compose sets its own Temporal address instead of the image.
- `app/recovery.py`: running jobs beat on their record every 30 s; a sweeper marks in-flight deployments that have not moved for `FORGEAPI_STALE_AFTER_SECONDS` (300) as `failed: interrupted…` with an audit event. `terraform._run` releases a dead run's **state lock** once (`force-unlock`) and repeats the command.
- `docs/work-deployment.md` rewritten for the one-app shape: what the MCP server does vs. the manual steps (roles for the app identity, Easy Auth app registration group claims and pipeline access, minimum replicas 1), the interruption section, and an explicit list of what has never been proven anywhere.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 121 / clean (lock-ID parsing, unlock-once-then-repeat, no unlock loop, sweeper, heartbeat, retry/destroy after interruption, `touch` on both stores; brief-vs-code tests) |
| One container, local: `local-file` deployment | PASS: `succeeded`, audit events and logs present |
| Kill the worker inside the container | PASS: `[allinone] worker exited with -9; stopping the rest`, container exited |
| `docker stop` (SIGTERM, as on scale-in) | PASS: stopped in 1 s |
| **Real interruption against Azure:** one container, real `resource-group` deployment, `docker kill` while `applying`, container restarted | PASS: stuck `applying` → marked `interrupted` ~1 min later (60 s window for the test); `retry` hit `Error acquiring the state lock … state blob is already locked`, released lock `38221561-…`, re-planned, `Apply complete`; audit trail shows create → interrupted (recovery) → retry → succeeded; then `DELETE` → `destroyed` |
| Compose stack after the image change | PASS: deployment `succeeded` |

**Bugs found on the way:** the image's baked-in `FORGEAPI_TEMPORAL_ADDRESS` made the supervisor think an external Temporal was configured; the Temporal binary refuses to start for a bare UID (`$USER set in environment`); `/data` was root-owned for a plain volume.

**Never proven anywhere (also listed in the brief):** Easy Auth's identity header and group claims reaching forgeapi; a system-assigned identity running Terraform (a user-assigned one is proven through the same code path); the one-container image on Container Apps; private endpoints; restricted egress; the MCP server.

## 2026-09-21 — Two apps for the work MCP server: API app + engine app (branch `two-apps`)

Engineer clarified the intended shape: one ACA hosts the **API**, another hosts a **complete Temporal install with its UI plus the worker**, deployed as two apps by the MCP server. The Temporal dev server is for getting started; the organisation's AKS Temporal takes over when the API moves to a real dev environment. The single-container mode from the previous entry is removed.

**Built:** `app/engine.py` + `deploy/engine/Dockerfile` (Temporal server on 7233, Temporal web UI on the app's HTTP port, worker; supervisor exits if any process stops; `FORGEAPI_TEMPORAL_ADDRESS` set → worker only). Root image default back to the API alone. `app/allinone.py` and `deploy/temporal/Dockerfile` removed. Brief rewritten: two apps, per-app roles (the API identity gets **no** Azure deploy rights), the internal-TCP-7233 check as the first thing to verify, engine pinned at one always-on replica, per-app troubleshooting.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 121 / clean (brief-vs-code tests included) |
| Two Docker containers on one network: API image + engine image, shared record store | PASS: `local-file` deployment via the API app ran on the engine's worker → `succeeded`; audit shows API (`create accepted`) and worker (`state succeeded`) writing; Temporal UI answered 200 on the engine's HTTP port |
| Kill the worker inside the engine | PASS: `[engine] worker exited with -9; stopping the rest`, container exited |
| API app with the engine down | PASS: `POST /deployments` → 503 |
| Compose stack after the image change | PASS |

**Finding during the proof:** with each container on its own SQLite the worker could not see the API's record (`'NoneType' object has no attribute 'pattern'` in the worker). Not a hosted concern (Table Storage is shared) but recorded in the brief's troubleshooting as "deployments stay accepted". The worker's error there could be clearer.

**Never proven:** an MCP-deployed app exposing an internal TCP port (the layout depends on it); everything else on the brief's list.

## 2026-09-21 — Tidy-up and the work prompt (branch `tidy`)

- Worker now raises `deployment … is not in this worker's record store; the API and the worker must use the same FORGEAPI_DB_BACKEND and storage settings` instead of `'NoneType' object has no attribute 'pattern'` (found during the two-app proof). Tested. (A first attempt made the helper call itself and hung the whole suite for ten minutes; fixed.)
- **`docs/work-prompt.md`:** the text to paste into an assistant at work, with two fill-in blocks (inputs; today's constraints). It frames the task and hands over to `docs/work-deployment.md`. README and AGENTS.md point at it; AGENTS.md requires both documents to be kept current together.
- Stale note in `hosting-plan.md` about `aca.sh` corrected.

Tests: 122 passing. Lab hosted copy: worker and Temporal at 0 running replicas (unchanged since the last check; not touched by this work).
