# Production on GCP: the API on Cloud Run, Postgres on Cloud SQL, and what lets GitHub Actions deploy it
# without a key file. Substrate is the only thing that runs on Kubernetes, so none of it is here.
# Test the module locally first: deploy/gcp/local-e2e.sh.

locals {
  apis = [
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
    "sts.googleapis.com",
  ]
}

resource "google_project_service" "api" {
  for_each           = toset(local.apis)
  service            = each.key
  disable_on_destroy = false
}

module "api" {
  source = "../modules/api"

  project_id = var.project_id
  region     = var.region
  image      = var.bootstrap_image
  env        = merge({ AGENT_URL = var.agent_url }, var.env)

  depends_on = [google_project_service.api]
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "justpass"
  location      = var.region
  format        = "DOCKER"

  depends_on = [google_project_service.api]
}

# GitHub Actions deploys as this service account, with a short-lived token from its OIDC identity.
resource "google_service_account" "deployer" {
  account_id   = "github-deployer"
  display_name = "GitHub Actions deploys the API"

  depends_on = [google_project_service.api]
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"

  depends_on = [google_project_service.api]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  # Only workflows on main of this repository can get a token.
  attribute_condition = "assertion.repository == \"${var.github_repository}\" && assertion.ref == \"refs/heads/main\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_from_github" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_artifact_registry_repository_iam_member" "deployer_pushes" {
  repository = google_artifact_registry_repository.images.name
  location   = var.region
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_cloud_run_v2_service_iam_member" "deployer_deploys" {
  location = var.region
  name     = module.api.service_name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.deployer.email}"
}

# A new revision runs as the API's service account, which deploying it counts as acting as.
resource "google_service_account_iam_member" "deployer_acts_as_runtime" {
  service_account_id = module.api.runtime_service_account_name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}
