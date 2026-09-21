# Review of the M0 design package — findings for the author

**Package reviewed:** `docs/design` (17 files), `docs/adr` (12 ADRs), `docs/design/openapi.yaml`, revision 0.2.0 dated 2026-09-05.  
**Sources checked against:** `docs/combined-build-prompt.md` and `docs/poc-intent-and-requirements.md`; SHA-256 of both matched the package's recorded hashes.  
**Review dates:** 2026-09-06 to 2026-09-07. **Reviewer:** Claude (Claude Code), on behalf of the requesting engineer.  
**Result:** 56 findings kept after verification — 5 high, 35 medium, 16 low; 15 need a decision from the two engineers at M0 signoff. 22 of 27 external claims confirmed, 5 partially correct, none contradicted.

## How to use this document

This is a revision request, not a signoff. For each finding (`RF-nn`) please respond with exactly one of:

- **Fixed** — name the file and section changed.
- **Decision needed** — the finding is marked "Decide at M0" or you judge it belongs to the two engineers: present at most two or three options with one recommendation, in the place indicated, and add it to a new "M0 decisions requested" table in `README.md` (see RF-11 and the table below). Do not decide these unilaterally and do not record acceptance.
- **Disagree** — quote the package text (file:line) that already covers the point or explain why the premise is wrong. Several findings were downgraded by adversarial verification for exactly this reason; the "Considered and dismissed" section shows what was already rejected, so you need not re-argue those.

Constraints that still apply: preserve both source documents byte-for-byte; keep every ADR **Proposed**; do not mark anything run, verified or approved that was not; keep provider fields out of the public contract; and when you rerun `validation.md`, run a real OpenAPI linter (Redocly or Spectral, pinned) in addition to the schema check, and move the checker out of `/tmp` (RF-13, RF-39).

Line numbers refer to the files as of 2026-09-06.

## Verdict

The package is unusually rigorous and honest. Traceability to both sources is real, every external claim survived fetching, and the durable-dispatch and late-effect designs are the strongest part of the work. It is not ready to sign as-is: the authorization model has two holes an M1 implementer cannot fill alone (RF-01, RF-02); several contract-level choices are undefined rather than decided (RF-12 to RF-18); the compute-isolation strategy rests on a Docker request broker that is neither scoped nor scheduled and may not be needed at all (RF-03); the plan omits the deployment that the security and shutdown experiments assume (RF-04); and there is no single place that says what the two engineers are being asked to decide (RF-11). Most fixes are a paragraph or a table. The Temporal findings (RF-06 to RF-08, RF-27, RF-28) deserve one dedicated pass because they sit in the component everything depends on.

The most pervasive weakness is the caveat density (RF-35): every paragraph restates what is unverified, often inside the sentence that makes the recommendation. One evidence-status banner in `README.md` plus a closing "Decision / verified by / fallback" line per recommendation would make the package reviewable in the ninety minutes the README implies.

## Decisions requested at M0 (draft table for README.md)

| Decision | Recommended | Where it lives | Finding | Decide at M0 |
|---|---|---|---|---|
| Override model for ExecutionInput | Template-fixed fields optional and server-filled; allowed_overrides defines what may vary | `openapi.yaml ExecutionInput / ExecutionTemplate, 04-api.md` | RF-12 | Yes |
| Permission set and default grants | Add execution.data.read and catalog.read; Developer and App owner hold both on their own app/environment | `03-domain.md role table` | RF-01 | Yes |
| Grant scope model | Polymorphic scope (BU, team, app-environment, target, catalog, audit) with precedence rules | `10-registry-decisions.md ER, 03-domain.md` | RF-02 | Yes |
| Does the untrusted process need Docker API access? | Determine first; default to supervisor-run template Compose with tests getting network access only, so no broker is built | `ADR-0004, 06-compute-images.md` | RF-03 | Yes |
| Isolation runtime if Docker API access is needed | Compare rootless+broker, sysbox, nested microVM, VM-as-boundary; budget the chosen one as an M2 work package | `ADR-0004, 11-delivery.md` | RF-03 | Yes |
| Timeout semantics | Split platform allocation deadline from workload runtime, or keep one clock and expose started_at and timed_out_from | `ADR-0002, 03-domain.md, openapi.yaml` | RF-17 | Yes |
| Input-artifact intake contract | Choose source_ref {repository, commit} or an authenticated input-artifact publish route; implement in M3 | `03-domain.md, openapi.yaml` | RF-18 | Yes |
| Batch topology and allocation mode for the M2 experiment | Test pool-per-execution and node-per-execution in a governed pool; decide user-subscription versus Batch-service mode with the platform owner | `06-compute-images.md, V05/V07/V08` | RF-19, RF-20 | No |
| Workflow ID reuse and conflict policies | Record exact values and which recovery paths start a new run | `05-temporal-recovery.md` | RF-06 | No |
| M1 gate | Approve M1 now on local fixtures; M1-core versus M1-hardening lists; V02/V03 gate only the connected claim | `README.md, 11-delivery.md, V04` | RF-05, RF-37 | Yes |
| Control-plane hosting | Add an M2.0 work package for ACA apps, identities and mTLS to the development namespace | `11-delivery.md` | RF-04 | Yes |
| API conventions (V01 defaults) | Accept 202-as-operation, lowercase enums, closed inputs, long-poll provisionally under the local profile | `04-api.md review table, ADR-0002` | — | Yes |
| Runtime data interface boundary | Separate ACA app and identity from M3 | `02-architecture.md` | RF-22 | No |
| Working agreement | Approve separately from the architecture; make it tool-neutral; V20 split into P0 tooling and P2 preference | `working-agreement.md, ADR-0012, V20` | RF-38 | No |

## High findings (5)

Resolve or present as decisions before signoff.

### RF-01 — Permissions required by routes are granted to no role: developers cannot read their own logs, results or artifacts, or list templates

*High · Authorization model · **Decide at M0***  
*Found by:* requirements traceability, cross-doc consistency, API developer experience, direct inspection  

**Evidence**

- 03-domain.md:20 — "POC submitter, reader, canceller, template-admin, operator, and auditor map to the corresponding six permissions. Log/result/artifact routes require both execution access and data-read permission for the object's classification."
- 03-domain.md:13 — Developer: "`execution.submit`, `execution.read`, `execution.cancel` on explicitly granted app/environment"
- openapi.yaml:248, 286, 314 — "x-required-permission: execution.read+execution.data.read"
- 04-api.md:28 — GET /execution-templates: "Catalog read in caller scope"; 03-domain.md:16 — only "Template administrator | … plus catalog read"

**Problem.** Three documents use three permission sets. As written, the persona in POC demo steps 2–7 and in the M1 done-criterion ("observe … results") cannot list templates, read logs, read results or download the artifact. The S02 negative-test corpus cannot be written unambiguously.

**Fix.** Enumerate the full permission set in the role table (add execution.data.read and a named template.read/catalog.read), state which roles hold them by default (Developer and App owner on their own app/environment, classification-bounded), restate the mapping as seven-plus permissions, and add an S02 case for read-without-data-read.

**Verification.** Confirmed by grep: the only holder of "catalog read" is the template administrator; execution.data.read appears on three routes and in no role row.

### RF-02 — The registry ER model scopes every grant to one application-environment, but the role table needs team, target, catalog, audit and BU scopes

*High · Authorization model · **Decide at M0***  
*Found by:* requirements traceability, registry & infra, direct inspection  

**Evidence**

- 10-registry-decisions.md:17-18 — "PRINCIPAL ||--o{ GRANT : receives" / "GRANT }o--|| APPLICATION_ENVIRONMENT : scopes"
- 03-domain.md:13 — "unless a team-wide read/cancel grant exists"; :16 — "`template.admin` on assigned catalog"; :17 — "`platform.operate` in registered targets"; :18 — "`audit.read` … in assigned scope"
- combined-build-prompt.md §4 — "Map platform roles and operation-specific permissions to BU, team, application, and environment scopes."

**Problem.** The registry contract and the authorization model describe incompatible data models. Engineer A's M1.2 migrations and Engineer B's authorization checks will diverge on what a scope is, and the operator/auditor/template-admin roles are unimplementable as drawn.

**Fix.** Make Grant.scope polymorphic (BU | team | application_environment | target_registration | catalog | audit_scope), or separate grant tables per scope kind, and state precedence/intersection rules for overlapping grants before M1.2 migrations are written.

**Verification.** Confirmed by inspection: the ER diagram has exactly one scope relation for GRANT.

### RF-03 — The isolation strategy rests on a bespoke Docker-API request broker that is unscoped, unbudgeted, needed for the M2 gate but scheduled for M3, and whose necessity is never established

*High · Compute isolation · **Decide at M0***  
*Found by:* orchestrator hypothesis, compute isolation & security, reviewability, delivery plan  

**Evidence**

- README.md:5 — "The principal unresolved risk is combining the required Docker functionality with enforceable credential, image, and host isolation."
- 06-compute-images.md:52 — "Recommended candidate: a supervised rootless Docker daemon … accessed through a small allowlisted Docker request broker … No claim is made that an off-the-shelf proxy already provides this policy."
- 06-compute-images.md:54 — "A broker must account for streaming/upgrade endpoints and every accepted Compose API operation"
- 12-verification.md:18-19 — V06 default "constrained Docker broker"; V07 (P0, Batch gate) requires "broker bypass checks"
- 11-delivery.md:49-50 — M2: "Batch experiment, images and Docker/isolation tests"; M3: "Selected adapter, supervisor"; no broker work item anywhere
- adr/0004 Alternatives — only "reusable pools" and "direct VMSS"; grep for sysbox, gVisor, Kata, Firecracker, microVM returns nothing

