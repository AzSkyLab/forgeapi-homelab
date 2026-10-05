# Prompt: implement ForgeAPI in the work environment

**Current as of:** 2026-10-04, agent-v1 operations API on `main` (Temporal TLS/mTLS, AKS workload
identity, real Entra caller auth and live group re-checks proven in the home lab; AKS base
rehearsed on kind with Entra auth as shipped; work-day runbook in `deploy/aks/README.md`). Not yet
deployed at work.

Paste everything below the line into the work assistant, in a checkout of this repository. The
old two-Container-App prompt is archived in `docs/archive-temporal/` and applies only to the old
release.

---

You are bringing ForgeAPI's current version into the work environment, working with the
engineer. The work environment already runs an earlier ForgeAPI (the `/deployments` API on Azure
Container Apps with Table Storage, built from an early version of this repository). The new
version is an agent-first operations API: intents become saved Terraform plans that run only when
a caller executes the exact plan digest; it needs one durable disk shared by API and worker, so it
runs as one pod on AKS next to the organisation's Temporal.

Read, in order, before acting: `AGENTS.md`, `docs/work-handover.md` (your plan: baseline check,
what changed, decisions, phases with gates, report format), then look things up in
`docs/work-deployment.md` (every setting, endpoint and limit) and follow the work-day runbook in
`deploy/aks/README.md`. Follow the repository's Ponytail and model-routing instructions if they
are available to you; say so if they are not.

Rules that matter most:
- Start read-only: find the work repository's baseline and inventory the running deployment
  (handover §4) and report before changing anything.
- Leave the existing deployment untouched; deploy the new version alongside it (handover §5).
  Adopting existing deployments is not built; do not improvise it.
- Ask the engineer before any change to Entra (app registrations, federated credentials,
  consent), role assignments, cloud resources or the existing deployment, and before deploying any
  pattern other than the agreed verification pattern.
- No local development on the work Mac; run the repository's CLIs inside the deployed pod (`kubectl exec`).
  Do not change application code to make something work; report it instead.
- Never print or commit tokens, keys, tenant/subscription IDs, the tenant mapping or Terraform
  state. Never retry an apply, force-unlock state or edit the ledger.
- Stop at each gate in handover §7 and report in the handover §9 format: what you did, evidence
  (operation IDs, readbacks), decisions, and everything unverified, plainly.
