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
  description = "Refuse to destroy the database, the Cloud Run service and a non-empty uploads bucket."
  default     = true
}

variable "api_image_digest" {
  type        = string
  description = "Digest (sha256:...) of the API image in GHCR that the deploy workflow resolves for the commit."

  validation {
    condition     = can(regex("^sha256:[0-9a-f]{64}$", var.api_image_digest))
    error_message = "Pass the image digest as sha256:<64 hex digits>."
  }
}

variable "env" {
  type        = map(string)
  description = "More environment for the API. GCS_BUCKET is always the uploads bucket."
  default     = {}
}

variable "openrouter_api_key" {
  type        = string
  description = "Key of the model provider (OPENROUTER_API_KEY). The deploy workflow passes it as TF_VAR_openrouter_api_key."
  default     = ""
  sensitive   = true
}
