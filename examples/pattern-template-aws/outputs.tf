# Outputs are for consumers of the deployment. Never output the account ID or anything that
# embeds it (an ARN contains the account ID, so this pattern outputs the bucket name instead).

output "bucket_name" {
  description = "Name of the S3 bucket."
  value       = aws_s3_bucket.this.id
}

output "bucket_endpoint" {
  description = "Regional endpoint host name of the bucket."
  value       = aws_s3_bucket.this.bucket_regional_domain_name
}

# Secrets: never output a secret value. A sensitive output is withheld by the platform
# (`withheld_outputs`) and is not retrievable. Put the secret in the pattern's own secret
# store and output a REFERENCE (a name or path) that the consumer resolves with its own identity.
# A Secrets Manager secret costs about 0.40 USD per month, so this template documents it only.
#
# resource "aws_secretsmanager_secret" "api_key" {
#   name                    = "${var.name}/api-key"
#   recovery_window_in_days = 7
# }
#
# output "api_key_secret_name" {
#   description = "Secrets Manager name of the API key. A reference, not the value."
#   value       = aws_secretsmanager_secret.api_key.name # the name, not the ARN (the ARN has the account ID)
# }
