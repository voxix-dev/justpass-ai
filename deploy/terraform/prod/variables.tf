variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "europe-west1"
}

variable "github_repository" {
  type        = string
  description = "The repository whose main branch may deploy."
  default     = "voxix-dev/justpass-ai"
}

variable "workload_identity_pool_id" {
  type        = string
  description = "The Workload Identity pool that deploy/terraform/bootstrap made."
  default     = "github"
}

variable "deletion_protection" {
  type        = bool
  description = "Refuse to destroy the database and the Cloud Run service."
  default     = true
}

variable "bootstrap_image" {
  type        = string
  description = "Image used when api_image_digest is empty, e.g. in a plain local plan."
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "api_image_digest" {
  type        = string
  description = "Digest (sha256:...) of the API image in GHCR that the deploy workflow resolves for the commit."
  default     = ""
}

variable "env" {
  type        = map(string)
  description = "More environment for the API: GCS_BUCKET, ..."
  default     = {}
}

variable "openrouter_api_key" {
  type        = string
  description = "Key of the model provider (OPENROUTER_API_KEY). The deploy workflow passes it as TF_VAR_openrouter_api_key."
  default     = ""
  sensitive   = true
}
