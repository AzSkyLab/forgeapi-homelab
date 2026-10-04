terraform {
  required_version = ">= 1.9, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Remote state. The platform only configures the azurerm backend; for any other backend the
  # pattern repo must be self-sufficient. Without remote state, version upgrades of a
  # deployment are refused (local state cannot be carried between runs). Uncomment, and have the
  # platform team create the bucket and lock table/lockfile access for the worker identity.
  #
  # backend "s3" {
  #   bucket       = "CHANGE-ME-tfstate"          # created and owned by the platform team
  #   key          = "patterns/pattern-template-aws/terraform.tfstate"
  #   region       = "us-east-1"
  #   encrypt      = true
  #   use_lockfile = true                         # S3-native locking (Terraform >= 1.10)
  # }
  #
  # A backend block cannot reference variables, and a state key shared by two deployments would
  # make them overwrite each other: derive the key per deployment with
  # `terraform init -backend-config="key=..."` in whatever runs init, or give each deployment its
  # own workspace. Settle this with the platform team before enabling.
}
