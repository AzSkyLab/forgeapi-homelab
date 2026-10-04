# ---- Set by the platform: callers cannot set these --------------------------------------------

variable "subscription_id" {
  type        = string
  description = "Azure subscription to deploy into. Set by the platform; callers cannot set it."
}

variable "region" {
  type        = string
  description = "Azure region. Set by the platform; callers cannot set it."
}

variable "environment" {
  type        = string
  default     = null
  description = "Environment name (for example dev or prd). Set by the platform; callers cannot set it. Used for tags."
}

variable "business_unit" {
  type        = string
  default     = null
  description = "Owning business unit. Injected by the platform from the unit's configuration; callers cannot set it. Used for tags."
}

variable "cost_center" {
  type        = string
  default     = null
  description = "Cost center for charge-back. Injected by the platform from the unit's configuration; callers cannot set it. Used for tags."
}

# Environment network keys. The platform injects any `network:` key of the environment that
# matches a variable declared here (see docs/tenancy.md). Optional, so the pattern also works
# in an environment that has no network configuration.
variable "private_endpoint_subnet_id" {
  type        = string
  default     = null
  description = "Subnet for the storage account's private endpoint. Set by the platform per environment; callers cannot set it. Null creates no private endpoint."
}

variable "blob_private_dns_zone_id" {
  type        = string
  default     = null
  description = "Private DNS zone (privatelink.blob.core.windows.net) for the private endpoint. Set by the platform per environment; callers cannot set it."
}

# ---- Set by the pattern's sizing (config.yaml): callers choose a size, not these values -------

variable "replication_type" {
  type        = string
  default     = "LRS"
  description = "Storage replication: LRS or ZRS. Set by the chosen size."

  validation {
    condition     = contains(["LRS", "ZRS"], var.replication_type)
    error_message = "replication_type must be LRS or ZRS."
  }
}

variable "blob_retention_days" {
  type        = number
  default     = 7
  description = "Days a deleted blob or container can be recovered. Set by the chosen size."

  validation {
    condition     = var.blob_retention_days >= 1 && var.blob_retention_days <= 365
    error_message = "blob_retention_days must be between 1 and 365."
  }
}

# ---- Caller inputs ---------------------------------------------------------------------------

variable "name" {
  type        = string
  description = "Storage account name. Globally unique, 3 to 24 characters: lowercase letters and digits only."

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.name))
    error_message = "name must be 3 to 24 characters: lowercase letters and digits only."
  }
}

variable "extra_tags" {
  type        = map(string)
  default     = {}
  description = "Additional tags. Platform tags (Environment, BusinessUnit, CostCenter) cannot be overridden."
}
