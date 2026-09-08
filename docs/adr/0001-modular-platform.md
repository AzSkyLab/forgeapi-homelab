# ADR-0001 — Modular platform with separate API and workers

**Status:** Proposed. **Date:** 2026-09-05. **Scope:** M1–M3. [Design](../design/02-architecture.md).

## Context

**Implementation note, 2026-09-07:** Go/chi and separate local API/worker roles run via Compose. ACA/runtime-role deployment remains future. The native lab Terraform worker is a separately scoped exception in [ADR-0013](0013-local-terraform-lab-exception.md), not hosted acceptance.

Two engineers need shared platform concerns and a working first capability. Temporal already exists on AKS, while the API must run elsewhere. Workload VMs and Temporal activity workers have different trust and operational requirements.

## Decision

Recommend Go/chi in one monorepo, separately deployed API and role-configured worker processes on VNet-integrated ACA. Keep domain/application services modular and adapters at actual boundaries. Run the fake without provisioning rights; separate live provider worker identity/deployment by approved target trust group. Use the existing Temporal service after access verification. PostgreSQL and object storage are shared platform services with distinct authorities.

The shared HTTP binary also has a separately deployed private runtime role/identity for assignment, launch claims and transfer, beginning M2.1. M1 is laptop/local-fixture plus connected dev evidence; M2.0 explicitly deploys ACA roles and tests mTLS/ingress/shutdown. No separate listener is treated as a privilege boundary.

## Alternatives

Echo is acceptable if confirmed team standard outweighs chi preference. Independently operated domain microservices add coordination cost without a demonstrated first-slice need. API hosting on existing AKS conflicts with the supplied placement constraint.

## Consequences

Shared code/deployment pipelines keep the application small; separate deployments preserve compute privilege boundaries. Role configuration must be validated so an API deployment cannot load a provider identity or poll a privileged queue. Later capabilities add modules rather than speculative frameworks.

## Verification dependencies

V02–V04/V14/V15/V20: Entra, Temporal authorization/connectivity, durable fake slice, ACA ingress/shutdown, real repository ownership and pinned tooling. Both engineers' signoff is pending.
