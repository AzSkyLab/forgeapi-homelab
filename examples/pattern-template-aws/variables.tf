# ---- Set by the platform: callers cannot set these --------------------------------------------

variable "aws_account_id" {
  type        = string
  description = "AWS account to deploy into. Set by the platform; callers cannot set it."
}

variable "region" {
  type        = string
  description = "AWS region. Set by the platform; callers cannot set it."
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

# ---- Set by the pattern's sizing (config.yaml): callers choose a size, not these values -------

variable "noncurrent_retention_days" {
  type        = number
  default     = 30
  description = "Days an overwritten or deleted object version is kept before it expires. Set by the chosen size."

  validation {
    condition     = var.noncurrent_retention_days >= 1 && var.noncurrent_retention_days <= 365
    error_message = "noncurrent_retention_days must be between 1 and 365."
  }
}

# ---- Caller inputs ---------------------------------------------------------------------------

variable "name" {
  type        = string
  description = "Bucket name. Globally unique, 3 to 63 characters: lowercase letters, digits and hyphens, starting and ending with a letter or digit."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$", var.name))
    error_message = "name must be 3 to 63 characters: lowercase letters, digits and hyphens, starting and ending with a letter or digit."
  }
}

variable "extra_tags" {
  type        = map(string)
  default     = {}
  description = "Additional tags for the bucket. Platform tags (Environment, BusinessUnit, CostCenter) cannot be overridden."
}
