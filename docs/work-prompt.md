# Prompt: deploy forgeapi in the work environment

Paste everything below the line into a fresh session with an assistant that has the Container Apps MCP server, in a clone of this repository at tag `v0.7.0` or later. Fill in the two bracketed items first. The assistant then works through `docs/work-deployment.md`, which is the detailed brief; this prompt sets the frame.

---

You are deploying **forgeapi**, a self-service API that lets teams deploy approved Terraform patterns into their own dev subscriptions, into our Azure Container Apps environment using the Container Apps MCP server available to you. It was built and proven in a home lab; your job is to reproduce a known-good shape here, not to redesign it. I am the engineer; ask me for anything you need and stop when the brief says to stop.

**Read first, in this order, before calling any tool:**

1. `AGENTS.md` (repository rules).
2. `docs/work-deployment.md` (the brief: what to deploy, every setting, the verification steps, troubleshooting). Follow it exactly. Where it says "stop and report", stop and report.
3. Skim `docs/tenancy.md`, `docs/audit.md` and `docs/outputs.md` only as far as the brief refers to them.

**What you are deploying (summary; the brief has the detail):**

- Two container apps from this repository, both built and deployed by the MCP server:
  - `forgeapi-api` from the root `Dockerfile`: the API, behind Easy Auth.
  - `forgeapi-engine` from `deploy/engine/Dockerfile`: a Temporal dev server (gRPC 7233), its web UI on the HTTP port behind Easy Auth, and the worker that runs Terraform. Pinned to one always-on replica.
- The API app reaches the engine on gRPC port 7233 **inside the environment**. Whether the MCP server can expose an internal TCP port on an app is the **first thing to establish**; if it cannot, stop and tell me, because this layout does not work without it.
- Records, Terraform state, logs and audit events live in an existing storage account. Configuration goes in as environment variables and Key Vault secret references; there is nothing else to configure through the MCP server.
- Each app gets a system-assigned managed identity. Only the **engine** identity gets Azure deploy rights. Role grants and the Easy Auth app registration's token settings are **manual steps that I will do**; tell me exactly what to grant and where, with the identities' principal IDs.

**Inputs I am giving you now** (ask for anything missing; do not guess):

[fill in: tenant ID; platform subscription ID; state storage account, resource group, container and table names; pattern repositories (host, org, repo, sub-directory); GitHub credential (App ID + installation ID + Key Vault secret name, or a Key Vault secret name holding a read-only token); github.com or GHES host; the business-unit mapping YAML or where it is; egress allowlist status]

**Rules:**

- Do not improvise architecture or substitute workarounds. Do not run `uv`, tests or local Docker. Do not change application code; the only file you edit is `patterns.yaml`.
- Never print, log or paste a secret. Refer to secrets by Key Vault secret name.
- Ask me before: creating or changing any role assignment or app registration, deploying any real pattern (only `local-file` and `azure-identity-check` are yours to run), or when a verification step fails twice.
- Work through the brief's steps in order: discover what the MCP server can do (Step 0), egress (Step 1), catalog (Step 2), deploy the engine then the API (Step 3), hand me the manual steps (Step 4), verify in the numbered order (Step 5). Do not skip verification steps or reorder them.
- Finish with the brief's report format: what is deployed, which manual steps are done or still open, which numbered checks passed (with deployment IDs), what was not verified and why, any deviation from the brief, and open items for me.

**Things to know:**

- forgeapi is dev-only. There is no production approval workflow and none is wanted.
- The Temporal dev server keeps history in memory and has no authentication of its own; it is a getting-started arrangement. We will move the engine to our Temporal service on AKS when the API moves to a real dev environment (the brief says how).
- If a deployment is interrupted because the engine restarted, it is marked `interrupted` and `POST /deployments/{id}/retry` continues it; the brief explains this.
- Several things have never been proven anywhere (Easy Auth's identity header reaching the app, a system-assigned identity running Terraform, an internal TCP port on an MCP-deployed app, our private endpoints and egress). The brief lists them. Treat each as unproven until a verification step shows it working here.

Start with Step 0 and tell me what you find before deploying anything.

[fill in: anything specific to today, e.g. "the engine may not have deploy rights yet; verify only up to step 4 of the checks"]
