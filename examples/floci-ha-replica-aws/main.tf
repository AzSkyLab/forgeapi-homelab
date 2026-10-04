# Test-only HA replica (AWS side): a bucket hosting a tiny static app plus a health object.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.14.1"
    }
  }
}

provider "aws" {
  region                      = var.region
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = false
  skip_metadata_api_check     = true
  allowed_account_ids         = [var.aws_account_id]
  s3_use_path_style           = true
  endpoints {
    s3  = "http://localhost:4566"
    sts = "http://localhost:4566"
  }
}

variable "name" {
  type        = string
  description = "Unique replica bucket name"
}

variable "app_version" {
  type        = string
  description = "Version text served by the app"
  default     = "1"
}

variable "aws_account_id" {
  type = string
}

variable "region" {
  type = string
}

resource "aws_s3_bucket" "app" {
  bucket = var.name
}

resource "aws_s3_object" "index" {
  bucket       = aws_s3_bucket.app.id
  key          = "index.html"
  content      = "<h1>${var.name} aws v${var.app_version}</h1>"
  content_type = "text/html"
}

resource "aws_s3_object" "health" {
  bucket  = aws_s3_bucket.app.id
  key     = "health"
  content = "ok"
}

output "name" {
  value = aws_s3_bucket.app.id
}

output "endpoint" {
  value = aws_s3_bucket.app.bucket_regional_domain_name
}

output "health_path" {
  value = "/${aws_s3_object.health.key}"
}