**Problem.** A deny-by-default Docker API policy proxy that must deep-inspect HostConfig on container create, network/volume create, image pulls and hijacked attach/exec streams is weeks of security-critical work, presented as "a small broker". The M2 viability decision is defined to depend on bypass testing of a component the plan does not build until M3. And the decisive question is never asked: 06:52-54 assume the untrusted test process calls the Docker API through the broker, but never justify that against the source's "if needed" wording. If the trusted supervisor starts the template-owned Compose helpers (03-domain.md:115), the untrusted tests may need only network reachability to them, and no broker is required at all.

**Fix.** Before signoff, add to ADR-0004 an explicit determination of whether the untrusted process needs Docker API access for the chosen template, then compare (1) supervisor-run Compose with tests getting network access only, (2) rootless daemon + broker, (3) sysbox, (4) nested microVM, (5) host-root inside the single-use VM with no ambient credentials. If a broker survives, add a named M2 work package with owner, endpoint allowlist, fail-closed test corpus and supply-chain path into the worker image.

**Verification.** Seeded version: 6 votes, 2 stand (high), 4 downgrade (medium, "M1 is fake-only"). The necessity question was then verified separately: the expert lens stands at high; the refuter downgrades to medium, noting the broker is a V07 candidate rather than a committed build. Held at high because it is the README's own principal risk and the M2 gate depends on it.

### RF-04 — Deploying the control plane to ACA is assigned to no milestone or work package

*High · Delivery plan · **Decide at M0***  
*Found by:* delivery plan, direct inspection  

**Evidence**

- 11-delivery.md:93 — "| S03 | … | M1 hosted scope, M2/M3 cloud |"
- 12-verification.md:27 — V15 gated "P1 M1 shutdown", while 11-delivery.md:38 makes M1 laptop-based ("A developer can run API/worker on a laptop against the enterprise development Temporal namespace")
- 07-terraform.md:17 — "Minimal M2 includes only the approved POC resource group/Batch, image, network, artifact and identity dependencies."
- 11-delivery.md:49-50 — M2/M3 rows list identity/network setup, Batch experiment, adapter, artifacts, demo; nothing deploys API/worker revisions or the mTLS path to the dev namespace
- poc-intent-and-requirements.md §4.1 — in scope: "ACA-hosted API and Temporal workflow/activity workers"

**Problem.** V14 (ingress) and V15 (ACA shutdown behaviour) test a deployment nobody is scheduled to create, and S03's "M1 hosted scope" is undefined. The first time the API runs anywhere but a laptop is unplanned.

**Fix.** Add an explicit work package (e.g. "M2.0 control-plane hosting: ACA environment/apps, API and worker revisions, managed identities, mTLS to the development namespace") with an owner and evidence IDs; define or delete "M1 hosted scope"; assign V14/V15 to it.

**Verification.** Adversarially verified: expert stands (high); refuter downgrades (medium), noting 07-terraform.md:11 names an owner ("Terraform/release pipeline") and 11-delivery.md:154 defers live deployment evidence to "later approved cloud setup". Both confirm no milestone, work package or evidence ID schedules the deployment. Confirmed by grep that "hosted scope" occurs only in S03.

### RF-11 — There is no single list of what the two engineers are being asked to decide, the scope of "signoff" is stated three different ways, and the recommended reading path skips the section holding the principal risk

*High · Reviewability · **Decide at M0***  
*Found by:* reviewability (both passes), orchestrator hypothesis  

**Evidence**

- README.md:7 — "neither engineer needs to approve every file individually" vs 11-delivery.md:44 — "Both approve contract/ADRs and M1 scope" vs 10-registry-decisions.md:58 — "When accepting an ADR, record both engineers' decision/date and conditions"
- README.md:7 — reading path: 01-context, working-agreement, 11 (M1 criteria), 12; this never visits 04-api's five convention decisions, 03-domain's transition tables, or 06's Docker/Batch sections
- README.md:40 — "Recommended next approval: M1 only … subject to the API-convention and development identity/Temporal decisions in section 12" (V02/V03 are experiments owned by other teams and scheduled after approval)

**Problem.** Following the README literally, the manager reads about 400 of 2,400 lines and never encounters the API conventions or the Docker broker they are implicitly approving. Decisions are spread across V01–V20, ADR alternative sections, the 04-api review table and section 1 prose, with no place to record acceptance per ADR.

**Fix.** Add a one-page "M0 decisions requested" table to README (decision, recommended option, alternatives, where argued, who decides, blocks M1?), a per-ADR accept/conditional/defer column, and reorder the reading path: 01 → decision list → 06 Docker/Batch sections → 11 M1 stop condition → 12 V01/V04/V20. The table in this review's "Decisions" section is a starting draft.

**Verification.** Two lenses on the seeded version downgraded to low ("README:40 and 11-delivery:44 state the scope"). Both reviewability passes independently rated it high with more specific contradictions. Listed high because the package's purpose is to obtain signoff; it does not affect what gets built.

## Medium findings (35)

Fix before implementation; those marked "Decide at M0" belong in the decisions table because the artifact carrying them is being signed now.

### RF-05 — The M1 completion gate is ambiguous: V04 gates M1 on F13–F18 with no milestone or tier column, "applicable cases" and "local subset" are undefined, and the M1 stop condition never says which of F15–F17 it requires

*Medium · Delivery plan · **Decide at M0***  
*Found by:* delivery plan, direct inspection  

**Evidence**

- 12-verification.md:16 — V04 / P0 / M1 completion: "F01/F03/F11/F13–F18 against real PostgreSQL/Temporal fixture and connected dev namespace subset"
- 12-verification.md:25 — V13 / P1 / M3: "F17, measured recovery time/loss, expired token/role/private DNS restoration, unequal restore-point reconciliation"
- 11-delivery.md:64 — "M1 exercises the fake; M3 repeats applicable cases"; :100 — S10 "M1 local subset, M3 live" (neither "applicable" nor "local subset" is defined; the F table has no milestone column)

**Problem.** Read literally, a two-engineer M1 must pass a local F17 (restore unequal DB/object/Temporal points, freeze, inventory, quarantine, reconcile). That may be intended, but nothing lets a reader confirm it: "M1 completion", "applicable cases" and "local subset" are never reconciled, and the M1 stop condition does not mention them.

**Fix.** Add a Milestone column to the F/S tables; define an M1-core gate (e.g. F01, F02, F11, F13, F14, F18, S01, S02, S07-local) and an M1-hardening bucket (F15, F16, F17-local, S10-local) that may complete in parallel with M2; make V04 reference the core list only.

**Verification.** Adversarially verified: both lenses downgrade to medium. V04 (local fixture tier) and V13 (live tier at M3) follow the package's stated two-tier pattern (S10 "M1 local subset, M3 live"; ADR-0003), so they are not contradictory. The defect is that the F table lacks a milestone/tier column and the M1 stop condition at 11-delivery.md:52 omits the restoration, late-effect and artifact cases V04 gates on.

### RF-06 — The workflow ID reuse policy is named only as "reject closed duplicates", and the restore procedure never says how a "new fenced generation" reaches a closed run

*Medium · Lifecycle & Temporal*  
*Found by:* lifecycle & Temporal  

**Evidence**

- 05-temporal-recovery.md:9 — "One stable identity spans Temporal runs … Configure start conflict behavior to fail and reconcile the existing workflow, and reuse policy to reject closed duplicates."
- 05-temporal-recovery.md:7 — "`ExecuteV1(execution_id, spec_digest, lifecycle_generation)`"; :81 — "Resume only reconciled records in a new fenced generation"
- 03-domain.md cleanup table — "failed → running: Durable retry generation acquired by workflow/reconciler"

**Problem.** The residual gap is real: 05:9 does not name the exact WorkflowIDReusePolicy value, and 05:80 ("Resume only reconciled records in a new fenced generation") never states whether resuming a record whose workflow is closed or absent after a Temporal restore starts a new run under execution/<id> (which RejectDuplicate would block within retention), is signalled into an open run, or proceeds ticket-only.

**Fix.** Record ConflictPolicy=Fail and ReusePolicy=RejectDuplicate explicitly, and add one sentence per recovery path: no path ever starts a second run under execution/<id>; generations advance in the database and reach an open run by signal; closed runs are recovered only via tickets. State whether tickets execute as separately identified workflows.

**Verification.** Adversarially verified: both lenses downgrade to medium and correct the premise. lifecycle_generation is a database projection-fencing token (05:52, 03:34, ADR-0003), and every closed-workflow recovery path routes through ReconciliationTickets and the sweeper (03:36, 05:23, 05:40), so RejectDuplicate is consistent with the design and in fact enforces its "never recreate a completed execution" invariant. Fact-check confirmed RejectDuplicate applies regardless of closed status.

### RF-07 — When a stop request fails or stays ambiguous and the cleanup deadline expires with presence unknown, no execution-state transition is defined; the projection can sit in running with cancellation requested until the platform deadline

*Medium · Lifecycle & Temporal*  
*Found by:* lifecycle & Temporal  

**Evidence**

- 03-domain.md:76 — "a timeout can commit without provider contact, while cancellation requires stop/no-dispatch evidence … A cancel API failure keeps cancellation `requested` and records `cancellation_failed`, with cleanup/reconciliation continuing."
- 03-domain.md:70 — "| running | cancelled | Stop confirmed with cancellation as winning outcome |"
- 05-temporal-recovery.md:34 — cancel activity "20s / 120s"

**Problem.** running → cancelled requires stop evidence and running → failed requires a confirmed workload failure, so an unconfirmed stop plus an expired cleanup deadline has no legal transition. No re-request-on-inspection rule and no cancellation budget exist, so the source requirement that "cancellation and timeout must converge" is only partly met.

**Fix.** Add a convergence rule: re-request stop on each inspection until a profile-bound cancellation budget elapses; when cleanup-verified absence is established commit cancelled; when the cleanup deadline expires with presence unknown commit failed (or cancelled) with infrastructure_status=unknown and a ticket.

