# Diagnostic, creates nothing: proves Terraform can sign in to Azure as whatever identity the
# worker runs as (managed identity when hosted, certificate locally) AND read/write remote state.
# Needs no pattern repo, so it works before any GitHub access is configured.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  backend "azurerm" {}
}

provider "azurerm" {
  features {}
  resource_provider_registrations = "none"
}

variable "note" {
  description = "Free text stored in state, to prove the state blob is written"
  type        = string
  default     = "identity check"
}

data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {}

resource "terraform_data" "note" {
  input = var.note
}

output "signed_in_object_id" { value = data.azurerm_client_config.current.object_id }
output "tenant_id" { value = data.azurerm_client_config.current.tenant_id }
output "subscription" { value = data.azurerm_subscription.current.display_name }
output "note" { value = terraform_data.note.output }
