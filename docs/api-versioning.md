# API versioning and compatibility

Current as of: 2026-10-02. This policy covers the operation-based `agent-v1` API only.
The retained `/deployments` application is outside this guarantee. No legacy state migration
is implied. This working tree is unreleased; establishing a contract does not publish an image
or deploy it.

## Version boundaries

| Version | Meaning |
| --- | --- |
| `/v1` and `agent-v1` | The HTTP contract and its behavior. Compatible changes stay in v1. |
| Application release, currently `1.0.0` | The executable release. Minor releases add compatible behavior; patches fix defects. |
| Pattern tag and resolved commit | The Terraform definition selected at acceptance, independent of API/application versions. |
| Ledger and Temporal history | Persistent execution data. API versioning does not make arbitrary data or workflow changes compatible. |

`/v1` is the canonical business API. Existing root routes are compatibility aliases to the
same handlers and ledger, not redirects and not a moving reference to the newest major.
Requests through either spelling share idempotency scope. `/healthz` stays unversioned.
Responses preserve the caller's route family in links, Location and dispatch status URLs.
`/openapi.json` retains the existing root surface; `/v1/openapi.json` describes the canonical
v1 contract. OpenAPI's own specification version is not this API's major version.

## Backward compatibility

An existing conforming v1 client must continue to work with a newer v1 server. Within v1:

- Preserve field names, types, required/nullable behavior, defaults, identifier formats,
  status codes, pagination and the meanings of existing fields.
- Do not add required request fields or tighten accepted input constraints as an ordinary
  compatible release. Optional additions must preserve behavior when omitted.
- Preserve idempotency scope and request identity, acceptance-before-dispatch, exact saved-plan
  execution, append-only audit, placement isolation and sensitive-output withholding.
- Freeze operation states and execution instructions. Adding a new state or `next_action`
  is not automatically safe merely because it is an enum addition.
- Additive response fields are allowed. Consumers must ignore fields they do not understand.
  Dynamic pattern input schemas, outputs and metadata retain their documented map semantics.

Renaming/removing fields, changing defaults or execution semantics, or requiring new inputs
requires a new major contract. A security correction may necessarily reject unsafe behavior;
document the impact explicitly rather than describing it as transparent compatibility.

## Forward compatibility and feature discovery

New clients can work with older servers for the features both support. They cannot make an
older server implement a future feature.

Bootstrap with the retained `/agent` discovery route. Use its advertised links and capabilities.
An older `agent-v1` response without capability metadata means the original baseline contract;
absence is never permission to send a newer optional field. Ignore unknown capability names
and extra response fields. Require advertised support for an optional feature; otherwise use
the baseline behavior only when it satisfies the request, or report the feature unavailable
before mutating anything.

Capability values must be literal JSON booleans. Malformed values such as `1` or `"yes"` are
rejected during discovery rather than coerced into feature permission. Unknown capability
names with correctly typed values remain compatible; omitted metadata still means baseline.

Stable operation listing is an optional v1 feature, advertised as
`capabilities.stable_operation_pagination`. On a supporting server, read an initial operations
page and continue with `before=<next_before>` until the nullable `next_before` is null.
`before` cannot accompany a nonzero `offset`; continuation pages return `next_offset: null`.
The existing offset mode and its `next_offset` semantics remain unchanged. Older responses can
omit `next_before`; newer consumers must not send `before` without the capability. Anchors use
public operation IDs and the caller's visibility rules; missing and invisible anchors return
the same 404. This stabilizes traversal under new insertions, not operation-state snapshots.
The client rejects duplicate operation IDs, a returned cursor anchor, and continuation values
that do not match the returned page. Older pages without `next_before` and additive response
fields remain supported. This checks one page; it does not prove a complete snapshot.
Operation `offset` and event `after` accept integers from 0 through 9223372036854775807;
oversized positions return structured 422 instead of the previous SQLite overflow.
Intent inputs must contain finite JSON numbers, including nested values. `NaN`, infinities
and literals that decode to infinity receive sanitized 422 before acceptance or dispatch.
Finite inputs and ordinary strings such as `"NaN"` retain their existing behavior.
The client exposes existing event `after`/`limit` pagination without adding a capability.
Missing old discovery metadata permits baseline events; explicit `operation_events: false`
refuses them. Consumers may encounter new event action/outcome strings and extra fields;
these are readable data, unlike execution instructions. Event identity and sequence must
still match the requested trail.

