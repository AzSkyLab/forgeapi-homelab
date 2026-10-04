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
  use_cli                         = false
  use_msi                         = true
  msi_endpoint                    = "http://localhost:4577/metadata/identity/oauth2/token"
  environment                     = "stack"
  metadata_host                   = "localhost:4577"
  subscription_id                 = var.subscription_id
  tenant_id                       = "00000000-0000-0000-0000-000000000002"
}

variable "name" {
  type        = string
  description = "Storage name: 3-24 lowercase letters and digits"
  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.name))
    error_message = "Use 3-24 lowercase letters and digits."
  }
}

resource "azurerm_resource_group" "test" {
  name     = var.name
  location = var.region
}

resource "azurerm_storage_account" "test" {
  name                     = var.name
  resource_group_name      = azurerm_resource_group.test.name
  location                 = var.region
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_storage_container" "test" {
  name                  = "data"
  storage_account_name  = azurerm_storage_account.test.name
  container_access_type = "private"
}

output "name" {
  value = azurerm_resource_group.test.name
}

output "container_name" {
  value = azurerm_storage_container.test.name
}

variable "subscription_id" {
  type = string
}

variable "region" {
  type = string
}

output "placement_id" {
  value = var.subscription_id
}
