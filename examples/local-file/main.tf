terraform {
  required_version = ">= 1.9"
  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

# No cloud and no credentials: proves the API -> Temporal -> Terraform pipeline end to end.
# Real patterns live in their own git repos; see patterns.yaml.
variable "filename" {
  description = "File name to write inside the deployment workspace"
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", var.filename))
    error_message = "filename must be a plain file name (letters, digits, dot, dash, underscore)."
  }
}

variable "content" {
  description = "File content"
  type        = string
}

resource "local_file" "this" {
  filename = "${path.module}/out/${var.filename}"
  content  = var.content
}

output "path" { value = abspath(local_file.this.filename) }
output "content_sha256" { value = local_file.this.content_sha256 }
