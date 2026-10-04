# Multi-cloud placement for the operation API

Current as of: 2026-10-02. The rules below apply to `app.main:app` (`agent-v1`).

A trusted catalog entry declares `cloud: azure`, `cloud: aws`, or `cloud: gcp`. An agent
chooses the pattern and its business unit/environment. The platform supplies the cloud
identity and region from the mapping; neither is a request field. The pattern description
returns its cloud and hides injected variables from the JSON schema.

```yaml
# patterns.yaml — repositories remain independently versioned and commit-pinned
patterns:
  object-storage:
    repo: github.com/example/terraform-storage
    cloud: aws

# tenants.yaml — private, platform-owned configuration
business_units:
  finance:
    groups: [finance-developers]
    patterns: [object-storage]
    environments:
      dev:
        targets:
          azure: {subscription_id: "<subscription>", region: eastus}
          aws: {aws_account_id: "<account>", region: us-east-1}
          gcp: {project_id: "<project>", region: us-central1}
```

Each cloud target has exactly its identifier and `region`, both nonempty strings. A pattern
must declare those two Terraform variables. They are injected after other platform inputs,
removed from the caller schema, and caller overrides return 422. A missing or malformed target,
a missing pattern variable, or a pattern without `cloud` in an environment with targets fails
closed with 503. Region is fixed by this target in this milestone. Legacy subscription-only
mappings and catalog entries without a cloud continue to work as before.

**Provider contract:** trusted Azure patterns wire `subscription_id` into the provider and
`region` into resource location; GCP patterns wire `project_id` and `region` into the provider;
AWS patterns wire `region` and `allowed_account_ids = [var.aws_account_id]` into the provider.
The AWS executor identity selects its account; the supplied ID is an account guard, not a
credential or an assume-role mechanism. The API checks variable declarations, not arbitrary
Terraform semantics. Review the trusted module's provider wiring before registration.

The operation and resource store their cloud/target privately. The same resource cannot move
between targets. Execution, update and destroy recheck the current mapping and catalog cloud;
a changed target returns 409 without dispatch. Restore the authorized mapping or perform an
explicit operator-managed migration before resuming. An already dispatched activity uses its
accepted target; config changes cannot revoke work that has already started. Sensitive outputs
and outputs containing a target identifier are withheld; only their names are exposed.

New Terraform plan summaries are also checked for known placement identifiers. A resource
address containing an injected target ID causes a generic planning failure before public
changes or an executable digest are saved. Use non-sensitive `for_each` keys in pattern repos.
Private placement data remains available to execution; historical summaries are not rewritten.

`POST /operations` and explicit digest execution retain the existing Temporal run-once rules.
Audit remains append-only, and BU membership gates reads while environment membership gates
changes. Budgets reserve atomically in SQLite. No real AWS/GCP credential selection or workload
identity is introduced by this placement milestone.

Resolved numeric cost estimates must be finite and nonnegative. A selected `budget_monthly`
must be a finite nonnegative number, excluding booleans; omitted/None means unlimited and zero
is valid. Invalid numeric configuration receives sanitized 503 before operation acceptance.
An environment with a budget still refuses a missing estimate with 403. Existing cost selection
and fallback remain unchanged. These operation-policy checks do not rewrite legacy settings
or historical cost records.

A present selected `estimated_costs.<size>` entry must be a mapping; null, scalar, boolean
or list entries return fixed 503. Missing sizes retain environment/global fallback, and
requests without a size ignore unrelated size entries. Valid selection is unchanged.
The shared resolver also returns controlled 503 for these malformed legacy requests.

New admissions validate every contributing stored estimate and the aggregate before budget
arithmetic. Invalid accounting returns sanitized 503 without acceptance or dispatch. Legacy
rows are read without modification. Validation-only can still succeed when operation-ledger
accounting is invalid: its transactional reservation check runs on submission. Exact-key replay
and destroy remain available. Stored NULL counts as zero; original NaN/boolean types already
normalized by SQLite cannot be reconstructed.


The storage examples use only Floci and dummy identifiers. Verified patterns: Azure Storage
account plus private blob container and supporting resource group; AWS S3 bucket with account
guard; GCP storage bucket. Deployment: [placement demo](../deploy/emulator-placement/README.md).

---

# Legacy deployment API placement reference

The following documents `app.legacy:app` and its `/deployments` routes. Its retry, discovery
and budget limitations do not describe the operation API above.


**Goal:** callers say *what* they want; the platform decides *where* it goes and *whether they may*. A caller never supplies or sees a subscription ID, network ID or cost centre. Granting a team access to the API **is** adding them to the mapping.

Off by default: with no `FORGEAPI_TENANTS_PATH`, the API behaves as before (one subscription, no ownership), which keeps local development simple.

## Decisions (engineer, 2026-09-20)

| Question | Decision |
| --- | --- |
| Caller → business unit | Entra **group** membership; the mapping lists group object IDs |
| What a BU maps to | **BU + environment → subscription** |
| The mapping also controls | injected inputs, allowed patterns, who may deploy to which environment, allowed regions, predefined network values (subnets etc.) per BU/environment |
| Caller in several BUs | optional `business_unit` in the request; implied when they belong to exactly one |
| T-shirt sizes | defined **and** limited per environment by the **pattern** (`config.yaml`), not by the mapping |
| Size vs. explicit inputs | a size **locks** the inputs it sets; callers cannot override them |
| Storage | YAML file now; a database at work later, so it sits behind `app/tenants.py` |

## Mapping file

