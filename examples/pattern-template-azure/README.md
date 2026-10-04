# pattern-template-azure

Copy-ready ForgeAPI pattern: a resource group with a private, versioned storage account
(TLS 1.2, no public blob access) and a private container. When the environment provides a
subnet, it also creates a blob private endpoint. Copy this directory into a new git repository
and rename it (`terraform-azurerm-<thing>` is a good convention). The AWS and GCP templates
(`pattern-template-aws`, `pattern-template-gcp`) have the same structure; this README is the
full guide.

## What it creates

| Resource | Notes |
|---|---|
| `azurerm_resource_group` | `rg-<name>` |
| `azurerm_storage_account` | StorageV2, TLS 1.2, HTTPS only, no public blobs, versioning and soft delete |
| `azurerm_storage_container` | `data`, private |
| `azurerm_private_endpoint` | Only when `private_endpoint_subnet_id` is injected |

No Key Vault, no provisioners, no external data sources.

## Inputs

| Variable | Who sets it | Type | Notes |
|---|---|---|---|
| `name` | **Caller** | string | 3 to 24 lowercase letters and digits; globally unique |
| `extra_tags` | **Caller** (optional) | map(string) | Cannot override platform tags |
| `subscription_id` | Platform | string | From the environment's cloud target. Callers cannot set it |
| `region` | Platform | string | From the same cloud target. Callers cannot set it |
| `environment` | Platform | string | Tag. Callers cannot set it |
| `business_unit`, `cost_center` | Platform | string | Business unit `inject:` keys; tags. Callers cannot set them |
| `private_endpoint_subnet_id`, `blob_private_dns_zone_id` | Platform | string | Environment `network:` keys; optional. Callers cannot set them |
| `replication_type`, `blob_retention_days` | Size | string, number | From `sizing` in `config.yaml`; callers choose `size` |

The platform injects a variable only if the pattern declares it, and hides injected variables
from callers. Supported variable types are `string`, `number`, `bool`, `list(string)`,
`set(string)`, `list(number)` and `map(string)`. A `validation` block becomes a caller-facing
error with your `error_message`; simple conditions (`can(regex(...))`, `contains([...], v)`,
numeric bounds, `length(v)` bounds) also appear in the API schema.

## Outputs

`storage_account_name`, `primary_blob_endpoint`, `container_name`, `resource_group_name`.
Outputs never include the subscription ID or anything embedding it (resource IDs do).
A sensitive output is never stored, returned or logged; the API reports only its name in
`withheld_outputs`.

### Secrets

Never output a secret value. Keep it in the pattern's own Key Vault and output a reference such
as `azurerm_key_vault_secret.<x>.versionless_id`. A commented example is in `outputs.tf`.
There is no reveal endpoint: the consumer reads the secret with its own identity.

## State

`versions.tf` declares an empty `backend "azurerm" {}`. The platform injects the storage
account, container and key for each deployment; keep the block empty and never add credentials.

## Versioning

- Releases are git tags `vMAJOR.MINOR.PATCH`. Never move or delete a published tag; publish a new one.
- Deployments are pinned to a commit. A change reaches a deployment only when it is upgraded to a newer tag.
- Add an entry to `CHANGELOG.md` for every tag. Changes that rename or remove a resource, or change an input, are a major or minor bump, never a patch.
- Commit `.terraform.lock.hcl` (run `terraform init -backend=false`) so provider versions are reproducible.
- Update `estimated_costs` whenever resources or sizes change.

## Onboarding

1. From the ForgeAPI repository, check the pattern directory:

   ```bash
   uv run python -m app.pattern_check /path/to/this-repo --cloud azure --terraform
   ```

2. Commit, push and tag: `git tag v1.0.0 && git push origin v1.0.0`.
3. Add the pattern to ForgeAPI's `patterns.yaml`:

   ```yaml
   patterns:
     storage-account:
       repo: github.com/YOUR-ORG/terraform-azurerm-storage-account
       default_version: v1.0.0   # omit to follow the newest tag
       cloud: azure
   ```

4. Allow it for a business unit in `tenants.yaml` (the environment names must match the keys in `sizing` and `estimated_costs`):

   ```yaml
   business_units:
     finance:
       patterns: [storage-account]
       inject:
         business_unit: finance
         cost_center: "CC-1042"
       environments:
         dev:
           targets:
             azure: {subscription_id: 00000000-0000-0000-0000-0000000000aa, region: eastus2}
           budget_monthly: 500
         prd:
           targets:
             azure: {subscription_id: 00000000-0000-0000-0000-0000000000bb, region: eastus2}
           network:
             private_endpoint_subnet_id: /subscriptions/.../subnets/snet-private-endpoints
             blob_private_dns_zone_id: /subscriptions/.../privateDnsZones/privatelink.blob.core.windows.net
   ```

5. Verify through the API: `GET /patterns/storage-account/check?version=v1.0.0`.
6. Deploy to dev, check the outputs and plan, then promote to prd.

## CI

`.github/workflows/pattern-ci.yml` runs `terraform fmt -check`, `init -backend=false`,
`validate` and the ForgeAPI checker on pull requests and tags. It needs no secrets. It checks
out the ForgeAPI repository: set the repository variable `FORGEAPI_REPO` (owner/name) and pin
`FORGEAPI_REF`. A private ForgeAPI repository needs a read-only token (see the commented
`token:` line).
