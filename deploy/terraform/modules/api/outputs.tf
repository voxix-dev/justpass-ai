output "service_name" {
  value = google_cloud_run_v2_service.api.name
}

output "service_uri" {
  value = google_cloud_run_v2_service.api.uri
}

output "runtime_service_account_name" {
  description = "Resource name of the service account the API runs as."
  value       = google_service_account.runtime.name
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.db.connection_name
}
