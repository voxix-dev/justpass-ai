# Values the Kubernetes manifests and Substrate's install need.
output "cluster" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.main.name} --zone ${var.zone} --project ${var.project_id}"
}

output "cloudsql_instance" {
  description = "ATE_API_POSTGRES_CLOUDSQL_INSTANCE, and the api's Cloud SQL connector instance."
  value       = google_sql_database_instance.main.connection_name
}

output "service_accounts" {
  description = "Annotate each Kubernetes SA with iam.gke.io/gcp-service-account."
  value = {
    "justpass/api"              = google_service_account.api.email
    "ate-system/ate-api-server" = google_service_account.ate_api.email
  }
}

output "app_data_bucket" {
  value = google_storage_bucket.app_data.name
}

output "snapshot_bucket" {
  description = "BUCKET_NAME for Substrate's install."
  value       = google_storage_bucket.snapshots.name
}

output "snapshot_location" {
  description = "SNAPSHOT_LOCATION for Substrate's install."
  value       = "gs://${google_storage_bucket.snapshots.name}/"
}

output "image_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}
