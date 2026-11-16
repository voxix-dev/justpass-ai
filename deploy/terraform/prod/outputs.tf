# Repository variables for the deploy workflow come from bootstrap; the workflow reads the deployer itself.

output "GCP_DEPLOYER_SERVICE_ACCOUNT" {
  value = google_service_account.deployer.email
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

# The web app's Firebase config, which the deploy workflow gives the web build (VITE_FIREBASE_*). None of it is secret.
output "firebase_web_api_key" {
  value = data.google_firebase_web_app_config.web.api_key
}

output "firebase_web_app_id" {
  value = google_firebase_web_app.web.app_id
}

# The Hosting domain, used as the web app's Firebase auth domain.
output "firebase_auth_domain" {
  value = local.hosting_domains[0]
}
