# Outputs are for consumers of the deployment. Never output the project ID or anything that
# embeds it. A sensitive output is withheld by the platform and is never retrievable.

output "bucket_name" {
  description = "Name of the Cloud Storage bucket."
  value       = google_storage_bucket.this.name
}

output "bucket_url" {
  description = "gs:// URL of the bucket."
  value       = google_storage_bucket.this.url
}

# Secrets: never output a secret value. Put it in Secret Manager and output a REFERENCE
# (the secret's short name) that the consumer resolves with its own identity. A secret
# version costs a few cents a month, so this template documents it only. The full resource
# path embeds the project, so output the short name.
#
# resource "google_secret_manager_secret" "api_key" {
#   secret_id = "${var.name}-api-key"
#
#   replication {
#     auto {}
#   }
# }
#
# output "api_key_secret_name" {
#   description = "Secret Manager secret name. A reference, not the value."
#   value       = google_secret_manager_secret.api_key.secret_id
# }
