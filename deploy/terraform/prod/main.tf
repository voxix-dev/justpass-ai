# Production on GCP: the API on Cloud Run (pulling the image CI pushes to GHCR through an Artifact Registry
# remote repository), Postgres on Cloud SQL, Firebase (Auth and Hosting) in the same project, and what lets
# GitHub Actions deploy the API and the web app without a key file. Substrate is the only thing that runs on
# Kubernetes, so none of it is here.
# Test the module locally first: deploy/gcp/local-e2e.sh.

locals {
  apis = [
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "firebase.googleapis.com",
    "firebasehosting.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "identitytoolkit.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
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
  env        = merge({ AGENT_URL = var.agent_url, FIREBASE_PROJECT_ID = var.project_id }, var.env)

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

resource "google_firebase_project" "main" {
  provider = google-beta

  depends_on = [google_project_service.api]
}

resource "google_identity_platform_config" "auth" {
  provider = google-beta

  authorized_domains = ["localhost", "${var.project_id}.firebaseapp.com", "${var.project_id}.web.app"]

  depends_on = [google_firebase_project.main]
}

resource "google_firebase_web_app" "web" {
  provider = google-beta

  display_name = "web"

  depends_on = [google_firebase_project.main]
}

data "google_firebase_web_app_config" "web" {
  provider = google-beta

  web_app_id = google_firebase_web_app.web.app_id
}

resource "google_firebase_hosting_site" "default" {
  provider = google-beta

  site_id = var.project_id

  depends_on = [google_firebase_project.main]
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
  # Only the deploy workflow of this repository, on main, can get a token. The numeric ids stay the same if
  # the repository or the organisation is renamed, and a different one that takes over the old name fails them.
  attribute_condition = join(" && ", [
    "assertion.repository == \"${var.github_repository}\"",
    "assertion.repository_id == \"${var.github_repository_id}\"",
    "assertion.repository_owner_id == \"${var.github_owner_id}\"",
    "assertion.ref == \"refs/heads/main\"",
    "assertion.job_workflow_ref == \"${var.github_repository}/.github/workflows/deploy.yml@refs/heads/main\"",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_from_github" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
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
    "roles/serviceusage.apiKeysViewer",
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
