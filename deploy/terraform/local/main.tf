# The API and its database in floci-gcp, the GCP emulator: deploy/gcp/local-e2e.sh.
# Same module as production, with `emulator = true`. Local state, no credentials.

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
  min_instances       = 1
  env = {
    AGENT_URL = "http://agent.invalid" # the API needs one to start; no agent runs here
  }
}

output "project_id" {
  value = "local-project"
}

output "region" {
  value = "europe-west1"
}

output "service_name" {
  value = module.api.service_name
}

output "service_uri" {
  value = module.api.service_uri
}
