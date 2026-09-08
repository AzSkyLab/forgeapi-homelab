# TLDR — what we are building and how we work

**The goal:** one governed API that lets a caller request work, track it, and retrieve results. Infrastructure capabilities come after the core API works.

**Today:** the local API includes authentication, asynchronous jobs, cancellation, paged/long-polled events, bounded logs, verified downloads, admission limits, durable audit and recovery. Run it with Docker and required Entra sign-in. No paid Azure hosting; compute stays simulated.

**Verified live spike:** [one empty Key Vault through Terraform](key-vault-demo.md). An authenticated API request → approved plan → Temporal/Terraform created the retained vault. The executor has since been separated from the caller: a dedicated lab service principal uses a seven-day certificate, with real ARM/Terraform reads verified and no human CLI fallback. Managed identity on the work-hosted worker is the next identity integration. [Identity TLDR and commands](terraform-identity.md). Arbitrary Terraform repos are not executable yet.

```text
Caller → Go API → PostgreSQL: execution + dispatch intent
                         ↓
                      Worker → Temporal: durable workflow/timers
                         ↓
                   Simulated compute
                         ↓
                  PostgreSQL: status, events, logs, results
                         ↑
                   Caller reads via API
```

The API returns `202 Accepted` after saving the job, audit record and dispatch intent together. The worker rechecks current policy and starts a stable-ID Temporal workflow. Workload outcome, artifact delivery and cleanup are separate; durable recovery continues unfinished cleanup/delivery after process loss. Reusing a request key cannot create another accepted job. Cancellation requests a stop, not a rollback.

## What is real versus simulated?

| Real in this increment | Deliberately not real yet |
| --- | --- |
| HTTP routes, validation, PostgreSQL transactions, idempotency and outbox | Full enterprise policy/group integration |
| Entra token validation/current grants and a real browser PKCE → Docker API walkthrough verified on Linux | Mac execution, connected second-user isolation and managed-identity/WIF workload tests remain pending |
| Temporal server, worker, durable timers and persisted local history | Azure compute, container/image execution, source checkout |
| Status/events/log/result APIs and artifact integrity checking | Workload test results and SBOM: clearly labeled synthetic output |
| Unit/race tests, actual-response OpenAPI validation, real worker-kill/restart, old/new history replay, local OTLP and vulnerability gate | Production readiness, full failure/security matrix and human M1 acceptance |

## How we co-develop

1. A human chooses one observable behavior: “the same request must not create two jobs.”
2. Engineer + coding assistant add a test, implement the behavior, and run the shared checks.
3. Another engineer reviews the small diff and test evidence. Humans own acceptance and consequential decisions.
4. Record the next task in [progress](progress.md), so the next session can continue without rereading every design file.

One coordinating assistant is enough initially. Optional helpers can investigate or review bounded work when requested; they are development assistants, not components of the running API. No agent service or AI runtime credential is required. The short [coding handoff](handoff.md) gives the next session exact files, commands and acceptance checks.

**Identity rule:** callers sign in through the browser; Azure provisioning uses a separate worker identity. No API keys/client passwords. Prefer managed identities for Azure workers and WIF for trusted external runners. The home lab has an explicitly approved seven-day certificate exception; its private key stays on the host, outside Git/Docker. It must not become the work-environment default. [Local Entra setup](entra-local.md), [executor identity](terraform-identity.md).

## Roadmap, in order

| Stage | Outcome |
| --- | --- |
| **Now: local core implementation** | Run the Entra-protected API and its recovery/contract tests; inspect workflows, durable audit and local traces |
| **Next: close M1 acceptance** | Peer review, remaining failure-budget/security cases, second-user/Mac evidence and enterprise Temporal; the narrow Key Vault spike does not waive these gates |
| **Then: shared Azure integration** | Add reviewed hosting infrastructure code; deploy API/workers to ACA and verify enterprise identity/networking/Temporal connectivity |
| **Then: real temporary compute** | Run gated Azure Batch viability experiments; implement the chosen provider with isolation, cleanup and cost controls |
| **Later: broader infrastructure through the API** | Expand the one-pattern spike to approved existing repo bundles, managed/federated runners, remote state, promotion and approval/cost governance |

Local development remains the normal edit/test loop; ACA becomes the shared integration environment. Hosting the API and adding infrastructure operations **to** the API are separate steps.

**Meeting rehearsal:** after [Entra setup](entra-local.md), run `sh scripts/verify-local.sh`. Open Temporal UI at <http://localhost:8233>. [Walkthrough](demo.md)

**Three commands to show:** `make up` starts/upgrades safely; `make demo` runs the signed-in walkthrough; `make test-core` proves real local crash recovery and workflow replay. `make test-docker` shows the unit/race/contract suite.
