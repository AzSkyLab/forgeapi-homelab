terraform {
  required_version = ">= 1.9, < 2.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # Remote state. The platform only configures the azurerm backend; for any other backend the
  # pattern repo must be self-sufficient. Without remote state, version upgrades of a
  # deployment are refused. Uncomment after the platform team creates the bucket and grants
  # the worker identity access. A state prefix shared by two deployments would make them
  # overwrite each other: settle the per-deployment prefix with the platform team first.
  #
  # backend "gcs" {
  #   bucket = "CHANGE-ME-tfstate"
  #   prefix = "patterns/pattern-template-gcp"
  # }
}
