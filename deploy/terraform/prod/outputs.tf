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
