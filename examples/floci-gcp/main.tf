# Test-only root module: explicit emulator token prevents use of ambient Google credentials.
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "7.36.0"
    }
  }
}

provider "google" {
  project                 = "forgeapi-emulator"
  region                  = "us-central1"
  access_token            = "floci-emulator-only"
  user_project_override   = false
  storage_custom_endpoint = "http://localhost:4588/storage/v1/"
}

variable "name" {
  type        = string
  description = "Unique test bucket name"
}

resource "google_storage_bucket" "test" {
  name                        = var.name
  location                    = "US"
  uniform_bucket_level_access = true
}

output "name" {
  value = google_storage_bucket.test.name
}
