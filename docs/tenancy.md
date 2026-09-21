# Placement and access: business units

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

## Discovery

`GET /me` → the caller's BUs, the environments they can deploy to, regions and patterns. `GET /patterns` is filtered to what the caller can use. `GET /patterns/{name}?business_unit=&environment=` shows the schema as that caller will see it: platform-supplied inputs removed, `location` limited to allowed regions, available sizes listed. `GET /deployments` lists the caller's BUs' deployments.

## Not in scope yet

Per-pattern version limits per BU; quotas/budgets; approval steps for prd; an admin API for the mapping; audit log beyond the deployment record.
