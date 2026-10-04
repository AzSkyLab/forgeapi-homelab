# Test-only HA router: Route 53 failover CNAMEs with a health check per endpoint.
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
  endpoints {
    route53 = "http://localhost:4566"
    sts     = "http://localhost:4566"
  }
}

variable "zone_name" {
  type        = string
  description = "Hosted zone, e.g. app.example.com."
}

variable "primary_endpoint" {
  type = string
}

variable "secondary_endpoint" {
  type = string
}

# Each replica publishes its own health path; one shared path would mark the other cloud's
# replica unhealthy and failover would never happen.
variable "primary_health_path" {
  type = string
}

variable "secondary_health_path" {
  type = string
}

variable "aws_account_id" {
  type = string
}

variable "region" {
  type = string
}

resource "aws_route53_zone" "app" {
  name = var.zone_name
}

resource "aws_route53_health_check" "primary" {
  type              = "HTTP"
  fqdn              = var.primary_endpoint
  port              = 80
  resource_path     = var.primary_health_path
  failure_threshold = 3
  request_interval  = 30
}

resource "aws_route53_health_check" "secondary" {
  type              = "HTTP"
  fqdn              = var.secondary_endpoint
  port              = 80
  resource_path     = var.secondary_health_path
  failure_threshold = 3
  request_interval  = 30
}

resource "aws_route53_record" "primary" {
  zone_id         = aws_route53_zone.app.zone_id
  name            = "app.${var.zone_name}"
  type            = "CNAME"
  ttl             = 30
  records         = [var.primary_endpoint]
  set_identifier  = "primary"
  health_check_id = aws_route53_health_check.primary.id
  failover_routing_policy {
    type = "PRIMARY"
  }
}

resource "aws_route53_record" "secondary" {
  zone_id         = aws_route53_zone.app.zone_id
  name            = "app.${var.zone_name}"
  type            = "CNAME"
  ttl             = 30
  records         = [var.secondary_endpoint]
  set_identifier  = "secondary"
  health_check_id = aws_route53_health_check.secondary.id
  failover_routing_policy {
    type = "SECONDARY"
  }
}

output "app_fqdn" {
  value = aws_route53_record.primary.fqdn
}

output "zone_id" {
  value = aws_route53_zone.app.zone_id
}
