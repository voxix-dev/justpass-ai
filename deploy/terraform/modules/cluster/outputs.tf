output "name" {
  value = google_container_cluster.substrate.name
}

output "location" {
  value = google_container_cluster.substrate.location
}

output "network" {
  value = google_compute_network.substrate.id
}

output "run_subnetwork" {
  description = "Subnet for Cloud Run's direct VPC egress."
  value       = google_compute_subnetwork.run.id
}

output "run_cidr" {
  value = google_compute_subnetwork.run.ip_cidr_range
}

output "ate_api_ip" {
  value = google_compute_address.internal["ate-api"].address
}

output "router_ip" {
  value = google_compute_address.internal["router"].address
}

output "snapshot_location" {
  value = "gs://${google_storage_bucket.snapshots[local.snapshot_bucket].name}/snapshots/"
}

output "snapshot_transfer_gsa" {
  description = "Name of the snapshot transfer account (SNAPSHOT_TRANSFER_GSA_NAME)."
  value       = google_service_account.snapshot_transfer.account_id
}

output "assets_bucket" {
  value = google_storage_bucket.snapshots[local.assets_bucket].name
}

output "cloudsql_database" {
  value = google_sql_database.substrate.name
}

output "cloudsql_gsa" {
  description = "Email of the account ate-api uses for Cloud SQL IAM authentication (ATE_API_POSTGRES_CLOUDSQL_GSA)."
  value       = google_service_account.cloudsql.email
}

output "substrate_admin_password" {
  description = "Password of the Cloud SQL user that runs deploy/gke/substrate-db.sql."
  value       = random_password.substrate_admin.result
  sensitive   = true
}

output "router_client_ca_pem" {
  description = "Certificate authority of the API's router client certificate, for the router's trusted client list."
  value       = tls_self_signed_cert.router_ca.cert_pem
}

output "secret_ids" {
  description = "Secret Manager ids by name: the three the cluster sync writes, and the router client certificate."
  value       = { for k, s in google_secret_manager_secret.api : k => s.secret_id }

  # Cloud Run does not start a revision that mounts a secret with no version.
  depends_on = [
    google_secret_manager_secret_version.client_cert,
    google_secret_manager_secret_version.placeholder,
  ]
}
