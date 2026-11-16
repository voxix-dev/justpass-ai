output "service_uri" {
  value = google_cloud_run_v2_service.api.uri
}

output "runtime_service_account_unique_id" {
  description = "Numeric id of the service account the API runs as: the `sub` of the Google ID tokens it fetches."
  value       = google_service_account.runtime.unique_id
}

output "cloud_sql_instance_name" {
  value = google_sql_database_instance.db.name
}

output "cloud_sql_connection_name" {
  value = google_sql_database_instance.db.connection_name
}
