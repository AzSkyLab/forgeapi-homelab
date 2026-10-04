locals {
  # Platform tags are applied last so a caller cannot override them. Unset (null) values are dropped.
  tags = merge(
    var.extra_tags,
    { for k, v in {
      Environment  = var.environment
      BusinessUnit = var.business_unit
      CostCenter   = var.cost_center
      ManagedBy    = "forgeapi"
    } : k => v if v != null }
  )
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${var.name}"
  location = var.region
  tags     = local.tags
}

resource "azurerm_storage_account" "this" {
  name                     = var.name
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = var.replication_type

  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = var.blob_retention_days
    }

    container_delete_retention_policy {
      days = var.blob_retention_days
    }
  }

  tags = local.tags
}

resource "azurerm_storage_container" "data" {
  name                  = "data"
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

# Only when the environment provides a subnet (`network.private_endpoint_subnet_id` in the
# business unit's configuration). A private endpoint costs a few dollars a month, which is why
# config.yaml's prd estimates are higher than dev's.
resource "azurerm_private_endpoint" "blob" {
  count = var.private_endpoint_subnet_id == null ? 0 : 1

  name                = "pe-${var.name}-blob"
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  subnet_id           = var.private_endpoint_subnet_id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-${var.name}-blob"
    private_connection_resource_id = azurerm_storage_account.this.id
    subresource_names              = ["blob"]
    is_manual_connection           = false
  }

  dynamic "private_dns_zone_group" {
    for_each = var.blob_private_dns_zone_id == null ? [] : [1]

    content {
      name                 = "blob"
      private_dns_zone_ids = [var.blob_private_dns_zone_id]
    }
  }
}
