# Test-only root module. Azure Stack discovery and emulated MSI both stay on loopback.
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "4.65.0"
    }
  }
}

provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
  use_cli                        = false
  use_msi                        = true
  msi_endpoint                   = "http://localhost:4577/metadata/identity/oauth2/token"
  environment                    = "stack"
  metadata_host                  = "localhost:4577"
  subscription_id                = "00000000-0000-0000-0000-000000000001"
  tenant_id                      = "00000000-0000-0000-0000-000000000002"
}

variable "name" {
  type        = string
  description = "Unique test resource group name"
}

resource "azurerm_resource_group" "test" {
  name     = var.name
  location = "eastus"
}

output "name" {
  value = azurerm_resource_group.test.name
}
