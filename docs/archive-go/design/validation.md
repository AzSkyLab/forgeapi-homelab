# Design and contract validation

**Date:** 2026-09-07. **Current package:** 0.4.0; historical M0 baseline 0.3.0 retained below. **Scope:** documentation, two OpenAPI contracts and focused HTTP contract tests; no new Azure operation. [Index](README.md), [review dispositions](../review/m0-design-review-response-2026-09-07.md).

## Current reconciliation evidence

- PASS: `python3 docs/design/tools/validate_design.py` checks design/ADR links and anchors, preserved source hashes/review coverage, all ten portable compute operations and all four lab deployment operations. The lab checks also compare the router's exact paths, resolve schemas, reject caller target/credential/HCL overrides and retain the portable compute property denylist unchanged.
- PASS: Redocly **2.51.2** lint for **both** contracts with the checked-in profile, no warnings/errors. This run reused the existing task-specific npm cache offline; no dependency installation was needed.
- PASS: `TestDeploymentOpenAPIResponses` first failed because the lab contract was absent, then passed after the contract was added. Shared response-schema validation checks actual handler status/body/common headers, denial/error responses, all current state strings and retained legacy targets without executor. The repository fixture is in-memory: this is HTTP contract evidence, not real Entra, DB or Terraform evidence.
- PASS: native `make check` (format/vet/unit/race/build) using the existing Go 1.27.1 toolchain/cache. The first sandboxed attempt could not open loopback `httptest` sockets; the approved rerun passed. No assertion was skipped or weakened.
- Current counts are emitted by the checker; historical M0 counts below are not live counters. ADRs remain Proposed, including ADR-0013's record of already-authorized lab operations. Human acceptance is separate.

No PostgreSQL/Temporal integration suite, signed-in demo, live identity check or Azure apply was rerun for this documentation change. Their previous evidence remains in [progress](../progress.md), with full M1/enterprise/live-compute gaps still open. No commit/push or runtime behavior change.

## Reproduce from any checkout

The [checker](tools/validate_design.py) resolves the repository relative to its own location, not the caller's working directory. In an approved Python environment, install the pinned [documentation requirements](tools/requirements.txt) if absent, then run from the repository root:

```sh
python3 -m pip install -r docs/design/tools/requirements.txt
python3 docs/design/tools/validate_design.py
REDOCLY_TELEMETRY=off npm exec --yes --package=@redocly/cli@2.51.2 -- redocly lint docs/design/openapi.yaml docs/design/openapi-deployments.yaml --config docs/design/redocly.yaml --format=stylish
```

The pip line is setup, not required on every run. npm needs approved registry access on first use; a populated cache may be used offline. Do not commit caches or virtual environments. These documentation commands remain separate from `make check`; Go contract tests run through `make check` or `make test-docker`. Use the current checkout's commands, not the historical M0-only descriptions below.

## Historical M0 validation environment — package 0.3.0

Actual environment: Python 3.14.7, PyYAML 6.0.3, jsonschema 4.26.0, Node 26.8.1, npm 12.0.2 and Redocly 2.51.2. Python dependencies were already available. An offline npm invocation could not resolve registry metadata; the pinned command was then run successfully with approved network access and a task-specific temporary npm cache. No application dependencies or cloud resources were created.

The [local profile](redocly.yaml), `platform-api-design/0.1.0`, extends Redocly recommended rules, makes structure/media-type example failures errors, and explicitly exempts an undeclared publication license and the documentation-only example.com server. Tags have descriptions. These are scoped M0 exceptions, not ignored schema defects or approval of the unknown enterprise Zalando profile.

## Historical M0 observed results

| Check | Observed result |
| --- | --- |
| Original RF-13 reproduction | Pre-revision OpenAPI failed Redocly structure lint: unquoted flow-mapping comma created unexpected key `never a cloud signed URL.`; baseline had 1 error and 5 warnings |
| Revised OpenAPI lint | Redocly 2.51.2 with committed profile: exit 0, no errors or warnings |
| Repository documentation/schema checker | Exit 0; final inventory below is recorded after the complete revision |
| Source preservation | Both original requirement-document SHA-256 values match the baseline in README; reviewer report also unchanged |
| ADR/review status | All 12 ADRs remain Proposed; each RF-01 through RF-56 has exactly one disposition; no signoff invented |
| Coverage | 12 numbered design sections, 25 source sections, 15 source questions, F01–F18 and positive A01/A02 registered |
| OpenAPI contract | 10 operations, unique IDs, path parameters, required permission roles, shared correlation headers, explicit common errors and alias response parity |
| Examples | 11 embedded JSON examples validate; all 9 JSON-success operations have examples (submission aliases share one). Binary download uses manifest/header documentation |
| Input regressions | Full and five-field minimal caller requests accepted structurally; 20 invalid caller examples rejected, including absent required fields, null optional values, provider field, mutable tag and bounds |
| Other contract regressions | RF-13 exact mapping/description, resumable empty log page, event facet requirements/extensibility, artifact name requirement, template ceiling/secret-list examples |
| Public contract inspection | Declared property denylist has no forbidden provider identifiers; this does not prove a runtime serializer cannot leak data |
| Document integrity | Local links/anchors, balanced fences and ADR sections checked; paths are checkout-relative |

Historical M0 inventory: 29 Markdown artifacts, 119 local links/anchors, 12 Proposed ADRs, 10 API operations, 246 resolved local OpenAPI references, 15 component schemas, 11 embedded JSON examples and 56 finding dispositions. All checked successfully at that checkpoint. The checker was also invoked from outside the checkout to verify repository-relative path resolution.

## Historical M0 evidence limits

The September 5 checker and its counts were genuine but insufficient to detect RF-13: JSON Schema accepts unknown keywords that the intended OpenAPI structure should not contain. This revision adds both an exact regression and the independent linter rather than treating YAML parsing as conformance proof.

The template example checks cover structural/documented constraints; they do not execute server expansion, authorization, idempotency transactions or runtime policy. Example IDs, image hashes, timestamps and artifact bytes/digests are synthetic, not observed workloads.

At M0, the application/Entra/Temporal/Azure experiments had not run. Subsequent local/Entra/Key Vault results and remaining gates are separated in the [current verification overlay](12-verification.md#current-evidence-overlay). Enterprise guideline lint, Mermaid rendering and live compute/isolation/restore/billing/performance acceptance remain unverified. Local documentation success does not close those gates.

Git was unavailable during M0; it is usable now. No commit, push or human acceptance is claimed by this reconciliation. The original sources and reviewer report remain preserved; dated M0 test results above describe their original checkpoint, not today's full repository state.