Requests remain strict: unknown fields are rejected. Silently discarding an infrastructure
instruction is unsafe. Clients must stop mutation on unknown execution states/actions, an
unsupported contract, or `uncertain`; they must not interpret unfamiliar results as success.
Use the existing `terminal`, `next_action`, polling interval and links rather than guessing
progress from presentation text. Errors are machine-readable envelopes; do not parse human
detail strings. `503 dispatch_unconfirmed` retains its original request/operation identity.

The client checks submission responses against the requested action, pattern and any explicit
resource target. A contradictory response is an unconfirmed mutation: retain the original
body/key; do not trust its operation ID. The client performs no automatic retry or follow-up.

Ledger initialization creates the event `(operation_id, seq)` index if missing, including
on an existing database. This additive index leaves rows, audit triggers and cursor semantics
unchanged. A large existing history can lengthen the first ledger open while SQLite builds
the index; it is not a ledger data rewrite or a distributed-storage migration.


**New operation action (2026-10-03):** `drift_check` is a new `action` value. Older clients
validate `action` as deploy/destroy and must stop on unknown values, so `GET /operations` omits
drift-check operations unless the caller asks for them (`?action=drift_check` or
`?include_checks=true`), and `resource.latest_operation_id` keeps pointing at the last real
change. Only callers of the new endpoint (or holders of an id from a `resource_busy` 409) see the
new value.

## Durable requests and upgrades

The v1 canonical intent is the original default-expanded representation. Its frozen fields
and defaults feed the existing sorted-JSON SHA-256 fingerprint. URL prefixes, software release
numbers and discovery metadata never enter that fingerprint. Omitted and explicitly supplied
defaults keep their historical meaning. Do not switch to `exclude_unset`, alter JSON encoding,
or silently omit a new instruction from the hash.

Adding an optional intent field requires explicit canonicalization rules and a historical
replay test: old requests must retain their fingerprints, while a materially different new
request must conflict when it reuses a key. Existing ledger rows are not rewritten to make
tests pass. A changed route must not create a second operation for the same caller/key/body.

The real local Temporal scenario races initial acceptance and exact-digest execution across
root/v1 aliases. It verifies one operation/resource, one accepted event per phase, one scheduled
activity in each phase history and one actual Terraform plan/apply receipt across worker restart.
It is a bounded local concurrency proof, not a hosted throughput guarantee.

Coordinate API and worker upgrades on the supported single host. Preserve the ledger, audit,
Temporal history, Terraform state and exact saved plans. Keep the Terraform/provider toolchain
compatible with pending plans; an HTTP-compatible release does not guarantee that a different
Terraform binary can apply an older binary plan. Never replan, retry an apply, or force-unlock
as an upgrade fallback. Inspect unresolved operations before changing execution dependencies.

An offline backup must preserve the ledger, Temporal history and Terraform artifacts at one
quiesced point. Restoring it is not a rollback of cloud changes. Keep original writers stopped,
retain the same paths/configuration/toolchain, and reconcile anything that changed after the
snapshot before resuming. An older ledger may lack execution records for newer applies.

Workflow changes need deterministic history replay tests. A ledger/schema change needs its own
explicit migration and rollback proof. These requirements do not promise arbitrary mixed-version
workers or downgrade support. A release must state whether rollback is compatible with its
persistent data; otherwise preserve the data and reconcile before changing binaries.

`tests/test_workflow_replay.py` replays real completed planning, successful apply and controlled
activity-failure histories in `tests/fixtures/recovery/`, with unchanged production timeout/retry
settings. The successful apply used real local-file Terraform. The failure uses a test-only
activity that claims the operation then raises an application error; the production workflow
and uncertainty writer handle it. Host identities, sticky queue names and failure stack paths
are normalized as disclosed in fixture provenance. These are current baselines, not histories
from a pre-versioning release or actual timeout. Replay checks determinism; it does not rerun
Terraform, prove cloud state or establish arbitrary mixed-worker/downgrade compatibility.

## Authentication boundary corrections

Malformed principals are not a compatibility promise: missing identities and invalid relevant
claim types receive generic 401 rather than a shared fallback caller identity or parsing error.
Valid JWT `oid`/`sub` selection and additive unknown claims remain supported. The shared auth
module also applies this correction to legacy routes; that does not extend the operation API's
compatibility guarantee to `/deployments`. Hosted identity integration remains separately unverified.

