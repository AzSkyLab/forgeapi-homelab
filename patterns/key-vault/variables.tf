variable "subscription_id" { type = string }
variable "tenant_id" { type = string }
variable "executor_client_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", var.executor_client_id))
    error_message = "An explicit approved executor client ID is required; no human CLI fallback."
  }
}
variable "resource_group_id" { type = string }
variable "location" { type = string }
variable "name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,22}[a-z0-9]$", var.name)) && !strcontains(var.name, "--")
    error_message = "Use a 3–24 character lowercase Key Vault name without consecutive hyphens."
  }
}
