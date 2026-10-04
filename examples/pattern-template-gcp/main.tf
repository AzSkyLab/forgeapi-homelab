locals {
  # GCP label values allow only lowercase letters, digits, hyphens and underscores.
  # Platform labels are applied last so a caller cannot override them. Unset values are dropped.
  labels = merge(
    var.extra_labels,
    { for k, v in {
      environment   = var.environment
      business_unit = var.business_unit
      cost_center   = var.cost_center
      managed_by    = "forgeapi"
    } : k => lower(replace(v, " ", "_")) if v != null }
  )
}

resource "google_storage_bucket" "this" {
  name     = var.name
  location = var.region

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false # a deployment must never silently lose data
  labels                      = local.labels

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      days_since_noncurrent_time = var.noncurrent_retention_days
      with_state                 = "ARCHIVED"
    }

    action {
      type = "Delete"
    }
  }
}
