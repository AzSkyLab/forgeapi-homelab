# The subscription comes from the platform (see variables.tf). No credentials here: the engine
# signs in with its managed identity (or a short-lived certificate for local runs).
provider "azurerm" {
  subscription_id = var.subscription_id

  features {}
}
