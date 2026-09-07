# Official references and version register

**Baseline checked:** 2026-09-05. **Targeted review refresh:** 2026-09-07 for Temporal SDK/Worker Versioning, Batch identity/account/quota/retry guidance, Cosign and documentation lint tooling. Other rows retain their baseline retrieval date. [Index](README.md). Public documentation is research evidence, not enterprise deployment/security proof; capture immutable versions/configuration with implementation evidence.

## Version register

| Component / standard | M0 record | Verification and implementation action |
| --- | --- | --- |
| OpenAPI | **3.1.1**, design API version **0.2.0**, JSON Schema 2020-12 | Revised sketch; local Redocly/schema/example checks in validation.md; enterprise profile unverified |
| Zalando | Public living guidelines consulted 2026-09-05; changelog includes 2026-03-16 | Authoritative organizational commit **unknown**; commit-list retrieval failed. V01 must supply approved immutable revision; changelog date is not a pin |
| Local lint proposal | `platform-api-design/0.1.0`, executable redocly.yaml, CLI **2.51.2** | Local structure/examples profile checked; two documented internal-design exceptions, not organization-approved Zalando rules |
| Go | Candidate **1.27.1**, listed by official downloads | Not installed/tested for this project; pin architecture checksum/container digest in M1 after compatibility/security review |
| chi | Preferred router; exact module version **not selected** | Confirm team standard and select/pin in M1; no dependency installed |
| Temporal Go SDK | Candidate **1.48.0**, latest release resolved to version permalink on 2026-09-07 | Tagged go.mod requires Go **1.25.4**; replaces prior inspected 1.42.0 candidate. Actual server/SDK/replay/tracing compatibility still V03/V04; not installed as an application dependency |
| Temporal server / namespaces / retention / authorizer | Existing self-hosted AKS service supplied as fact; versions/settings **unknown** | Temporal owner provides actual versions/config, mTLS and operation authorization V03 |
| Temporal versioning | Worker Versioning docs rechecked 2026-09-07; initial GetVersion/replay pending hosting capability | Published minimums: Go SDK 1.35.0, self-hosted server 1.29.1, CLI 1.4.1 and UI 2.38.0. Versions alone do not prove configuration or safe rollout; V03 verifies compatibility, patching remains initial fallback |
| KEDA | **2.20** Temporal-scaler docs examined; source notes introduction from 2.17+ | Deployed ACA scaler availability/version/mTLS and safe scale-in **unknown**; fixed replicas proposed |
| ACA | Managed service; deployment API version/profile/grace behavior **unknown** | Application-lifecycle docs describe SIGTERM then 30s window; verify deployed behavior, do not infer a user-controlled KEDA version |
| Terraform CLI | Candidate **1.16.0**, official binary listing verified | Untested for this project. M2 pins binary/checksum and tested provider/module set; no claim this version is already deployed |
| OpenTofu | No version selected | Evaluate only for concrete benefit; no compatibility/equivalence commitment |
| AzureRM / terraform-exec / terraform-json | Exact versions **not selected** | M2 foundation / M4 executor matrix and lock checksums required |
| Azure Batch / SDK | Allocation candidate recorded; API version and Go SDK packages **not selected** | M2 verify management/data-plane API coverage, Entra auth and features before pinning; examples in product docs are not a Go compatibility test |
| VMSS | Fallback only; orchestration mode/API version **unselected** | Evaluate only if Batch gate warrants it; own a separate fleet |
| Worker image / OS / node agent / Docker / Compose | Exact versions **unknown** | V07/V09 must identify and test Gallery image version plus full runtime/software manifest |
| PostgreSQL Flexible Server | Recommended; engine major/minor, region/tier/HA **unselected** | Select supported engine with pgx/migrations/auth tests; record service restore settings |
| Key Vault / Blob / Entra | Documented service patterns inspected; tenant/resource configuration **unknown** | V02/V06/V10 record actual auth endpoints, permissions, private DNS, retention and identity scope |
| Microsoft Graph | **v1.0** List manager page inspected | Application permissions documented unsupported; tenant-approved query/feed still undecided |
| Infracost | Public Cloud price-book/API/FAQ docs inspected; CLI/edition version **unselected** | Vendor auth/licensing/egress/rate tests before selection; no implied self-hosted parity |
| OTEL SDK / collector, golangci-lint, CI actions, Dev Container | **Not selected** | Pin tested versions/digests in M1; enterprise OTLP path V12 |
| Test framework | Proposed Go testing/httptest, Temporal testsuite/replayer, Testcontainers PostgreSQL and pinned Temporal dev fixture | Standard library follows Go pin; module/server/container versions selected and tested in M1.1; no harness installed yet |
| API test/lint tools | Redocly **2.51.2**, PyYAML **6.0.3**, jsonschema **4.26.0** used for M0 docs | Exact commands/results in validation.md; M1 Go request/response validator remains unselected (`kin-openapi` candidate). No dialect downgrade; enterprise rules remain V01 |
| Codex project instructions / subagents / worktrees | Current official guidance consulted for revision 0.2.0; installed client/version/config **not inventoried** | Working agreement and future AGENTS.md are proposed; availability/permission/loading checks in M1.1; no custom agents configured during M0 |
| SOC 2 reference | AICPA 2017 Trust Services Criteria with 2022 revised points of focus | Proposed control-family mapping only; organization's audit scope/version/retention decisions remain authoritative |