**Verification.** Adversarially verified: both lenses downgrade to medium. The design already proceeds unconditionally to bounded cleanup after a stop request (02-architecture.md:106-110) and maximum resource lifetime independently bounds compute, so resources are not left running for 24 hours. What can linger is the lifecycle state.

### RF-08 — Who commits accepted → timed_out or cancelled for an execution whose workflow never started is unassigned, and the "backlog policy" it defers to is undefined

*Medium · Lifecycle & Temporal*  
*Found by:* lifecycle & Temporal  

**Evidence**

- 03-domain.md:58-59 — "accepted → cancelled: Cancellation wins before submission; prove no submit attempt can still dispatch" / "accepted → timed_out: Accepted-to-finish deadline exceeded before dispatch"
- 05-temporal-recovery.md:61 — "Temporal unavailable … status stays accepted/pending; deadline and backlog policy still apply"
- 05-temporal-recovery.md:23 — the reconciler "checks accepted nonterminal records … If closed unexpectedly, create an operator/recovery ticket"

**Problem.** A stuck accepted record holds an admission slot (05:40) with no component named to commit timed_out or to run the no-allocation cleanup traversal; 05:61 says "backlog policy still apply" but no such policy is defined anywhere. Not a duplicate-workload risk, but a stuck-slot and divergent-implementation risk.

**Fix.** State that the reconciler may commit accepted → timed_out/cancelled and run the no-allocation cleanup traversal when dispatch_status=pending and the deadline or cancel intent applies, closing the outbox row in the same transaction; define the backlog policy.

**Verification.** Adversarially verified: refuter downgrades to low, expert to medium. When the workflow does start, its first fenced activity is the writer (05:21, 03:76) and late dispatch is fenced by the dispatcher recheck, the startup state read, generation fencing and submission_closed. The gap is the never-started case during a prolonged Temporal outage.

### RF-09 — Pool-per-execution with "one-use" identities requires management-plane pool creation and an assign permission on each pre-created identity that the provider worker row does not list; identity count and retirement are unanalysed

*Medium · Compute isolation*  
*Found by:* compute isolation & security  

**Evidence**

- 06-compute-images.md:64 — "recommend pre-provisioned **one-use** execution identities with no reassignment to a different execution … Reassigning a principal while an old token remains valid is unsafe."
- 02-architecture.md:31 — provider worker MI: "Target-scoped Batch account/pool/job/task operations … No other BU targets, workload exposure, or general role assignment"

**Problem.** The provider worker row (02:31) lists Batch account/pool/job/task operations without distinguishing the management plane and omits userAssignedIdentities/assign/action. How many identities exist, how one is quarantined until its last token expires, and role-propagation timing are not analysed; the identity pattern is deferred to V06 with a broker-session alternative.

**Fix.** Enumerate the management-plane and assign permissions in the provider worker row, and replace "one-use" with an explicit model: a fixed pre-created pool of UAMIs with no Azure data-plane RBAC whose only purpose is a token for the private runtime interface audience, bound to an execution in the database and quarantined until token expiry before reuse.

**Verification.** Adversarially verified: both lenses downgrade to medium and correct the premise. Terraform pre-creates identities and grants RBAC (06:42), so no runtime identity creation or role assignment is implied. The expert confirmed from official docs that pools with managed identities can only be created through the Batch Management plane, identities are node-wide via IMDS, and in-place identity updates are not supported with active nodes.

### RF-10 — No rule selects one target when an application environment permits several; it is needed before the migration the design proposes can begin

*Medium · Registry & infra*  
*Found by:* registry & infra, direct inspection  

**Evidence**

- 10-registry-decisions.md:16 — "APPLICATION_ENVIRONMENT }o--o{ TARGET_REGISTRATION : permits"
- 03-domain.md:9 — "The API verifies that relationship and derives owning team, BU, target, policies, cost center, and identities from its registry."
- 10-registry-decisions.md:33 — "plan target-by-target migration with platform owners"

**Problem.** The public request carries only application_id, environment and logical profiles; admission must pin one registration version (10:31) and "derive" a single target, yet the model permits N. The moment a second target is enabled for the same app-environment, which the proposed prod/nonprod migration requires, admission has no selection function.

**Fix.** Give the permit row a role (primary | migrating | drain) plus placement/region eligibility; admission selects the unique enabled primary matching the resolved placement policy and rejects with a registry error otherwise.

**Verification.** Adversarially verified: both lenses downgrade to medium. The POC is confined to one approved nonproduction target and the migration plan is explicitly deferred to V05/V06/V10 evidence, so the gap is not exercised in M1–M3; both lenses confirmed no selection rule exists anywhere in the package.

### RF-12 — All 14 ExecutionInput fields are required, so template defaults and allowed_overrides have nothing to act on and the override rules the prompt asked for are undefined

*Medium · API contract · **Decide at M0***  
*Found by:* orchestrator hypothesis, API developer experience (both passes), requirements traceability, cross-doc consistency  

**Evidence**

- openapi.yaml:507-521 — required: all fourteen properties; additionalProperties: false
- openapi.yaml:660-673 — ExecutionTemplate required: [… allowed_overrides, input_schema, defaults]
- 04-api.md:44 — "expand explicit defaults, then hash canonical JSON"
- combined-build-prompt.md §4 — "Define which fields a caller may override"; §5 — "Finalize exact schemas and override rules in OpenAPI"

**Problem.** A caller must copy the template's 64-hex digest and exact argv into every request; `defaults` can never fill anything; `allowed_overrides` has no defined meaning. An M1 implementer of admission and idempotency hashing cannot tell whether expansion applies.

**Fix.** Decide one model and apply it in openapi.yaml, 04-api and 03-domain: either template-fixed fields (image, command, execution_class, profiles) become optional and are server-filled from the pinned template version with "if present must equal", or keep full-spec echo-and-verify and drop defaults/allowed_overrides from the template representation.

**Verification.** 6 votes, none refuted: 4 downgrade to medium, 2 to low. Verifiers note the full-spec shape mirrors the POC's own example, so it is an unstated model rather than a contradiction; but defaults/allowed_overrides semantics are defined nowhere and "expand explicit defaults" is a no-op against a closed schema.

### RF-13 — openapi.yaml line 656 is malformed YAML: an unquoted comma in a flow mapping injects a bogus schema key, which the author's validator could not see

*Medium · API contract · **Decide at M0***  
*Found by:* OpenAPI contract (Redocly lint), direct inspection  

**Evidence**

- openapi.yaml:656 — `download_url: { type: string, format: uri, description: Same-origin authenticated API route, never a cloud signed URL. }`
- Redocly: "Property `never a cloud signed URL.` is not expected here" at #/components/schemas/Artifact/properties/download_url
- validation.md:13 — "OpenAPI YAML | Parsed with duplicate-key rejection"; :25 — enterprise/structural lint "not performed"

**Problem.** JSON Schema 2020-12 ignores unknown keywords, so the jsonschema check passed silently. A real OpenAPI linter rejects the file. The validation record describes a check that cannot catch this class of defect.

**Fix.** Quote the description. Add Redocly and/or Spectral (pinned) to the M0 validation record so lint status is evidenced rather than described as not performed.

**Verification.** Confirmed: PyYAML parses download_url as {type, format, description: "Same-origin authenticated API route", "never a cloud signed URL.": null}. Only one such line in the file.

### RF-14 — Five public status fields plus result_complete and three error objects, with no glossary, no completion predicate, an undefined value, and one word meaning two things

*Medium · API contract · **Decide at M0***  
*Found by:* reviewability (both passes), API developer experience (both passes), orchestrator hypothesis, cross-doc consistency  

**Evidence**

- 03-domain.md:40-44 — state, cleanup_state, delivery_status, infrastructure_status, dispatch_status (pending, started, attention_required)
- openapi.yaml:181-183 — cancellation reason enum includes `superseded`; :616 — Cancellation.status examples include `superseded` and `acknowledged` (never defined in 03)
- openapi.yaml:569 — Execution requires all five statuses plus result_complete; ExecutionResult repeats delivery_status and result_complete

**Problem.** The prompt asked for four distinctions; the contract exposes five strings and a boolean and never tells a client when to stop polling or which combination means "done and I may download artifacts". attention_required has no definition, transition or client action. result_complete is undefined relative to delivery_status.

**Fix.** Add a one-table glossary (field, values, meaning, who writes it, what a client does) and a short client recipe: terminal = state in {succeeded, failed, cancelled, timed_out}; results usable when delivery_status in {complete, partial, failed}; define result_complete as delivery_status == complete or remove it; define or remove attention_required and acknowledged; rename one use of superseded.

**Verification.** Five reviewers converged independently; verification on the seeded variant did not complete. Confirmed by grep: attention_required appears only in the enum lines; "superseded" is both a caller reason and a server status.

### RF-15 — The event feed is untyped: no event_type vocabulary, no way to carry delivery/infrastructure/dispatch/cancellation changes, and two competing pagination mechanisms

*Medium · API contract · **Decide at M0***  
*Found by:* OpenAPI contract, API developer experience, cross-doc consistency  

**Evidence**

- openapi.yaml:619-633 — ExecutionEvent: `event_type: { type: string, maxLength: 128 }`; fields state, cleanup_state, inferred, error only
- openapi.yaml:226-233 — events response requires `cursor` and `page` (Page.next "absent when no further page exists"); 04-api.md:52 — empty timeout returns "unchanged cursor"
- 09-security-data.md:74 — a 15-stage telemetry vocabulary exists but the public event has no stage/outcome field

**Problem.** Events are the designated progress channel, yet consumers get no event-type catalog, no field for four of the five status facets, and an end-of-stream ambiguity: under Page.next semantics an empty long-poll looks like the end of the stream.

**Fix.** Publish the event-type list (extensible enum examples), add optional delivery_status/infrastructure_status/dispatch_status/cancellation and stage/outcome fields, and pick one continuation mechanism for events (cursor + wait_seconds) with an example of the empty-timeout case.

