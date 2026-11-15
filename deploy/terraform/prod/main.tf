# Production on GCP, applied by the deploy workflow on every merge: the API on Cloud Run (pulling the image CI pushes
# to GHCR through an Artifact Registry remote repository), Postgres on Cloud SQL, and the identity GitHub Actions
# deploys the API and the web app with. What has to exist before the workflow can run (Firebase project and Hosting
# site, Workload Identity pool, the Terraform service account) is deploy/terraform/bootstrap. Substrate is the only
# thing that runs on Kubernetes, so none of it is here.
# Test the module locally first: deploy/gcp/local-e2e.sh.

locals {
  apis = [
    "artifactregistry.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sqladmin.googleapis.com",
  ]
}

resource "google_project_service" "api" {
  for_each           = toset(local.apis)
  service            = each.key
  disable_on_destroy = false
}

data "google_project" "this" {}

module "api" {
  source = "../modules/api"

  project_id = var.project_id
  region     = var.region
  image      = var.bootstrap_image
  env        = merge({ AGENT_URL = var.agent_url, FIREBASE_PROJECT_ID = var.project_id }, var.env)

  deletion_protection = var.deletion_protection

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

# GitHub Actions deploys as this service account, with a short-lived token from its OIDC identity. The Workload
# Identity pool is in bootstrap.
resource "google_service_account" "deployer" {
  account_id   = "github-deployer"
  display_name = "GitHub Actions deploys the API"

  depends_on = [google_project_service.api]
}

resource "google_service_account_iam_member" "deployer_from_github" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.workload_identity_pool_id}/attribute.repository/${var.github_repository}"
}

# Cloud Run checks that whoever deploys an image can read it.
resource "google_artifact_registry_repository_iam_member" "deployer_reads" {
  repository = google_artifact_registry_repository.ghcr.name
  location   = var.region
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.deployer.email}"
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
