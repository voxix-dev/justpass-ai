# Production on GCP, applied by the deploy workflow on every merge: the API on Cloud Run (pulling the image CI pushes
# to GHCR through an Artifact Registry remote repository), Postgres on Cloud SQL, the identity GitHub Actions deploys
# the web app with, and the GKE cluster Substrate runs on (modules/cluster). What has to exist before the workflow can
# run (Firebase project and Hosting site, Workload Identity pool, the Terraform service account) is deploy/terraform/bootstrap. Substrate is the only
# thing that runs on Kubernetes, so the manifests are in deploy/gke, not here.
# Test the module locally first: deploy/gcp/local-e2e.sh.

locals {
  apis = [
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "dns.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
  ]

  # The image CI pushed to GHCR, pulled by digest through the Artifact Registry remote repository.
  api_image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.ghcr.repository_id}/${var.github_repository}/api@${var.api_image_digest}"
}

resource "google_project_service" "api" {
  for_each           = toset(local.apis)
  service            = each.key
  disable_on_destroy = false
}

data "google_project" "this" {}

# Each file gets its own directory on Cloud Run.
locals {
  ate_files = {
    ca          = "/secrets/ate-ca/ca.pem"
    client_cert = "/secrets/ate-client/client.pem"
    template    = "/secrets/ate-template/template"
  }
}

module "api" {
  source = "../modules/api"

  project_id         = var.project_id
  region             = var.region
  image              = local.api_image
  openrouter_api_key = var.openrouter_api_key
  env = merge({
    FIREBASE_PROJECT_ID = var.project_id

    # Substrate's ate-api and router, on internal load balancers in the cluster's VPC (names: modules/cluster).
    ATE_ROUTER_URL        = "https://atenet-router.ate-system.svc"
    ATE_API_TARGET        = "api.ate-system.svc:443"
    ATE_ATESPACE          = "justpass-agents"
    ATE_CA_FILE           = local.ate_files.ca
    ATE_ID_TOKEN_AUDIENCE = "api.ate-system.svc" # Google ID tokens from the metadata server; the sync trusts the account
    ATE_CLIENT_CERT_FILE  = local.ate_files.client_cert
    ATE_TEMPLATE_FILE     = local.ate_files.template

    # The agents call the API back at its own URL, which cannot be read from the service without a cycle.
    CONTEXT_API_URL = "https://api-${data.google_project.this.number}.${var.region}.run.app"
  }, var.env)

  vpc = {
    network    = module.cluster.network
    subnetwork = module.cluster.run_subnetwork
  }

  # The cluster sync writes these; the files are read at their latest version.
  secret_files = {
    ca          = { secret_id = module.cluster.secret_ids["ATE_CA"], path = local.ate_files.ca }
    client_cert = { secret_id = module.cluster.secret_ids["ATE_CLIENT_CERT"], path = local.ate_files.client_cert }
    template    = { secret_id = module.cluster.secret_ids["ATE_TEMPLATE"], path = local.ate_files.template }
  }

  deletion_protection = var.deletion_protection

  depends_on = [google_project_service.api]
}

module "cluster" {
  source = "../modules/cluster"

  project_id               = var.project_id
  region                   = var.region
  cloudsql_instance        = module.api.cloud_sql_instance_name
  cloudsql_connection_name = module.api.cloud_sql_connection_name
  deletion_protection      = var.deletion_protection

  depends_on = [google_project_service.api]
}

# Cloud Run cannot pull from ghcr.io, so it pulls through this repository, which fetches from the public GHCR package.
resource "google_artifact_registry_repository" "ghcr" {
  repository_id = "ghcr"
  location      = var.region
  format        = "DOCKER"
  mode          = "REMOTE_REPOSITORY"

  remote_repository_config {
    docker_repository {
      custom_repository {
        uri = "https://ghcr.io"
      }
    }
  }

  depends_on = [google_project_service.api]
}

# GitHub Actions deploys the web app as this service account, with a short-lived token from its OIDC identity. The
# Workload Identity pool is in bootstrap.
resource "google_service_account" "deployer" {
  account_id   = "github-deployer"
  display_name = "GitHub Actions deploys the web app"

  depends_on = [google_project_service.api]
}

resource "google_service_account_iam_member" "deployer_from_github" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.workload_identity_pool_id}/attribute.repository/${var.github_repository}"
}

# `firebase deploy --only hosting` as the deployer.
resource "google_project_iam_member" "deployer_firebase" {
  for_each = toset([
    "roles/firebasehosting.admin",
    "roles/serviceusage.serviceUsageConsumer",
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

moved {
  from = google_project_iam_member.deployer_reads_operations
  to   = google_project_iam_member.deployer_reads_run
}

# Read-only, for the Cloud Run service the /api/** rewrite names.
resource "google_project_iam_member" "deployer_reads_run" {
  project = var.project_id
  role    = "roles/run.viewer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}