**Verification.** Three reviewers independently; confirmed by inspection of the ExecutionEvent schema.

### RF-16 — Logs cannot be tailed while an execution runs: no cursor in the logs response, next is absent at the end, and entries carry no timestamp or source

*Medium · API contract · **Decide at M0***  
*Found by:* API developer experience, orchestrator hypothesis  

**Evidence**

- openapi.yaml:261 — logs response required: [items, page, truncated]; items required: [sequence, stream, text]
- openapi.yaml:214-216 — wait_seconds exists only on /events; Cursor is "opaque … always reauthorize"
- 04-api.md:34 — "Sanitized bounded log pages, published through the platform" (availability before terminal state unstated)

**Problem.** For a running execution the client reaches the end of published logs, page.next is absent, there is no cursor to resume from, and the cursor is opaque so it cannot be built from sequence. The only options are re-reading from the start or hoping "end" is not final.

**Fix.** Give the logs response the same cursor field as events with "unchanged cursor at current end" semantics (optionally wait_seconds), add observed_at and source (bootstrap | workload | platform) per entry, make the page-level truncated flag mean "more bytes exist", and state that logs are readable before terminal state.

**Verification.** Confirmed by inspection. Timestamp-only variant: 2 refuted, 4 downgraded to low because stage timing lives in events; the tailing gap is the material part.

### RF-17 — timeout_seconds is charged from acceptance including queue and allocation, with one error code and no started_at, so callers cannot tell a slow platform from a slow workload

*Medium · API contract · **Decide at M0***  
*Found by:* orchestrator hypothesis, API developer experience (both passes), requirements traceability  

**Evidence**

- 03-domain.md:113 — "Timeout runs from acceptance through workload finish, including queue/allocation"
- 03-domain.md:59 — "accepted → timed_out: Accepted-to-finish deadline exceeded before dispatch"
- 06-compute-images.md:29 — `execution_timed_out`: "Accepted-to-finish deadline expired"; openapi.yaml:586-589 — Execution has created_at, deadline_at, observed_at, completed_at, no started_at

**Problem.** A Temporal outage or slow Batch allocation consumes the caller's budget and surfaces as the workload-timeout code. The design resolves a source ambiguity in the least caller-friendly way without presenting it as a decision.

**Fix.** Present it as a reviewer decision in ADR-0002: acceptance-relative platform deadline versus container-start-relative workload timeout plus a separate platform queue budget with its own code. At minimum add started_at (observed, never inferred) and timed_out_from to Execution.

**Verification.** 6 votes, none refuted: 1 stands (medium), 5 downgrade (low) because events expose the running transition and the contract states the rule. Kept at medium because three reviewers raised it independently and it is an unrecorded decision the sources left open.

### RF-18 — The input-artifact intake path that every execution depends on has no route, identity, schema or milestone

*Medium · API contract · **Decide at M0***  
*Found by:* API developer experience  

**Evidence**

- 03-domain.md:117 — "in the live demo an approved CI/operator publishing path uploads and verifies the source archive before registering it. No public upload endpoint is needed for this slice."
- 03-domain.md:92 — "The source artifact registry binds `source-01` to an immutable repository ID, full commit, archive digest and classification."
- poc-intent-and-requirements.md §5 — "Receive an immutable repository and commit reference."

**Problem.** The design replaces the source's repository-and-commit input with an opaque registry reference that must already be published, but nothing defines how it gets published. The shape of ExecutionInput being approved at M0 depends on that choice.

**Fix.** Decide the intake contract now even if implementation is M3: either accept source_ref {repository_id, commit} and let the platform package it under an approved identity, or specify an authenticated POST /input-artifacts (staging → verified → published) with limits and ownership.

**Verification.** Confirmed by grep: the only mentions are two descriptive sentences.

### RF-19 — Pool-per-execution collides with the Batch pool quota and asynchronous pool deletion, contradicts the warm-node option, and is not compared with node-per-execution in a governed pool

*Medium · Compute isolation*  
*Found by:* orchestrator hypothesis, compute isolation & security, requirements traceability  

**Evidence**

- 06-compute-images.md:40 — "Create a dedicated pool for each execution … Deterministic pool/job/task names derive from execution ID and are recorded before creation"
- 06-compute-images.md:99 — "Warm nodes may be prebooted but **unused**" (a prebooted node must live in a pool that exists before the execution it serves)
- 01-context.md:75 — "its latency and quota cost may make it unsuitable"; references.md:84 claims public pages do not expose quotas (default ranges are published)

**Problem.** Concurrency is capped by pool count, not cores, including pools still in deleting state. The warm cohort is logically impossible under execution-derived pool names. The source explicitly allowed "an existing governed pool" and the design dismisses it as node reuse, which it is not when nodes are removed on task completion.

**Fix.** State the quota; define the admission cap as a function of it including deleting pools; compare both topologies in 06/ADR-0004 and test both in V07/V08; either drop the warm option or specify the claim protocol.

**Verification.** 6 votes, none refuted: 1 stands (medium), 5 downgrade (low, "M2 experiment detail"). Fact-check: pools per account default 0–100 (500 with support), active jobs 100–300; the design nowhere states a number.

### RF-20 — User-subscription allocation mode is recommended for "resource visibility" without stating its concrete prerequisites on a shared BU subscription or weighing them against Batch-service mode

*Medium · Compute isolation*  
*Found by:* compute isolation & security  
*Files:* `docs/design/06-compute-images.md`, `docs/adr/0004-compute-controller-isolation.md`, `docs/design/02-architecture.md`  

**Evidence**

- 06-compute-images.md:38 — "Recommend **user-subscription allocation mode** as the initial experiment candidate for resource visibility … Account creation has allocation-mode-specific prerequisites … Neither is satisfied merely by giving a worker Contributor."
- 02-architecture.md:32 — "Batch service identity / service principal | Documented allocation-mode permissions approved by platform team"

**Problem.** On a shared per-BU subscription hosting multiple applications, Owner is unlikely to be available to the team, the linked Key Vault is a new shared dependency, and quota is drawn from everyone's pool. 06:38 says only "allocation-mode-specific prerequisites" and never justifies the visibility default against that cost.

**Fix.** State the concrete prerequisites in 06 and ADR-0004 and compare against Batch-service allocation (no subscription-scoped role for the Batch principal, Batch-owned quota, less resource visibility); keep the choice in V05 with the platform and security owners.

**Verification.** Adversarially verified: both lenses downgrade to medium. The design already treats the Batch service principal as a separately reviewed identity (02:32) and gates the mode choice as a P0 V05 decision. The expert confirmed from current docs: the "Azure Batch Service Orchestration Role" must be assigned to the Microsoft Azure Batch service principal at subscription scope (Owner is needed to assign it), a Key Vault in the same subscription and region with Key Vault Secrets Officer for that principal, and the BU subscription's regional and per-series compute core quotas are consumed.

### RF-21 — Batch requeues a running task on node loss regardless of maxTaskRetryCount, and the proposed execution-once claim depends on the M3-only runtime interface while the behaviour is tested in M2

*Medium · Compute isolation*  
*Found by:* compute isolation & security  

**Evidence**

- 05-temporal-recovery.md:42 — "Disable provider automatic task reexecution where possible; verify rescheduling on node loss too. If Batch can replay a task despite configured retry limits, require a durable execution-once claim before launching the workload"
- 02-architecture.md:19 — the private runtime data interface "is enabled only in M3"

**Problem.** The design anticipates the risk but the control it proposes needs a component that does not exist when V07 runs. In a single-node dedicated pool Batch will re-provision the node to meet targetDedicatedNodes and run the task again.

**Fix.** State the retry-versus-requeue facts in section 6 and adopt an M2-capable control: treat any requeueCount > 0 or node replacement as terminal workload_failed and terminate the job immediately.

**Verification.** Fact-check: docs state maxTaskRetryCount governs only nonzero-exit retries; node failure triggers Batch-internal rescheduling independent of it.

### RF-22 — The workload-facing runtime data interface lives in the API process, and the API identity row omits the write and vault privileges that interface needs

*Medium · Compute isolation*  
*Found by:* orchestrator hypothesis, compute isolation & security  

**Evidence**

- 02-architecture.md:19 — "a module of the same API application, with a distinct listener/ingress policy and authentication audience. It accepts execution-scoped upload/secret-delivery requests"
- 11-delivery.md:10 — "cmd/api/ HTTP and private probe/runtime listeners"
- 02-architecture.md:29 — API managed identity: "caller-authorized artifact reads" (no staging writes, no vault access)

**Problem.** A distinct listener is a routing and authentication distinction, not a privilege boundary. The only listener reachable from disposable VMs running untrusted code would share a process, managed identity and PostgreSQL role with caller authentication, and the identity table cannot be satisfied as written.

**Fix.** Deploy the runtime interface as a separate ACA app with its own managed identity (staging write, no read of published outputs) and a DB role limited to artifact staging and assignment tables, from M3 onward.

**Verification.** 6 votes: 3 stand (medium), 3 downgrade (low) noting the package's one-binary-many-roles principle; the security reviewer added the identity-row inconsistency.

### RF-23 — Key isolation controls are asserted without a mechanism: the IMDS block has no enforcement point, and supervisor/workload UID separation, node-assignment proof and Batch task-token exposure are unaddressed

*Medium · Compute isolation*  
*Found by:* compute isolation & security  

**Evidence**

- 06-compute-images.md:62 — "Block workload-originated IMDS and Azure platform endpoint access at a boundary the allowed workload cannot modify"
- 06-compute-images.md:50 — host iptables rules described as insufficient, with no replacement mechanism named

**Problem.** On a Batch node the trusted supervisor must reach 169.254.169.254 while the workload must not. The workable mechanisms (interface- or UID-scoped nft rules installed by the start task, workload without CAP_NET_ADMIN) are never named, so V06/V07 have no concrete thing to test.

