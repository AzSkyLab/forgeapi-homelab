terraform {
  required_version = ">= 1.9, < 2.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  # Empty on purpose: the platform injects the storage account, container and key for every
  # deployment, so state is remote and per deployment and version upgrades work.
  # Do not put a storage account name, key or SAS token here.
  backend "azurerm" {}
}
