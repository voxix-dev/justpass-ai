# Applied by hand once, by someone who owns the project:
#   terraform init -backend-config=bucket=<state bucket> -backend-config=prefix=bootstrap
#   terraform apply -var project_id=<project>
# On a fresh project, also open the Firebase console, Authentication, Get started, once: prod/auth.tf imports the
# Identity Platform config, which exists only after that.
# It holds what the deploy workflow needs before it can run Terraform, and what Google cannot delete again
# (the Firebase project and its default Hosting site). Everything else is deploy/terraform/prod,
# which the deploy workflow applies.

locals {
  apis = [
    "cloudresourcemanager.googleapis.com",
    "firebase.googleapis.com",
    "firebasehosting.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "serviceusage.googleapis.com",
    "sts.googleapis.com",
  ]
}

resource "google_project_service" "api" {
  for_each           = toset(local.apis)
  service            = each.key
  disable_on_destroy = false
}

resource "google_firebase_project" "main" {
  provider = google-beta

  depends_on = [google_project_service.api]
}

resource "google_firebase_hosting_site" "default" {
  provider = google-beta

  site_id = var.project_id

  depends_on = [google_firebase_project.main]
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
    # Only jobs that run in a GitHub environment have the claim.
    "attribute.environment" = "has(assertion.environment) ? assertion.environment : 'none'"
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

# The deploy workflow applies deploy/terraform/prod as this account. Terraform manages the project's IAM, so it is an
# owner anyway.
resource "google_service_account" "terraform" {
  account_id   = "github-terraform"
  display_name = "GitHub Actions applies Terraform"

  depends_on = [google_project_service.api]
}

resource "google_project_iam_member" "terraform_owner" {
  project = var.project_id
  role    = "roles/owner"
  member  = "serviceAccount:${google_service_account.terraform.email}"
}

# Only the infra job's `production` environment gets the owner account; the other jobs (which run npm installs) never do.
resource "google_service_account_iam_member" "terraform_from_github" {
  service_account_id = google_service_account.terraform.name
  role               = "roles/iam.workloadIdentityUser"
  # By attribute, not by subject: the subject's format is a repository setting (this one uses immutable subjects).
  member = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.environment/production"
}
