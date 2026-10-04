# The account and region come from the platform (see variables.tf). Credentials come from the
# worker's ambient identity (role or workload identity), never from this repo.
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = local.tags
  }
}
