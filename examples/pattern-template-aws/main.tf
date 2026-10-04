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

resource "aws_s3_bucket" "this" {
  bucket = var.name

  # A deployment must never silently lose data: destroying a non-empty bucket fails.
  force_destroy = false
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    bucket_key_enabled = true

    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = var.noncurrent_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Lifecycle noncurrent rules need versioning to exist first.
  depends_on = [aws_s3_bucket_versioning.this]
}
