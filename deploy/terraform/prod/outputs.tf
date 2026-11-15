# The GitHub repository variables the deploy workflow reads (.github/workflows/deploy.yml).

output "GCP_PROJECT_ID" {
  value = var.project_id
}

output "GCP_REGION" {
  value = var.region
}

output "GCP_WORKLOAD_IDENTITY_PROVIDER" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "GCP_DEPLOYER_SERVICE_ACCOUNT" {
  value = google_service_account.deployer.email
}

output "GCP_IMAGE_REPOSITORY" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.ghcr.repository_id}/${var.github_repository}/api"
}

output "api_url" {
  value = module.api.service_uri
}

output "firebase_web_config" {
  value = {
    apiKey     = data.google_firebase_web_app_config.web.api_key
    authDomain = data.google_firebase_web_app_config.web.auth_domain
    projectId  = var.project_id
    appId      = google_firebase_web_app.web.app_id
  }
}
