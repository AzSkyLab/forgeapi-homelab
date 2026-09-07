# TLDR — what we are building and how we work

**The goal:** one governed API that lets a caller request work, track it, and retrieve results. Infrastructure capabilities come after the core API works.

**Today:** run the API, database and workflow engine locally with Docker, with required Entra sign-in. Two identity app registrations are needed; no paid Azure hosting is provisioned. Compute stays simulated.

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

The API returns `202 Accepted` promptly. A worker picks up saved dispatch intent and starts a workflow with a stable execution ID. Temporal coordinates the steps; the fake activity records simulated outcomes. Reusing the same request key cannot create another accepted job. Cancellation is a request to stop, not a rollback.

## What is real versus simulated?

| Real in this increment | Deliberately not real yet |
| --- | --- |
| HTTP routes, validation, PostgreSQL transactions, idempotency and outbox | Full enterprise policy/group integration |
| Entra token validation/current grants and a real browser PKCE → Docker API walkthrough verified on Linux | Mac execution, connected second-user isolation and managed-identity/WIF workload tests remain pending |
| Temporal server, worker, durable timers and persisted local history | Azure compute, container/image execution, source checkout |
| Status/events/log/result APIs and artifact integrity checking | Workload test results and SBOM: clearly labeled synthetic output |
| Automated unit, HTTP, workflow and database tests | Production readiness, full recovery/security acceptance |

## How we co-develop

1. A human chooses one observable behavior: “the same request must not create two jobs.”
2. Engineer + coding assistant add a test, implement the behavior, and run the shared checks.
3. Another engineer reviews the small diff and test evidence. Humans own acceptance and consequential decisions.
4. Record the next task in [progress](progress.md), so the next session can continue without rereading every design file.

One coordinating assistant is enough initially. Optional helpers can investigate or review bounded work when requested; they are development assistants, not components of the running API. No agent service or AI runtime credential is required. The short [coding handoff](handoff.md) gives the next session exact files, commands and acceptance checks.

**Identity rule:** no static API keys/client secrets where avoidable. Local developers sign in through the browser; the API verifies public signing keys. Prefer managed identities for future Azure services and WIF for external workloads. Neither requires a secret for this human-operated demo. [Local Entra setup](entra-local.md).

## Roadmap, in order

| Stage | Outcome |
| --- | --- |
| **Now: local core demonstration** | Start the stack, submit/observe/cancel simulated jobs, explain code ownership, run tests |
| **Next: finish core API** | Enterprise authentication/authorization, remaining API semantics, recovery/failure cases, telemetry and CI; meet M1 acceptance |
| **Then: shared Azure integration** | Add reviewed hosting infrastructure code; deploy API/workers to ACA and verify enterprise identity/networking/Temporal connectivity |
| **Then: real temporary compute** | Run gated Azure Batch viability experiments; implement the chosen provider with isolation, cleanup and cost controls |
| **Later: infrastructure through the API** | Add persistent stack operations, Terraform execution, promotion and approval/cost governance |

Local development remains the normal edit/test loop; ACA becomes the shared integration environment. Hosting the API and adding infrastructure operations **to** the API are separate steps.

**Meeting rehearsal:** after [Entra setup](entra-local.md), run `sh scripts/verify-local.sh`. Open Temporal UI at <http://localhost:8233>. [Walkthrough](demo.md)