**Fix.** Specify the egress enforcement (owner-UID or bridge-interface drop rules owned by root), the UID split between supervisor and untrusted task, how a node proves its assignment, and where the Batch task authentication token lives relative to the workload user.

**Verification.** Reviewer claim; consistent with the package text, not adversarially verified.

### RF-24 — Image signature and attestation verification has no stated tool, trust root, rotation or enforcement point

*Medium · Compute isolation*  
*Found by:* compute isolation & security  

**Evidence**

- 06-compute-images.md:78 — "Runtime check | Recheck current allowlist/revocation and signatures before start and helper pulls"
- 09-security-data.md:16 — "Digests, signatures/provenance, source archive digest … and revocation | Image owners"

**Problem.** Digest pinning is well specified; signature verification is not. Where it runs (API at admission via the registry, or supervisor at pull), which format (Notation, cosign, OCI referrers), and where keys live and rotate are undefined, so S06 cannot be evidenced.

**Fix.** Add a short subsection: admission-time verification in the API against the registry plus supervisor-side digest match, tool/format, trust-root storage and rotation, and the evidence record.

**Verification.** Reviewer claim; confirmed that the only text is a table cell.

### RF-25 — The POC spend ceiling has no enforcement or monitoring mechanism; only agreeing a number is scheduled

*Medium · Registry & infra*  
*Found by:* registry & infra, direct inspection  

**Evidence**

- 08-cost-approvals.md:7 — "…and an agreed experiment spend ceiling … A cap on requested compute is not a guarantee Azure charges cannot exceed a monetary number during failures."

**Problem.** Concurrency, timeout and maximum lifetime bound the rate of spend, not the cumulative amount. A long experiment series with repeated cleanup failures exceeds the ceiling with no platform- or Azure-side stop.

**Fix.** Propose a two-layer mechanism: in-platform VM-minutes × rate accounting per target/period with a hard admission stop and operator override at the ceiling, plus an Azure budget alert on the experiment scope.

**Verification.** Confirmed by grep: "spend ceiling" appears only as something to agree (08:7, V11).

### RF-26 — Per-application vault topology conflicts with a platform secret broker unless the broker identity is also per application; the design asserts the outcome without a mechanism

*Medium · Registry & infra*  
*Found by:* registry & infra  

**Evidence**

- 10-registry-decisions.md:35 — "Each application uses its own identity … no single provider identity reads all application vaults."
- 02-architecture.md:35 — "Artifact/secret broker role | Resolve assigned artifact/secret policies with current execution checks | Separate deployment identity when app-secret access is required"

**Problem.** "Each application uses its own identity" describes workloads, not the platform component resolving secret_refs. A single broker granted to every application vault is exactly the identity the design says must not exist.

**Fix.** Add the broker access model options with a recommendation, e.g. per-target-pool broker deployments granted only to vaults of applications permitted on that target.

**Verification.** Reviewer claim; consistent with 10 and 02.

### RF-27 — Temporal under-specifications: history budget is unquantified with no Continue-As-New trigger, conflict validation on start has no mechanism, the signal outbox has no terminal rule for a closed workflow, and three clocks can end a workload with inconsistent outcomes

*Medium · Lifecycle & Temporal*  
*Found by:* lifecycle & Temporal  

**Evidence**

- 05-temporal-recovery.md:38 — "Poll initially every 5s, increasing toward 30s"; :76 — "Continue-As-New before approaching it" (no trigger defined)
- 05-temporal-recovery.md:20 — "an existing workflow with matching execution/spec/generation" (DescribeWorkflowExecution does not return input; no Memo/search attribute specified)
- 05-temporal-recovery.md:25 — signal retry defined only for the not-yet-started case
- 03-domain.md:69/71 — lost-worker termination → failed, workflow deadline → timed_out; 05:40 — provider ceiling and sweeper max-lifetime are "independent limits" with no ordering invariant

**Problem.** Each gap is small, but together they are the difference between a sketch and a spec for the component whose correctness the whole design depends on.

**Fix.** Record the history arithmetic and a concrete CAN trigger; write execution_id, spec_digest and generation into the workflow Memo at start and validate conflicts by Describe; define the signal-outbox terminal rule (execution terminal → cancellation superseded; else ticket); add an admission invariant that timeout_seconds plus margin is strictly less than the provider and sweeper lifetimes.

**Verification.** Reviewer claims consistent with the text; the arithmetic (about 10 history events per poll, 30s cadence, 24h ceiling ≈ 30k events) is straightforward.

### RF-28 — Every activity has a short schedule-to-close, but no invariant says the workflow never fails on activity exhaustion for DB and result activities

*Medium · Lifecycle & Temporal*  
*Found by:* lifecycle & Temporal  

**Evidence**

- 05-temporal-recovery.md:31-36 — 60s/120s schedule-to-close on every activity
- 05-temporal-recovery.md:38 — "A timer resumes inspection after exhausted transient retries" (provider calls only)

**Problem.** An ACA revision rollout or PostgreSQL failover longer than 60–120s fails a projection-write or result activity to the workflow, which must then distinguish zero attempts from an ambiguous attempt.

**Fix.** State the invariant that ExecuteV1 never completes or fails because an activity exhausted retries, and document the outer loop (timer, deadline check, ticket on repeated exhaustion) per activity class.

**Verification.** Reviewer claim; the outer-loop rule is stated only for provider calls.

### RF-29 — The reference register reports a stale Temporal SDK release as "observed" on the check date and omits the server floor needed to judge Worker Versioning

*Medium · Delivery plan*  
*Found by:* delivery plan, web fact-check  

**Evidence**

- references.md:3 — "**Checked:** 2026-09-05"; :14 — "Candidate **1.42.0**, official release notes inspected"; :49 — "1.42.0 observed"

**Problem.** Claiming a five-month-old release was observed on the check date suggests the row came from prior knowledge, which is exactly the distinction the prompt asks the register to make.

**Fix.** Re-run the check; record the actual current release with retrieval date, or mark the row "from prior knowledge"; add the server minimum for Worker Versioning to V03.

**Verification.** Fact-check: releases page lists v1.48.0 as current; v1.42.0 is dated 8 April. Worker Versioning is GA since 2026-03-30 with self-hosted server ≥ 1.29.1.

### RF-30 — No acceptance ID exists for the success path; the POC's primary success criteria are mapped onto failure rows

*Medium · Delivery plan*  
*Found by:* delivery plan  

**Evidence**

- 11-delivery.md:156 — "step 7 logs/artifact → F16/S02; … steps 9 and 12 result/removal/no-static-credentials → F12/S03/S05" (F16 is "upload then kill before registration", F12 is "cleanup failure/orphan")

**Problem.** Eighteen failure rows and ten security rows, but no row for "authorized caller submits pr-validation → running → succeeded with logs, results and one artifact → cleanup succeeded" on the fake (M1) or on Azure (M3).

**Fix.** Add an A-catalog with at least A01 fake happy path (M1) and A02 live workload with logs, results, one artifact and cleanup succeeded (M3), and map demo steps to it.

**Verification.** Confirmed by inspection of the two catalogs.

### RF-31 — The demo workload definition omits the required output artifact, while the mapping table claims "results/SBOM" is preserved

*Medium · Delivery plan*  
*Found by:* requirements traceability  

**Evidence**

- 06-compute-images.md:48 — "Demonstrate container create/start/inspect/logs/stop, an execution-local network, and an execution-local volume." (no artifact)
- 11-delivery.md:112 — "5 Demo use case | Source archive/commit, immutable entrypoint, exact Docker, results/SBOM, optional CI"
- combined-build-prompt.md §5 — "It produces logs, results, and one artifact such as an SBOM or scan report."

**Problem.** The workload V07 will test never exercises artifact publication, so the result contract, 16-artifact limit, quarantine, download route and F16 have no representative driver.

**Fix.** Require the entrypoint to emit one registered output artifact in addition to test results and cite that in the mapping row.

**Verification.** Confirmed: the only SBOM mentions in 06 concern image supply chain evidence.

### RF-32 — Idempotency behaviour after a non-202 outcome is undefined

*Medium · API contract · **Decide at M0***  
*Found by:* OpenAPI contract  

**Evidence**

- 04-api.md:44 — "An identical authorized retry returns the original 202 representation … a changed request returns 409 idempotency_conflict."
- openapi.yaml:483 — RetryableProblem: "retry with the same idempotency key"

**Problem.** Whether a 400/403/409/410 admission decision is stored and replayed under the key, or whether a retry re-evaluates policy after a transient revocation window, is unstated; two consistent implementations behave differently and CI retry logic cannot be written.

**Fix.** State which outcomes are stored under the key (recommend 202 and 4xx admission decisions), what a retry of a stored denial returns, and that 429/503/5xx are not stored.

**Verification.** Confirmed: only the 202 replay and payload-mismatch cases are specified.

### RF-33 — Artifacts have no name or purpose, so a CI job cannot deterministically pick "the SBOM" from up to 16 outputs

*Medium · API contract · **Decide at M0***  
*Found by:* API developer experience, direct inspection  

**Evidence**

- openapi.yaml:652-659 — Artifact required: [id, digest, size_bytes, media_type, download_url, expires_at]
- 03-domain.md:30 — the internal record has "input/output purpose"; openapi.yaml:320 — Content-Disposition is "a safe generated attachment filename"

**Problem.** Test results and an SBOM, both JSON, are indistinguishable except by order, which is not promised stable.

**Fix.** Add a required name (the template output-contract key) and optional description to Artifact; state that download_url equals the artifact route.

**Verification.** Confirmed by inspection of the Artifact schema.

### RF-34 — The "minimal documented curl flow" has no human token-acquisition path and is scheduled as the last M1 package

*Medium · API contract*  
*Found by:* API developer experience (both passes)  

**Evidence**

