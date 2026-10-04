# The project and region come from the platform (see variables.tf). Credentials come from the
# worker's ambient identity (workload identity), never from this repo.
provider "google" {
  project = var.project_id
  region  = var.region
}
