terraform {
  required_version = "= 1.15.9"
  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "= 2.11.0"
    }
  }
  # State stays in the trusted host runner's restricted, persistent workspace.
  backend "local" {}
}

provider "azapi" {
  subscription_id            = var.subscription_id
  tenant_id                  = var.tenant_id
  client_id                  = var.executor_client_id
  use_cli                    = false
  use_msi                    = false
  use_oidc                   = false
  use_aks_workload_identity  = false
  skip_provider_registration = true
}

# ARM-only: no data-plane requests, secrets, keys, certificates or RBAC grants.
resource "azapi_resource" "vault" {
  type      = "Microsoft.KeyVault/vaults@2025-05-01"
  parent_id = var.resource_group_id
  name      = var.name
  location  = var.location
  tags      = { managed_by = "forgeapi", purpose = "empty-vault-demo" }
  body = {
    properties = {
      tenantId                  = var.tenant_id
      sku                       = { family = "A", name = "standard" }
      enableRbacAuthorization   = true
      accessPolicies            = []
      enableSoftDelete          = true
      softDeleteRetentionInDays = 7
      # The ARM API rejects explicit false, including during creation.
      # Omission leaves purge protection unenabled for this disposable demo.
      enabledForDeployment         = false
      enabledForDiskEncryption     = false
      enabledForTemplateDeployment = false
      publicNetworkAccess          = "Disabled"
      networkAcls = {
        bypass              = "None"
        defaultAction       = "Deny"
        ipRules             = []
        virtualNetworkRules = []
      }
    }
  }
  response_export_values = ["properties.vaultUri", "properties.provisioningState"]
  lifecycle { prevent_destroy = true }
}

output "resource_id" { value = azapi_resource.vault.id }
output "vault_uri" { value = azapi_resource.vault.output.properties.vaultUri }
