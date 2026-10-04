# Test-only HA replica (Azure side). Floci Azure answers 501 to Set Blob Properties, which
# azurerm_storage_blob always calls, so no blob is created: the replica is the account and a
# container, and the endpoint is the account's blob host.
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
  description = "Replica name: 3-24 lowercase letters and digits"
  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.name))
    error_message = "Use 3-24 lowercase letters and digits."
  }
}

variable "app_version" {
  type        = string
  description = "Version text recorded as a tag"
  default     = "1"
}

variable "subscription_id" {
  type = string
}

variable "region" {
  type = string
}

resource "azurerm_resource_group" "app" {
  name     = var.name
  location = var.region
}

resource "azurerm_storage_account" "app" {
  name                     = var.name
  resource_group_name      = azurerm_resource_group.app.name
  location                 = var.region
  account_tier             = "Standard"
  account_replication_type = "LRS"
  tags                     = { app_version = var.app_version }
}

resource "azurerm_storage_container" "app" {
  name                  = "app"
  storage_account_name  = azurerm_storage_account.app.name
  container_access_type = "private"
}

output "name" {
  value = azurerm_resource_group.app.name
}

output "endpoint" {
  value = azurerm_storage_account.app.primary_blob_host
}

output "health_path" {
  value = "/${azurerm_storage_container.app.name}?restype=container"
}
