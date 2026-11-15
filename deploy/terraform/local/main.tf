# The API, its database and the GKE cluster in floci-gcp, the GCP emulator: deploy/gcp/local-e2e.sh.
# Same modules as production, with `emulator = true`. Local state, no credentials. The cluster is a k3s container.

terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.36"
    }
  }
}

variable "emulator_url" {
  type    = string
  default = "http://localhost:4588"
}

variable "container_url" {
  type        = string
  description = "floci-gcp serves GKE on its own host name."
  default     = "http://container.localhost:4588/"
}

variable "image" {
  type    = string
  default = "localhost/justpass-api:dev"
}

provider "google" {
  project      = "local-project"
  region       = "europe-west1"
  access_token = "floci" # floci-gcp ignores credentials

  user_project_override = false

  cloud_run_v2_custom_endpoint     = "${var.emulator_url}/v2/"
  compute_custom_endpoint          = "${var.emulator_url}/compute/v1/"
  container_custom_endpoint        = var.container_url
  storage_custom_endpoint          = "${var.emulator_url}/storage/v1/"
  sql_custom_endpoint              = "${var.emulator_url}/sql/v1beta4/"
  secret_manager_custom_endpoint   = "${var.emulator_url}/v1/"
  iam_custom_endpoint              = "${var.emulator_url}/"
  iam_beta_custom_endpoint         = "${var.emulator_url}/v1/"
  service_usage_custom_endpoint    = "${var.emulator_url}/v1/"
  resource_manager_custom_endpoint = "${var.emulator_url}/v1/"
}

module "api" {
  source = "../modules/api"

  project_id          = "local-project"
  image               = var.image
  emulator            = true
  deletion_protection = false
  env = {
    AGENT_URL = "http://agent.invalid" # the API needs one to start; no agent runs here
  }
}

module "cluster" {
  source = "../modules/cluster"

  project_id               = "local-project"
  cloudsql_instance        = module.api.cloud_sql_instance_name
  cloudsql_connection_name = module.api.cloud_sql_connection_name
  emulator                 = true
  deletion_protection      = false
}

output "project_id" {
  value = "local-project"
}

output "region" {
  value = "europe-west1"
}

output "service_uri" {
  value = module.api.service_uri
}

output "cloud_sql_instance_name" {
  value = module.api.cloud_sql_instance_name
}

output "cluster" {
  value = {
    project_id            = "local-project"
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
    secrets = {
      token    = module.cluster.secret_ids["ATE_TOKEN"]
      ca       = module.cluster.secret_ids["ATE_CA"]
      template = module.cluster.secret_ids["ATE_TEMPLATE"]
    }
  }
}
