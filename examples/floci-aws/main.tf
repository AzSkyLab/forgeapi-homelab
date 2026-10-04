# Test-only root module: every service endpoint is the local Floci emulator.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.14.1"
    }
  }
}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true
  endpoints {
    s3 = "http://localhost:4566"
  }
}

variable "name" {
  type        = string
  description = "Unique test bucket name"
}

resource "aws_s3_bucket" "test" {
  bucket = var.name
}

output "name" {
  value = aws_s3_bucket.test.id
}
