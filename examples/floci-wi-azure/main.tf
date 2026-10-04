# Test-only root module: AKS workload identity against the Floci Azure emulator. Credentials come
# from the ARM_* / AZURE_FEDERATED_TOKEN_FILE environment; only Azure Stack discovery is set here.
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
  use_cli                         = false
  environment                     = "stack"
  metadata_host                   = "localhost:4577"
  subscription_id                 = "00000000-0000-0000-0000-000000000001"
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