- combined-build-prompt.md §11 — "For human POC use, provide a minimal documented CLI/curl flow early"
- 02-architecture.md:47 — "Use authorization code with PKCE for humans. Device code is optional if policy allows."
- 11-delivery.md:48 — "curl guide" appears in M1.4

**Problem.** Authorization-code with PKCE needs a browser and a redirect listener; it is not a curl flow. V02 does not list the human CLI token method as a decision.

**Fix.** Add the human token method to V02 with a proposed default (device code if permitted, otherwise an approved CLI delegated token for the API audience); put a seven-call annotated curl sequence into 04-api now and move the guide to M1.1.

**Verification.** Confirmed: PKCE is the human default; device code is "optional if policy allows"; no CLI token path is described.

### RF-35 — Caveat density buries the recommendations; the same disclaimers are repeated per paragraph and often in the sentence that makes the recommendation

*Medium · Reviewability*  
*Found by:* reviewability (both passes)  

**Evidence**

- 04-api.md:7 — four of five sentences say what is not known before the recommendation appears
- 01-context.md:75 — "Use a dedicated single-node Batch pool per execution … its latency and quota cost may make it unsuitable. A direct VMSS fallback carries greater controller responsibilities and does not automatically resolve Docker privilege risks." (no closing decision sentence)
- 12-verification.md:56 — "'Signed' or 'approved' evidence in the table describes a future requirement, not a signature already supplied."

**Problem.** Each disclaimer is individually correct and the prompt asked for the distinction, but stating it per paragraph makes the reader do the author's job of separating the plan from the hedge.

**Fix.** Put one global evidence-status banner in README, delete per-paragraph repeats, and end each recommendation paragraph with one bold line: Decision, verified by, fallback.

**Verification.** Reviewer judgement, illustrated by quotation; not adversarially verified.

### RF-36 — Nothing sizes the "yes": no effort, duration or critical path for M1.1–M1.4 or M2

*Medium · Reviewability*  
*Found by:* reviewability (both passes)  

**Evidence**

- 11-delivery.md:42-50 — work-package table has no duration or effort column

**Problem.** M1 contains a durable outbox, fencing generations, a reconciler, a sweeper, a replay corpus, a Testcontainers plus real-Temporal harness, redaction canaries and 18+10 acceptance scenarios. A manager cannot tell whether that is three weeks or three months for two people.

**Fix.** Add a rough-order-of-magnitude column (engineer-weeks with assumptions) per M1.x and M2 row, the critical path, and which items run in parallel.

**Verification.** Confirmed by grep: no effort, week, sprint or sizing term appears in any design or ADR file.

### RF-37 — M1 entry and exit are each defined twice: approval is "subject to" decisions scheduled after approval, M1.1 must "freeze interfaces" while V01's fallback keeps the contract draft, and "M1 done" has a local and a connected definition

*Medium · Reviewability*  
*Found by:* reviewability (both passes), delivery plan  

**Evidence**

- README.md:40 — "M1 only … subject to the API-convention and development identity/Temporal decisions in section 12" followed by "Cloud prerequisites may remain open"
- 11-delivery.md:45 — M1.1 "Freeze interfaces"; 12-verification.md:13 — V01 fallback "keep contract marked draft, continue approved independent implementation only"
- 11-delivery.md:52 — "Entra and enterprise development Temporal validation are required before claiming the connected M1 flow; local tests may proceed independently"

**Problem.** A reviewer cannot tell whether to approve now and let V01–V03 close during M1.1, or withhold approval until an API-standards owner, the identity team and the Temporal team answer.

**Fix.** State once, in README and 11-delivery: "Approve M1 now. V01 is decided by the two engineers at signoff using the section 4 defaults; V02/V03 gate only the connected claim and are tracked as M2 preconditions."

**Verification.** Confirmed by quotation.

### RF-38 — The working agreement is an unrequested, vendor-bound process document placed second in the reading path and elevated to a P0 M1 gate

*Medium · Reviewability*  
*Found by:* reviewability (both passes), delivery plan, requirements traceability  

**Evidence**

- README.md:7 — working-agreement.md is the second item in the initial review order
- 12-verification.md:32 — V20 / P0 M1: "Human/Codex task ownership, bounded agents, actual Git/CODEOWNERS and test tooling"
- working-agreement.md:46 — "coordinator plus at most two active helpers … Keep the user's current model selection"

**Problem.** About a quarter of the shortlist reading budget goes to how one vendor's AI assistant should coordinate subagents, which the governing prompt did not ask for at M0, and it is bundled into the architecture signoff. The genuinely required parts (restore Git, real CODEOWNERS, pinned tooling) are mixed with tooling preferences.

**Fix.** Split V20 into the source-required repository/tooling items (P0) and the agent/working-agreement preference (P2, separate approval); make the agreement tool-neutral; keep the test-framework table linked from 11-delivery; move the file to the end of the reading path.

**Verification.** Confirmed by quotation; three reviewers independently.

### RF-39 — validation.md depends on a temporary script outside the repository with a hard-coded absolute path

*Medium · Evidence & references*  
*Found by:* orchestrator hypothesis, reviewability  

**Evidence**

- validation.md:5 — "The temporary checker is `/tmp/validate_forgeapi_design.py`"
- script line 9 — ROOT = Path('/home/zerocool/github/forgeapi-homelab'); pyyaml/jsonschema unpinned

**Problem.** The one piece of executed evidence in a package that insists on reproducible evidence cannot be re-run by the other reviewer.

**Fix.** Place the script under docs/design/tools with a relative root and pinned dependency versions, and reference it from validation.md.

**Verification.** 3 votes: 2 stand (medium), 1 downgrade (low). The script still exists on this machine and reproduces every count in validation.md; it will not survive a reboot or reach the second reviewer.

### RF-40 — "M3 repeats applicable cases" weakens the prompt's "exercise all twelve source failure scenarios before accepting M3"

*Medium · Delivery plan*  
*Found by:* requirements traceability  

**Evidence**

- 11-delivery.md:64 — "M1 exercises the fake; M3 repeats applicable cases on the selected live provider."
- combined-build-prompt.md §11 — "Before accepting M3, exercise all twelve source failure scenarios"

**Problem.** "Applicable" is an undefined exclusion, and the scenarios most likely to be dropped (ambiguous submit, capacity, image pull, worker loss without callback, orphan) are the ones that only mean something against the live provider.

**Fix.** Replace "applicable" with "all twelve" and require a named exception with adapter-boundary fault-injection evidence for any scenario that cannot be induced live.

**Verification.** Confirmed by quotation.

## Low findings (16)

Clarity and polish; batch these into the same revision.

### RF-41 — Correlation headers are promised on every operation but declared on few; the alias submission routes and the cancellation POST declare different response sets; 403/429/503 are missing on GET routes; 406 is declared nowhere

*Low · API contract*  
*Found by:* OpenAPI contract, cross-doc consistency, API developer experience  

**Evidence**

- openapi.yaml:10-12 — "All operations accept validated X-Flow-ID … and return server X-Request-ID and X-Flow-ID headers"; FlowId parameter declared only on the three POSTs; X-Flow-ID response header only on ExecutionAccepted
- openapi.yaml:89-98 vs :121-130 — createExecution has no 404, createTemplateExecution has no 413/415; cancellation POST has no 400/403/413/415/429/503
- 03-domain.md:20 — capability denials are 403, yet no GET route declares 403

**Problem.** Generated clients and the future Spectral/kin-openapi gates will treat undeclared but documented codes as contract violations.

**Fix.** Declare FlowId at path level everywhere, add a shared response-header set to every response including 304/401, share one responses block between the two submission routes, and declare 403/429/503 on data routes.

**Verification.** Confirmed by inspection.

### RF-42 — Undeclared Zalando deviations: permissions are not carried as security-requirement scopes, permission names break the naming rule, and the Problem code vocabulary is incomplete

*Low · API contract*  
*Found by:* OpenAPI contract  

**Evidence**

- openapi.yaml:26-27 — `security: - EntraBearer: []`; per-operation permission only in `x-required-permission: execution.read+execution.data.read`
- openapi.yaml:708 — Problem.code is a free string; only 13 codes are named across all documents and none for 401/403/404/413/415/429/503

**Problem.** The enterprise linter and gateway tooling read the security requirement, not a private extension; whether policy_denied is 400 or 403 is unstated (F02 says both).

**Fix.** Express permissions as security scopes per operation (or add rows to the deviation table), and give Problem.code an extensible examples list with a fixed status mapping.

**Verification.** Fact-check on the guidelines: the five declared deviations are accurately described; these additional ones are not declared.

### RF-43 — Template discovery is list-only: no single-template read, no description, `version` in the catalog versus `template_version` in the request, and undefined behaviour for revoked versions

*Low · API contract*  
*Found by:* API developer experience (both passes), OpenAPI contract  

**Evidence**

- openapi.yaml:45-71 — GET /execution-templates is the only template read; ExecutionTemplate has no description or links
- openapi.yaml:511 — request requires template_version; catalog field is `version`

**Problem.** A caller pages the whole authorized catalog, each entry carrying its full input_schema, to find one version, then renames the field.

**Fix.** Add GET /execution-templates/{template_id} (authorized versions, latest non-revoked marker), a description, links.self, and align the field name.

**Verification.** Confirmed by inspection.

### RF-44 — Cancellation ergonomics: mandatory 16+ character Idempotency-Key and mandatory JSON body whose properties are all optional; Location points at the execution; 409 on already-terminal executions needs "benign" guidance for CI cleanup

*Low · API contract*  
*Found by:* API developer experience (both passes), orchestrator hypothesis  

**Evidence**

- openapi.yaml:171-184 — IdempotencyKey required; requestBody required with no required properties
- openapi.yaml:191 — "Location is the execution status resource"; no GET for a cancellation

**Problem.** A curl user must invent a key and send -d '{}' to stop a run; a GitHub Actions cleanup step routinely hits 409 when the run finished first and must be told that is success.

