# Repository variables for the deploy workflow come from bootstrap; the workflow reads these two itself.

output "GCP_DEPLOYER_SERVICE_ACCOUNT" {
  value = google_service_account.deployer.email
}

output "GCP_IMAGE_REPOSITORY" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.ghcr.repository_id}/${var.github_repository}/api"
}

output "api_url" {
  value = module.api.service_uri
}

# What deploy/gke and its workflow need to install Substrate on the cluster and keep the API's secrets current.
output "cluster" {
  value = {
    project_id            = var.project_id
    name                  = module.cluster.name
    location              = module.cluster.location
    ate_api_ip            = module.cluster.ate_api_ip
    router_ip             = module.cluster.router_ip
    run_cidr              = module.cluster.run_cidr
    snapshot_location     = module.cluster.snapshot_location
    snapshot_transfer_gsa = module.cluster.snapshot_transfer_gsa
    assets_bucket         = module.cluster.assets_bucket
    cloudsql_instance     = module.api.cloud_sql_connection_name
    cloudsql_database     = module.cluster.cloudsql_database
    cloudsql_gsa          = module.cluster.cloudsql_gsa
    router_client_ca_pem  = module.cluster.router_client_ca_pem
    api_google_subject    = module.api.runtime_service_account_unique_id
    secrets = {
      ca       = module.cluster.secret_ids["ATE_CA"]
      template = module.cluster.secret_ids["ATE_TEMPLATE"]
    }
  }
}

# Kept out of `cluster`, which the workflow passes between jobs: the cluster job reads it from the state to run
# deploy/gke/substrate-db.sql.
output "substrate_admin_password" {
  value     = module.cluster.substrate_admin_password
  sensitive = true
}