Real loopback HTTP tests exercise the default client opener for 301/302/303/307/308 redirects
and cross-origin discovery links. A second origin receives no request or dummy bearer token;
safe diagnostics omit the token and response marker. This evidence does not cover hosted TLS
termination or proxy configuration.

## Observed older-server check

On 2026-10-02 the current client read the retained older hosted `agent-v1` API successfully.
Discovery omitted API-major, software-release and capability metadata. Baseline offset listing
returned three operations, and status plus two events for each retained Azure/AWS/GCP create
operation matched the expected identities. The observed API/engine runtime image was
`sha256:2b946bce45583e0e3b128d94436e67df80711f2cb01e5366098820e8e2150ad6`.
This is evidence for that particular older runtime, not every historical version.

The following opt-in check requires the existing placement-demo tunnel at loopback 38000.
Run it from this repository. It reads only the explicitly retained examples; it never submits,
executes, traverses pages or cleans up resources, and does not print outputs or placement data.

```sh
uv run python - <<'PYCLIENT'
"""One-shot, read-only check of the retained hosted API through app.client."""

import json

from app.client import Client


EXPECTED = (
    ("op_97b86d4dad2e44beb5e7f576b38adac1", "res_2678f96de5b7409e8aa5a6e6ef2e68cd"),
    ("op_51f6f38e75664557a94bf8a65f68f1f0", "res_3abfb4c9321340dc8b3187ced048b260"),
    ("op_37cb74de7fa04d0fa3991806007476aa", "res_3cc54c18749c426cbec8d001a9ba4676"),
)


def main():
    client = Client("http://127.0.0.1:38000")
    discovery = client.discover()
    assert discovery["contract"] == "agent-v1"
    page = client.operations(limit=3)
    summary = []
    for operation_id, resource_id in EXPECTED:
        status = client.status(operation_id)
        assert status["state"] == "succeeded"
        assert status["resource_id"] == resource_id
        events = client.events(operation_id, limit=2)
        assert events["items"]
        summary.append(
            {
                "id": operation_id,
                "resource_id": resource_id,
                "state": status["state"],
                "event_count": len(events["items"]),
                "event_continuation": events["next_after"] is not None,
            }
        )
    print(
        json.dumps(
            {
                "contract": discovery["contract"],
                "api_major_metadata_present": "supported_api_major" in discovery,
                "capabilities_metadata_present": "capabilities" in discovery,
                "release_metadata_present": "software_release" in discovery,
                "software_release": discovery.get("software_release"),
                "operation_page_count": len(page["items"]),
                "operation_page_continuation": page["next_offset"] is not None,
                "operations": summary,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
PYCLIENT
```

## Release checks and deprecation

The checked-in canonical OpenAPI baseline makes contract edits visible. Review snapshot diffs
against this policy; updating a snapshot alone does not establish compatibility. Run contract
fixtures for old-client/new-server and new-client/old-server behavior, root/v1 parity, actual
error envelopes, cross-alias idempotency and historical request fingerprints. Exercise an
existing saved plan without replanning, and replay recorded Temporal history when applicable.

Run `uv run pytest` and `uv run ruff check .` before release. Keep the work deployment brief,
prompt and progress evidence synchronized with contract changes. Distinguish fixture and local
integration evidence from a hosted rollout; passing tests does not deploy the version.

`.github/workflows/check.yml` applies these local regression checks to pull requests, main
pushes and reusable release checks. It validates the lockfile with `uv sync --locked`, then
uses frozen lint/test runs. `.github/workflows/image.yml` requires that job before publishing
tag/manual images. A passing workflow does not replace review of whether a schema change is
compatible; updating a snapshot alone still proves only consistency with the new snapshot.
Pushed image tags must exactly match `v` plus the project/runtime version; the workflow checks
this before registry login. Manual builds publish SHA tags only, even when dispatched on a tag.
External actions in both check and publishing workflows are pinned to verified full commit
references, with local regression coverage. Base-image tags remain mutable; this alone does
not establish reproducible image bytes or a verified hosted build.

Do not build v2 until a breaking requirement exists. When needed, serve supported majors during
an announced migration period, provide migration instructions and explicit retirement dates,
and verify affected consumers before removal. Root aliases stay bound to v1 for its lifetime.
There is no automatic retirement date or permission to remove a version in this milestone.