**Fix.** Make the key and body optional on cancellation (server-side convergence already provides safety), document 409 execution_terminal as expected for cleanup steps, and state that the cancellation is readable via Execution.cancellation.

**Verification.** Confirmed by inspection.

### RF-45 — No execution listing; the idempotent-replay path is the only lost-ID recovery and is not documented as such

*Low · API contract*  
*Found by:* orchestrator hypothesis, API developer experience (both passes)  

**Evidence**

- 04-api.md:38 — "No execution collection listing is required for the first slice; clients persist their submission ID."
- 03-domain.md:20 — "All queries filter by current authorized scope, including collection pagination." (the machinery exists)

**Problem.** A developer who closed the terminal after POST leaves a running, spend-consuming execution they cannot find or cancel through the API; a team-wide read grant has nothing to read.

**Fix.** Document "replay the identical POST with the same Idempotency-Key to recover Location"; consider a minimal own-scope GET /executions?application_id=&environment= in M1.3.

**Verification.** 6 votes: 2 refuted ("the prompt enumerates the routes and asks for no listing"), 4 downgrade to low. Kept as a suggestion, not a requirement gap.

### RF-46 — Examples exist for only 4 of 10 operations; events, logs, results, templates, identity-context and artifact download have none

*Low · API contract*  
*Found by:* orchestrator hypothesis, API developer experience  

**Evidence**

- validation.md:16 — "Four examples validate against their schemas"; openapi.yaml example blocks at 184, 404, 432, 465 only

**Problem.** The template representation, the single artifact a caller needs to construct a request, has no example; nor does a terminal event or a results page.

**Fix.** Add one example per 200 response, including a pr-validation-v1 template with a concrete input_schema and an events page showing a terminal event.

**Verification.** 3 votes, none refuted, all low.

### RF-47 — Artifact download declares no Content-Length, digest header or Accept-Ranges; the Cancellation representation drops the reason the caller supplied

*Low · API contract*  
*Found by:* OpenAPI contract  

**Evidence**

- openapi.yaml:313-322 — "Range requests are not supported"; headers are Cache-Control and Content-Disposition only
- openapi.yaml:610-618 — Cancellation has id, execution_id, status, requested_at, error; no reason

**Problem.** The client is told to compare with the manifest digest but gets no header carrying it or the length.

**Fix.** Add Content-Length, Repr-Digest or ETag = digest, and Accept-Ranges: none; add reason to Cancellation.

**Verification.** Confirmed by inspection.

### RF-48 — Batch's per-account uniqueness of pool and job IDs is the natural duplicate-detection marker for F03/F15 but is never stated, leaving "retain a marker" abstract

*Low · Compute isolation*  
*Found by:* orchestrator hypothesis  

**Evidence**

- 05-temporal-recovery.md:47 — "Retain the provider job/marker or another durable admission tombstone until outstanding submit attempts have settled"
- 06-compute-images.md:40 — "Deterministic pool/job/task names derive from execution ID and are recorded before creation"

**Problem.** A reader cannot tell whether retaining the job while deleting the pool is feasible or intended.

**Fix.** State the Batch fact and the marker choice (retain the job object, delete the pool).

**Verification.** Not adversarially verified. Fact-check confirmed PoolExists/JobExists conflict semantics.

### RF-49 — Threat model omits telemetry and log injection from untrusted workloads, the template author as a privileged role, and the Batch service as a trusted third party

*Low · Compute isolation*  
*Found by:* compute isolation & security  

**Evidence**

- 09-security-data.md:9-22 — threat rows; 09:72 — worker VM exports to the same OTLP path as the control plane

**Problem.** A workload that can reach the collector can forge spans carrying other execution IDs, corrupting the correlation evidence S07 relies on; template publication is a privileged act with no review step named.

**Fix.** Add three threat rows with controls: collector reachable only from the host agent path with workload telemetry treated as data; security review for template publication; Batch listed as a trusted dependency with its permission set reviewed.

**Verification.** Reviewer claim; consistent with the 12-row threat table.

### RF-50 — M1 CI publishes signed, scanned API/worker images via Azure federation although nothing in M1 consumes them and the registry/federation prerequisites are gated at M2

*Low · Delivery plan*  
*Found by:* delivery plan  

**Evidence**

- 11-delivery.md:58 — "Reviewed main/release jobs publish immutable API/worker OCI digests with SBOM/provenance and scans; deployment/publishing Azure access uses federation."
- 12-verification.md:17/21 — V05 and V09 (registry, image ownership) are P0 for M2

**Problem.** Work with no M1 consumer and a hidden dependency on open organizational decisions.

**Fix.** Restrict M1 CI to build/test/lint/vulnerability gates plus a local image build without push; move publish-with-federation to the control-plane hosting package.

**Verification.** Confirmed by quotation.

### RF-51 — Register hygiene: some V rows omit the fallback the prompt requires, V14 has no milestone, V15/V20 carry two priorities in one cell, and the ADR index verification column drifts from the ADRs

*Low · Delivery plan*  
*Found by:* delivery plan, reviewability, cross-doc consistency  

**Evidence**

- 12-verification.md:16/32 — V04 and V20 end without a fallback; :26 — "V14 / P0 for ingress deployment"; :27 — "V15 / P1 M1 shutdown; P2 scaler"
- 10-registry-decisions.md:46 — ADR-0002 "V01, F01–F04" vs adr/0002:23 — "V01 … F01/F14 … S02"

**Problem.** The P0 filter a manager would use does not work, and the index the reader is told to use lists different verification IDs than the ADRs it points to.

**Fix.** Split dual-priority rows, add a Blocks column and sort by it, add fallbacks to V04/V20, regenerate the ADR index column from the ADRs.

**Verification.** Confirmed by inspection.

### RF-52 — Retention tiers omit published input artifacts and execution events; "staged archive ≤24h" is ambiguous against reusable shared inputs and 90-day execution retention

*Low · Evidence & references*  
*Found by:* cross-doc consistency  

**Evidence**

- 09-security-data.md:48 — "staged archive ≤24h after completion"; 03-domain.md:117 — "Shared immutable input packages require explicit app-level read grants"

**Problem.** If "staged archive" means the published input, reusable inputs vanish 24 hours after the first execution while the execution referencing them lives 90 days.

**Fix.** Add rows for published input artifacts and ExecutionEvent history; rename "staged archive" to "staging-state upload" if that is the intent.

**Verification.** Confirmed by quotation.

### RF-53 — Small drifts: the lint-profile identifier differs between the OpenAPI file and the prose; the POC's "Azure Activity Log" source of truth is dropped; secret delivery has no S/V item; Terraform executor worker-shape requirements and the M2 foundation backend authentication are absent from section 7

*Low · Evidence & references*  
*Found by:* cross-doc consistency, requirements traceability, registry & infra  

**Evidence**

- openapi.yaml:22 — `platform-api-design/0.1.0-proposed` vs 04-api.md:3 and references.md:11 — `platform-api-design/0.1.0`
- grep for "activity log" across docs/design and docs/adr — no match; POC §9 lists "Azure administrative operations | Azure Activity Log"
- 07-terraform.md:33 — use_azuread_auth guidance sits only under the "M4 and later" heading; 07:21 — foundation backend "via the organization's established process"

**Problem.** Each is a one-line fix, but the mapping table claims coverage for two of them.

**Fix.** Unify the profile string; add Activity Log as a restricted-operator source in 02 and cite it in V08/V12; add an S/V item for secret delivery; cross-reference the backend paragraph from the foundation section and name a stack-executor queue with memory-bound concurrency.

**Verification.** Confirmed by grep for each item.

### RF-54 — Later-milestone design gaps (M4+): cross-BU permits leave the charged budget owner undefined, stack migration to a new subscription target implies recreate-or-import but is never stated, plan invalidation has no re-plan trigger or comparison basis, the ledger omits the reservation-to-forecast transition, and ACA image/revision ownership is left as "agreed"

*Low · Registry & infra*  
*Found by:* registry & infra  

**Evidence**

- 10-registry-decisions.md:16 — permits are unconstrained by BU; 08-cost-approvals.md speaks of "the" BU cap
- 07-terraform.md:41 — "If a fresh plan differs materially, require new review/approval" (no trigger, no comparator)
- 07-terraform.md:11 — "Terraform/release pipeline, with agreed image-deployment ownership"

**Problem.** Appropriately deferred, but the prompt asked the design to recommend one option where a decision belongs to the engineers, and ACA image ownership is the two-writer conflict that will actually occur in M2.

**Fix.** Recommend Terraform owning the app shell with lifecycle ignore_changes on the image and the release pipeline as sole image writer; note the others as M4 design items.

**Verification.** Reviewer claims consistent with the text; deferred material, not adversarially verified.

### RF-55 — Diagrams are absent exactly where the prose is densest: no lifecycle state diagram, no dispatch-protocol diagram with crash points, no worker-VM trust-boundary diagram despite the section title

*Low · Reviewability*  
*Found by:* reviewability  

**Evidence**

- grep -c "```mermaid": 01 (1), 02 (3 sequences), 08 (2), 10 (1); 03, 05, 06, 09 (0)

**Problem.** The three hardest things to hold in one's head, the 8×4 state lifecycle with arbitration, the commit/claim/start/record protocol with its crash points, and the layered trust boundary inside the VM, are prose only.

**Fix.** Add a stateDiagram-v2 to 03, an annotated dispatch sequence to 05, and a boxes-and-boundaries diagram to 06.

**Verification.** Confirmed by count: 03, 05, 06 and 09 contain no Mermaid blocks.

### RF-56 — F/S/V identifiers are used on the recommended reading path before they are defined, with no legend; component names (supervisor, broker, reconciler, sweeper, dispatcher) are used without a glossary or a statement of which are the same process

*Low · Reviewability*  
*Found by:* reviewability (both passes)  

**Evidence**

