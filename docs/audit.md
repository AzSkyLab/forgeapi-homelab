# Audit trail

**Goal:** answer "who did what, when, where, with what, and how did it end?" for every change made through the API, including the attempts that were refused.

## What is recorded

| Action | Outcome | Recorded by | Notes |
| --- | --- | --- | --- |
| `deployment.create`, `.update`, `.retry`, `.destroy` | `accepted` | API, **before** the job is dispatched | pattern, version and commit, business unit, environment, size, the caller's inputs, the values the platform injected, estimated cost; for updates the from/to version and size |
| the same | `refused` | API | HTTP status and the reason exactly as the caller saw it (wrong environment, not permitted, over budget with figures, invalid input, wrong state) |
| `deployment.access` | `refused` | API | someone outside the owning business unit tried to read or change a deployment (they saw 404), or lacked the environment's deploy right (403). Filed under the **owning** business unit, so the owners can see who tried |
| `deployment.state` | `succeeded`, `failed`, `destroyed` | worker | failures carry Terraform's error summary |

Every event has a time-ordered ID, a UTC timestamp and the actor: the caller's Entra object ID, or `worker`.

Not recorded: reads that were allowed, catalog browsing, and **dry runs** (they change nothing, and portals call them constantly).

## Guarantees and their limits

- **No audit, no action.** An accepted action is recorded before it is dispatched. If that write fails the request fails with 503, nothing is started, and a deployment record created moments earlier is marked failed. Refusals and worker outcomes describe something that has already happened, so they are recorded best-effort; a failed write is logged (without the event's content) and does not change the result.
- **Append-only by API.** There is no endpoint to change or delete an event, and a test asserts that every `events` route is `GET`. Storage itself is not immutable: someone with data-plane rights on the storage account could alter rows. Closing that needs storage-level controls (for example exporting events to an immutable blob container or a SIEM).
- **Sensitive inputs are redacted** (`***`) when the pattern marks the variable `sensitive`. If the pattern cannot be read at all, only input names are kept. *Known gap outside the audit trail:* the deployment record itself still stores sensitive inputs in clear, because Terraform needs them; sensitive values should be passed as Key Vault references, not literals.
- **Order.** IDs are timestamps from the writing process, so events from the API and a worker within the same few milliseconds can sort either way.

## Reading it

- `GET /deployments/{id}/events`: one deployment's trail, newest first. Same access rule as the deployment.
- `GET /events?business_unit=&since=&limit=`: your business units' events, newest first (limit ≤ 500).
- **Auditors:** groups listed under the mapping's top-level `auditors:` may read every business unit's events. It grants nothing else: no deployments, no patterns, no changes.

## Storage

Follows the deployment store: an `events` table in SQLite locally; hosted, a `<table>events` table in the same storage account (partitioned by business unit), covered by the existing Table Data role. No retention or export policy exists yet.