```yaml
business_units:
  finance:
    groups: [<entra-group-object-id>]            # membership = access to this BU
    inject: { business_unit: finance, cost_center: "CC-1042" }
    patterns: [key-vault, resource-group]        # others are invisible to this BU
    regions: { allowed: [eastus2, centralus], default: eastus2 }
    environments:
      dev:
        subscription_id: <guid>
        network: { private_endpoint_subnet_id: /subscriptions/.../subnets/snet-pe }
      prd:
        subscription_id: <guid>
        groups: [<release-group-object-id>]      # if set, BU membership alone is not enough
        network: { private_endpoint_subnet_id: /subscriptions/.../subnets/snet-pe }
```

## Rules

1. **Who is the caller.** Group IDs come from the Entra token (`groups` claim), from Easy Auth's `X-MS-CLIENT-PRINCIPAL` header (`FORGEAPI_AUTH_MODE=easyauth`, only safe behind Easy Auth, which strips client-supplied copies), or from `FORGEAPI_DEV_GROUPS` when auth is off locally. A token with group *overage* (too many groups to list) is refused with a clear message; configure the app registration to emit only groups assigned to the application.
2. **Which BU.** The BUs whose `groups` (or any environment's `groups`) include the caller. None → 403. Several and no `business_unit` named → 422 listing them.
3. **May they deploy there.** `environment` is required. It must exist for the BU; if that environment lists `groups`, the caller must be in one of them. The pattern must be in the BU's `patterns`. `location`, when the pattern has it, must be in `regions.allowed`; when omitted, `regions.default` is used (not the pattern's own default).
4. **Injection.** Candidates are `inject`, the environment's `network`, `environment`, and the values of the chosen size. A candidate is injected **only if the pattern declares a variable of that exact name**; that variable then disappears from the caller-facing schema and a caller who sends it gets 422. Patterns opt in to platform values simply by declaring the variable.
5. **Sizes.** `config.yaml` → `sizing.<size>.<environment>.<variable>`. A size is available in an environment only if it has an entry for it. If the pattern defines sizing, `size` is required.
6. **Where it runs.** Terraform runs with `ARM_SUBSCRIPTION_ID` = the environment's subscription. State stays in the platform's storage account (its subscription is pinned in the backend config).
7. **Ownership.** The record stores BU, environment, subscription, size, injected values and who asked. Reading a deployment needs membership of its BU; update, retry and destroy also need the environment's deploy right. Others get 404, not 403, so IDs do not leak across BUs.
8. **Re-evaluation.** `PUT` and `retry` recompute placement and injection from the current mapping, so a corrected subnet or cost centre is picked up. The subscription of an existing deployment never changes; if the mapping now points elsewhere the request is refused.

## Budgets

Decisions (engineer, 2026-09-20): limit the **estimated monthly cost**, per **business unit and environment**; measure against the **pattern's own estimates** now, real Azure spend later; **refuse** what does not fit, with a clear message.

- `environments.<env>.budget_monthly` in the mapping. Absent = unlimited. Currency is whatever the pattern authors use; the platform only compares numbers.
- A deployment's cost is read from the pattern's `config.yaml` at the pinned commit: `estimated_costs.<size>.<environment>`, else `estimated_costs.<environment>`, else a single number. It is stored on the record when the request is accepted, so later edits to the pattern do not silently change what is committed.
- **Committed** = the sum for every deployment in that BU/environment that is not `destroyed`. In-flight and **failed** deployments count: a failed deployment may hold resources, and destroying it is what frees the budget.
- A request that would push committed above the budget is refused (403) with the budget, what is committed, what this request adds and what is available. Dry run gives the same answer without creating anything. `PUT` and `retry` do not count the deployment's own current cost twice; a `PUT` to a larger size must fit the difference.
- Where a budget is set, a pattern that declares **no** estimate is refused: treating unknown as free would let exactly the unpriced patterns bypass the budget. Declare `estimated_costs: 0` for genuinely free patterns (the two built-in examples do).
- Callers see their position in `GET /me` (`budgets`), on the pattern page (`estimated_monthly_cost` per size, `budget`) and in dry runs.

Limits: estimates are not bills; two simultaneous requests can both pass the check and overshoot slightly (no cross-request lock); no BU-wide total, counts, per-pattern caps or expiry yet; actual spend from Cost Management (by the injected `BusinessUnit` tag) is not built.

## Discovery

`GET /me` → the caller's BUs, the environments they can deploy to, regions and patterns. `GET /patterns` is filtered to what the caller can use. `GET /patterns/{name}?business_unit=&environment=` shows the schema as that caller will see it: platform-supplied inputs removed, `location` limited to allowed regions, available sizes listed. `GET /deployments` lists the caller's BUs' deployments.

## Not in scope yet

Per-pattern version limits per BU; deployment counts, per-pattern caps and expiry; actual-spend reporting; approval steps for prd. (An operator admin API and portal for teams now exist, with `FORGEAPI_TENANTS_SOURCE=db`; see work-deployment.md. The audit trail now exists: [audit.md](audit.md).)

## Guardrails

An environment may add `protected_resource_types` (Terraform types that a plan may not delete
or replace there) and `allow_destroy: false` (no destroy intents there). Both are enforced by
the API: protected types when the plan is executed, destroy at submission. See
`tenants.example.yaml` and [the architecture](agent-architecture.md).

## Operators

Top-level `operators` lists Entra groups whose members may resolve an `uncertain` operation in
any business unit through `POST /operations/{id}/reconcile`. It grants nothing else: no reads
beyond their own units, no deploys. Without a tenant mapping, the caller who created the
operation may reconcile it. See [the architecture](agent-architecture.md).
