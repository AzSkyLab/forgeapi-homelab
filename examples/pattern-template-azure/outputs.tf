# Outputs are for consumers of the deployment. Never output the subscription ID or anything
# that embeds it (resource IDs contain it). A sensitive output is withheld by the platform
# (`withheld_outputs`) and is never retrievable.

output "storage_account_name" {
  description = "Name of the storage account."
  value       = azurerm_storage_account.this.name
}

output "primary_blob_endpoint" {
  description = "Blob service endpoint."
  value       = azurerm_storage_account.this.primary_blob_endpoint
}

output "container_name" {
  description = "Name of the private container."
  value       = azurerm_storage_container.data.name
}

output "resource_group_name" {
  description = "Name of the resource group."
  value       = azurerm_resource_group.this.name
}

# Secrets: never output a secret value. Store it in the pattern's own Key Vault and output a
# REFERENCE that the consumer resolves with its own identity (for example a Key Vault
# reference in an app setting). A vault costs almost nothing, but this template does not
# create one; the platform's key-vault pattern is the usual place. Example, if the pattern
# owned a vault and a generated secret:
#
# resource "azurerm_key_vault_secret" "connection" {
#   name         = "storage-connection"
#   value        = azurerm_storage_account.this.primary_connection_string
#   key_vault_id = azurerm_key_vault.this.id
# }
#
# output "connection_secret_uri" {
#   description = "Key Vault secret URI of the connection string. A reference, not the value."
#   value       = azurerm_key_vault_secret.connection.versionless_id
# }