- working-agreement.md:52 — "remains unresolved in V20" is the first V-ID a reader meets; the only legend is 11-delivery.md:104
- 02-architecture.md:17 "Provider reconciler"; 05:23 "periodic reconciler"; 05:50 "independent sweeper"; 06:52 "supervisor" and "broker" — never related to each other or to the worker box in the 01 diagram

**Problem.** A cold reader hits opaque identifiers dozens of times before learning what they mean.

**Fix.** Add a three-line legend and a component glossary (name, process, purpose, section) to README/02.

**Verification.** Confirmed by quotation.

## Fact-check ledger

Every version pin and documentation claim in `references.md` and inline text was fetched from its cited URL.

| Claim in the package | Status | What the source says |
|---|---|---|
| Go 1.27.1 is a published release | Confirmed | Listed as current stable on go.dev/dl. |
| Temporal Go SDK 1.42.0 exists and requires Go ≥ 1.24 | Confirmed | True, but 1.42.0 is dated 8 April; releases up to 1.48.0 exist. See RF-29. |
| Terraform 1.16.0 is published | Confirmed | Binary listing present. |
| KEDA 2.20 docs include a Temporal scaler introduced in 2.17 | Confirmed | Page states "Availability: v2.17+" and the backlog caveat the design cites. |
| learn.chatgpt.com AGENTS.md / subagents / worktrees pages are official OpenAI docs | Confirmed | All three resolve to OpenAI-branded Codex documentation. |
| Zalando changelog has a 2026-03-16 entry | Confirmed | Most recent entry (#859). |
| Zalando positions on enums, async, closed inputs, Idempotency-Key | Partially correct | UPPER_SNAKE_CASE is SHOULD; 202+Location is an allowed alternative; additionalProperties:false on requests conflicts with rule 111 (the design declares this deviation). |
| Spectral container has a documented telemetry opt-out | Confirmed | DO_NOT_TRACK=1; the npm distribution has a separate install-time opt-out. |
| Graph v1.0 List manager: application permissions not supported | Confirmed | Application row reads "Not supported." The orchestrator's own suspicion that this was wrong was refuted. |
| ACA sends SIGTERM then a 30-second window | Confirmed | Verbatim in the lifecycle page. |
| PostgreSQL Flexible Server backup retention up to 35 days with PITR | Confirmed | 7 days default, 35 max; WAL RPO up to ~5 minutes; geo-redundant PITR not available. |
| Batch user-subscription mode prerequisites | Confirmed | Subscription registration, Azure Batch Service Orchestration Role for the Batch service principal, linked Key Vault, marketplace terms. |
| Batch quotas: pools and jobs per account | Partially correct | Pools 0–100 default (500 max), active jobs 100–300 (1,000 max). The design makes no numeric claim; references.md says quotas are not on public pages, which is not quite right. |
| Batch pool/job IDs unique per account; duplicates conflict | Confirmed | PoolExists / JobExists on the status-and-error-codes page. |
| maxTaskRetryCount=0 prevents re-execution on node failure | Partially correct | It governs only nonzero-exit retries; node failure triggers Batch-internal rescheduling independent of it. See RF-21. |
| Pool managed-identity tokens are node-wide via IMDS with no per-task scoping | Confirmed | Assignment is pool-level only. |
| No-public-IP pools have documented outbound dependencies | Confirmed | nodeManagement private endpoint or TCP/443 to BatchNodeManagement. |
| Temporal ID conflict policy Fail and reuse policy RejectDuplicate semantics | Confirmed | RejectDuplicate applies regardless of closed status; Fail is the default. See RF-06. |
| GetVersion patching documented; Worker Versioning status | Confirmed | Worker Versioning GA 2026-03-30, self-hosted server ≥ 1.29.1; legacy method removed March 2026. |
| Temporal authorization via Authorizer/ClaimMapper; queues not a boundary | Partially correct | Authorizer/ClaimMapper confirmed; the page never mentions task queues, so that clause is the design's own (reasonable) inference. |
| Terraform ephemeral values 1.10, write-only arguments 1.11 | Confirmed | Verbatim. |
| azurerm backend use_azuread_auth and native blob locking | Confirmed | Least privilege is Storage Blob Data Contributor at container scope. |
| Dependency lock file locks providers, not modules | Confirmed | Verbatim. |
| Infracost price books, API token, plan-data flow | Confirmed | Azure price-sheet CSV via support; API needs a service-account token; CLI does not send the plan. |
| AICPA 2017 TSC with 2022 points of focus URL | Confirmed | Resolves; download behind a free account. |
| Docker authorization-plugin and rootless pages exist with the cited limitations | Partially correct | Upgrade-stream limitation confirmed verbatim; the rootless "known limitations" section moved to a troubleshooting sub-page and says nothing about Compose. |
| Azure cost data delays and corrections | Confirmed | 8–24h for EA/MCA, up to 72h finalization, re-rating documented. |

Corrections to make in `references.md`: record the Temporal Go SDK release actually current on the check date (1.48.0) or mark 1.42.0 as "from prior knowledge"; add the Temporal server minimum for Worker Versioning (self-hosted ≥ 1.29.1, GA 2026-03-30) to V03; note that Batch default quotas (pools 0–100, active jobs 100–300 per account) are published; note that `maxTaskRetryCount` does not govern node-loss requeue; note the npm Spectral opt-out (`SCARF_ANALYTICS=false`) alongside the container one; note that the rootless-Docker limitations section moved to the troubleshooting page.

## Considered and dismissed

Candidate findings that verification or fact-checking rejected or narrowed. Do not change the package for these.

| Candidate | Why it was dropped or narrowed |
|---|---|
| The Graph "List manager" application-permission claim is wrong | Fact-check: the page's Application row reads "Not supported." The design (and the prompt) are correct. |
| learn.chatgpt.com references look fabricated | All three URLs resolve to OpenAI-branded Codex documentation. |
| Missing GET /executions contradicts the prompt | Both sources enumerate the route set without a listing; 2 of 6 verifiers refuted the "contrary to the prompt" premise. Kept only as a DX suggestion (RF-45). |
| Log lines need timestamps to satisfy "break down time by stage" | Stage timing is designed into events (recorded_at/observed_at) and telemetry; 2 of 6 verifiers refuted the linkage. The tailing gap (RF-16) is the material part. |
| The runtime interface necessarily shares a process with the API | Half the verifiers read "same application" as same binary under a different role, which 02:41 and ADR-0001 support. Kept at medium (RF-22) because 11-delivery.md:10 puts both listeners in cmd/api and the identity row is inconsistent either way. |
| The design hides its Batch viability risk | It does not: 01-context, ADR-0004 and section 6 all say the first topology may be unsuitable and must be measured. The gaps are scope and sequencing (RF-03), not concealment. |

## What to keep

Specific, checked strengths. Do not regress these while revising.

- **Traceability is real, not decorative.** Every POC section 1–25 plus appendices A–D and every combined-prompt section maps to a design file and F/S/V IDs; all 15 source open questions are individually dispositioned; the twelve failure scenarios appear in source order as F01–F12. Spot checks by the traceability reviewer held.
- **External claims survive checking.** Twenty-seven version and documentation claims were fetched: none contradicted, five partially correct. Two of the orchestrator's own suspicions (the Graph permission claim, the learn.chatgpt.com URLs) were wrong and the design was right.
- **The dispatch protocol is genuinely crash-safe as written.** Atomic commit of spec, idempotency, reservation and outbox; FOR UPDATE SKIP LOCKED leases; recheck before start; start-conflict treated as success-with-validation; a lost start acknowledgement retried as the same start, never a new workflow ID.
- **The late-submit/deletion race is given a mechanism, not a slogan.** Section 5 identifies that deletion must not erase the only duplicate-detection marker while a delayed submit can still succeed, and requires a persisted submission_closed marker before any SDK call.
- **The Docker-authority analysis is technically correct and honest.** It states plainly that an unrestricted rootful socket is host authority, that iptables and hiding a token are insufficient, that removing an identity is not token revocation, and that no off-the-shelf proxy is claimed to solve it.
- **Deviations are labeled rather than smuggled.** reconcile is called out as an addition to the six-method contract; the environment → environment_variables rename is explained; five API-guideline deviations are tabulated with rationale; a ten-row reconciliation table records each source tension and its resolution.
- **Idempotency is specified to a codable level.** Key pattern, scope, canonicalization, alias-route normalization, replay and conflict behaviour, retention tied to terminal-plus-cleanup, and unique-constraint convergence are all stated.
- **Evidence discipline is honest.** Nothing is marked run, verified or approved that was not; the validation record separates what was checked from what was not; ADRs are all Proposed with no invented acceptance; the version register distinguishes candidate from tested.
- **Numeric limits and vocabularies are consistent everywhere they appear.** Body size, argument and environment caps, artifact counts, page sizes, wait seconds, key length, retention days, and the eight/four/four/four/three status vocabularies match across prose, OpenAPI and the sources.

## Method

The reviewer read all thirty-one files and both sources in full, formed sixteen hypotheses, then ran independent reviewers over nine dimensions (requirements traceability, OpenAPI contract, lifecycle & Temporal, compute isolation & security, registry & infra, delivery plan & process, cross-document consistency, reviewability (manager cold read), API developer experience (curl and CI)) and three web fact-check sweeps covering 27 claims. Sixteen hypotheses and nine high-severity technical findings received two- or three-lens adversarial verification (refuter, materiality, domain expert); findings settled by direct quotation or parsing are marked as such; medium and low finder claims that were neither are labeled "reviewer claim". Not done: the enterprise OpenAPI lint profile (unknown, V01), Mermaid rendering, and any behavioural, tenant or cloud test. The author's validator at `/tmp/validate_forgeapi_design.py` was run and reproduces every count in `validation.md`; Redocly lint was run on `openapi.yaml` and found RF-13.

The same findings are published as an interactive page: https://claude.ai/code/artifact/e1ffeae8-6e5b-4c92-9efb-f6619e086755