An M0 candidate is not a tested toolchain. Recheck security/support status when each milestone begins; replace a candidate if evidence warrants it and record the reviewed reason and exact tested pins. Do not silently follow `latest` in source, image, tool or deployment manifests.

## Reference register

The implication column records the narrow fact or design inference used. Environment evidence is assigned to V IDs in [section 12](12-verification.md). No copied vendor sample is treated as production-ready configuration.

| ID | Official source | Documented point / design implication |
| --- | --- | --- |
| R01 | [Zalando guidelines](https://opensource.zalando.com/restful-api-guidelines/), [changelog](https://github.com/zalando/restful-api-guidelines/blob/main/chapters/changelog.adoc) | Living conventions; proposed local differences identified, authoritative internal pin unavailable |
| R02 | [OpenAPI 3.1.1](https://spec.openapis.org/oas/v3.1.1.html) | Sketch syntax/dialect baseline; separate API info version |
| R03 | [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) | Problem-details representation; safe local code extensions |
| R04 | [Temporal idempotency](https://temporal.io/blog/idempotency-and-durable-execution) | External acknowledgement loss requires idempotency/recovery; outbox/attempt design is this project's proposal |
| R05 | [Self-hosted Temporal security](https://docs.temporal.io/self-hosted-guide/security) | ClaimMapper/Authorizer matter; mTLS or queue names alone do not establish application authorization |
| R06 | [Go error handling](https://docs.temporal.io/develop/go/best-practices/error-handling), [workflow versioning](https://docs.temporal.io/develop/go/workflows/versioning) | Error/cancellation and deterministic change handling; actual timeout/versioning configuration still to test |
| R07 | [Temporal SDK 1.48.0 release](https://github.com/temporalio/sdk-go/releases/tag/v1.48.0), [tagged go.mod](https://raw.githubusercontent.com/temporalio/sdk-go/v1.48.0/go.mod) | Retrieved 2026-09-07; published candidate and Go 1.25.4 floor, not evidence of enterprise compatibility. Release notes include tracing changes to assess in V04 |
| R08 | [ACA lifecycle](https://learn.microsoft.com/en-us/azure/container-apps/application-lifecycle-management) | Termination is expected; fixed min replicas do not remove restart/recovery obligations |
| R09 | [KEDA 2.20 Temporal scaler](https://keda.sh/docs/2.20/scalers/temporal/) | Documents TLS client options and backlog caveats; managed ACA availability needs separate validation |
| R10 | [Batch container workloads](https://learn.microsoft.com/en-us/azure/batch/batch-docker-container-workloads) | Container configuration and identity-registry examples; exact Docker/Compose/privilege behavior not established |
| R11 | [Batch pool managed identities](https://learn.microsoft.com/en-us/azure/batch/managed-identity-pools) | Rechecked 2026-09-07: UAMI pools require management-plane creation; identities are accessible through node IMDS, and active-node identity updates are unsupported |
| R12 | [Batch no-public-IP pools](https://learn.microsoft.com/en-us/azure/batch/simplified-node-communication-pool-no-public-ip) | Simplified communication/private endpoint model and explicit outbound dependencies need deployment tests |
| R13 | [Batch security guidance](https://learn.microsoft.com/en-us/azure/batch/security-best-practices) | Defense-in-depth input, not proof of a single-use workload VM's isolation |
| R14 | [Batch account creation](https://learn.microsoft.com/en-us/azure/batch/batch-account-create-portal) | User-subscription allocation has specific service/permission prerequisites |
| R15 | [Batch Compute Gallery pools](https://learn.microsoft.com/en-us/azure/batch/batch-sig-images) | Use exact Gallery image version and verify node-agent/access/region compatibility |
| R16 | [VMSS orchestration modes](https://learn.microsoft.com/en-us/azure/virtual-machine-scale-sets/virtual-machine-scale-sets-orchestration-modes) | Mode selection is a fallback design dependency; no equivalent Batch behavior assumed |
| R17 | [Docker security](https://docs.docker.com/engine/security/), [rootless mode](https://docs.docker.com/engine/security/rootless/), [authorization plugins](https://docs.docker.com/engine/extend/plugins_authorization/) | Host-daemon authority risk; supervisor-only Compose is the revised recommendation. Rootless alone does not prove image/egress/credential policy; any direct-call broker needs separate scope |
| R18 | [Entra access tokens](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens), [claims](https://learn.microsoft.com/en-us/entra/identity-platform/access-token-claims-reference) | Audience/signature/issuer checks and deliberate scopes/roles/overage handling |
| R19 | [PostgreSQL managed identity](https://learn.microsoft.com/en-us/azure/postgresql/security/security-connect-with-managed-identity) | Hosted identity/token connection pattern; actual DB roles/pooling/reconnect behavior untested |
| R20 | [PostgreSQL backup/restore](https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/concepts-backup-restore) | PITR/retention support, not an application-wide atomic restore or measured RTO |
| R21 | [Key Vault security](https://learn.microsoft.com/en-us/azure/key-vault/general/secure-key-vault) | Application/region/environment boundary recommendation; organizational topology remains a decision |
| R22 | [Blob immutability](https://learn.microsoft.com/en-us/azure/storage/blobs/immutable-storage-overview) | Storage controls for immutable audit; configuration, retention and lock approval still required |
| R23 | [AzureRM backend](https://developer.hashicorp.com/terraform/language/backend/azurerm) | Entra data-plane authentication and native blob locking; exact identity config/scopes need tests |
| R24 | [Provider network mirror](https://developer.hashicorp.com/terraform/internals/provider-network-mirror-protocol) | Mirror protocol/auth is separate from backend access; local verified bundle/mirror proposed |
| R25 | [Terraform lock file](https://developer.hashicorp.com/terraform/language/files/dependency-lock) | Provider locking does not pin the module tree |
| R26 | [Terraform plan](https://developer.hashicorp.com/terraform/cli/commands/plan), [sensitive values](https://developer.hashicorp.com/terraform/language/manage-sensitive-data) | Refreshed saved plans and resource-specific ephemeral/write-only behavior; inspect actual selected providers |
| R27 | [Terraform 1.16.0 binaries](https://releases.hashicorp.com/terraform/1.16.0/), [Go downloads](https://go.dev/dl/) | Candidate published tool versions, not installation/compatibility evidence |
| R28 | [Infracost price books](https://www.infracost.io/docs/infracost_cloud/custom_price_books/), [API](https://www.infracost.io/docs/infracost_cloud/api/), [FAQ](https://www.infracost.io/docs/faq/) | Negotiated pricing mechanisms, token requirement and distinct pricing/dashboard data flows; vendor deployment decisions unverified |
| R29 | [Azure cost data](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/understand-cost-mgt-data) | Delays/corrections/attribution require a reconciled nonoverlapping ledger |
| R30 | [Graph v1.0 List manager](https://learn.microsoft.com/en-us/graph/api/user-list-manager?view=graph-rest-1.0) | Application permission unsupported in inspected documentation; actual approved directory path unresolved |
| R31 | [Azure privileged roles](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged) | Contributor versus role-assignment authority; constrain escalation explicitly |
| R32 | [GitHub Azure OIDC](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-azure) | Federated Azure access; GitHub cross-repo/Check permissions require their own evidence |
| R33 | [OPA integration](https://www.openpolicyagent.org/docs/integration), [CEL](https://cel.dev/) | Alternatives to a small Go policy implementation, not automatic dependencies |
| R34 | [AICPA criteria](https://www.aicpa-cima.com/resources/download/2017-trust-services-criteria-with-revised-points-of-focus-2022) | Reference for proposed control mapping; no compliance certification or invented retention obligation |
| R35 | [Official AGENTS.md guidance](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents), [worktrees](https://learn.chatgpt.com/docs/environments/git-worktrees) | Repository guidance, explicit delegation and checkout isolation inform the proposed engineering agreement; no runtime agent dependency or automatic workspace isolation assumed |
| R36 | [Go testing](https://pkg.go.dev/testing), [httptest](https://pkg.go.dev/net/http/httptest), [Temporal Go testing](https://docs.temporal.io/develop/go/best-practices/testing-suite), [Testcontainers PostgreSQL](https://golang.testcontainers.org/modules/postgres/) | Named local test layers; unit/time-skipping tests do not prove actual DB/server restart behavior |
| R37 | [Redocly CLI](https://github.com/Redocly/redocly-cli), [Spectral](https://github.com/stoplightio/spectral), [kin-openapi](https://github.com/getkin/kin-openapi) | Pinned Redocly now performs local M0 structure/examples lint; enterprise rules and Go runtime validation are separate choices. Validation command disables Redocly telemetry |
| R38 | [Go fuzzing](https://go.dev/doc/security/fuzz/), [race detector](https://go.dev/doc/articles/race_detector), [govulncheck](https://go.dev/doc/security/vuln/) | Proposed complementary checks; no test results or blanket security guarantee inferred from tool selection |
| R39 | [Batch quotas](https://learn.microsoft.com/en-us/azure/batch/batch-quota-limit) | Published pool/job/core distinctions are not actual target quota inventory; deleting/unknown pools still affect experiment admission |
| R40 | [Batch best practices](https://learn.microsoft.com/en-us/azure/batch/best-practices) | Internal node-failure retries are distinct from maxTaskRetryCount; pool is the security isolation boundary; Batch-service allocation is the usual default |
| R41 | [Cosign verification](https://docs.sigstore.dev/cosign/verifying/verify/) | Candidate verification mechanism for pinned images/attestations; signer/root/rotation and offline-path approval still V09 |
| R42 | [Temporal Worker Versioning](https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning) | Published minimum versions above; production rollout recommendation depends on actual deployment topology/configuration, not version number alone |

## Retrieval limitations

The attempted Batch `pool-allocation-mode` page and version-qualified pkg.go.dev SDK page did not load; account-creation/Gallery official pages and SDK release notes provided narrower usable evidence instead. The requested Temporal failure-detection/versioning URLs redirected to current Go documentation; those current URLs are recorded. Public pages do not expose tenant settings, quotas, identity roles, deployed API versions, exact ACA scaler support, or the organization's API lint profile. Those remain open rather than inferred from documentation.

For revision 0.2.0, the former developers.openai.com Codex instruction/subagent/worktree URLs redirected to official ChatGPT Learn documentation; the resolved pages above were read. A Stoplight documentation URL failed; the official Spectral repository supplied the narrower tooling evidence. Neither fallback establishes the behavior of a version not yet selected/tested here.
