# Multi-cloud HA apps

A highly available app is a set of ordinary resources in one landing zone (business unit ×
environment) that share the label `app=<name>`:

- **Replicas** (`app_role=replica`): the same app deployed once per cloud/region, each from a
  pattern that outputs `endpoint` (a hostname) and `health_path`.
- **Router** (`app_role=router`): a global failover pattern whose inputs come from the replicas
  through `input_refs`: `primary_endpoint`, `secondary_endpoint`, `primary_health_path`,
  `secondary_health_path`. Each replica keeps its own health path; one shared path would mark the
  other cloud's replica unhealthy and failover would never happen.

No new endpoint is involved. The platform's existing rules give the safety:

| Concern | How the API handles it |
| --- | --- |
| Placement | Each replica pattern declares its cloud; the landing zone injects that cloud's account/subscription and region. |
| Budget | Every replica and the router reserve their estimate against the zone's budget. |
| Removing a region the router uses | Destroying a referenced replica is refused (409 `resource_referenced`) until the router no longer points at it. |
| Cross-cloud wiring | `input_refs` copy public outputs across clouds within one landing zone; withheld outputs cannot be referenced. |
| Review | Every change, including failover, is a plan with an exact digest that someone approves. |

## One request: `POST /apps`

`POST /apps` (capability `app_rollouts`) does the recipe below for you: it accepts all replicas
atomically, a Temporal workflow waits for them, then accepts and plans the router with
`replica_refs` resolved to `input_refs`. You approve twice with exact digests
(`POST /apps/{id}/approve`): all replica plans, then the router plan. Nothing applies without
that approval. `GET /apps/{id}` shows the rollout state and every member.

**Failover:** `POST /apps/{id}/failover` with `{"primary": 1}` re-plans the router with every
`primary_*` input pointing at replica 1 and every `secondary_*` input at the old primary; approve
it at the router gate. **Teardown:** `POST /apps/{id}/destroy` destroys the router first, then the
replicas, each behind its own approval gate; landing-zone guardrails apply.

## Recipe (manual)

1. `POST /operations` a replica intent per cloud with `labels: {"app": "storefront",
   "app_role": "replica"}`; review and execute each plan.
2. `POST /operations` the router intent with `input_refs` pointing at the replicas' `endpoint` and
   `health_path` outputs and `labels: {"app": "storefront", "app_role": "router"}`; execute.
3. **Failover drill:** submit the router intent again with `resource_id` set and the primary and
   secondary refs swapped. The plan shows in-place updates to the failover records and health
   checks only (no zone replacement); approve it.
4. Watch it in the developer portal: `GET /console#apps/storefront` shows redundancy (replicas
   ready), PRIMARY/SECONDARY, the router's `app_fqdn`, member operations and a link to review a
   pending failover plan.

## Evidence and limits

Proven on Floci (AWS S3 + Azure Storage replicas, Route 53 failover router) through the real API,
Temporal, worker and Terraform: `tests/test_floci_ha_app.py` and a live demo where the failover
was approved from the portal and Route 53 read back PRIMARY → Azure. Test patterns live in
`examples/floci-ha-*`; real patterns belong in their own repos.

- Floci does not evaluate Route 53 health checks, so automatic failover on a real outage is not
  proven; the drill is the operator-driven swap.
- Floci Azure cannot store blobs (501 on Set Blob Properties), so the Azure replica serves a
  container; a real Azure replica should use a static website over HTTPS.
- Real cloud: never run from the home lab (engineer rule). The real-cloud run happens after the
  transfer to the work environment; there a Route 53 hosted zone and each health check are
  billed (about $0.50/month each).
- Not built yet: continuous health status in the API and an automatic failback policy.
