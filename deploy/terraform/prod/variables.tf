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
  description = "Image of the first revision. CI deploys the real one."
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "agent_url" {
  type        = string
  description = "Where the API reaches an agent (AGENT_URL). The API does not start without it or the ATE_* settings."
  default     = "http://agent.invalid" # no agent yet: the API serves, rounds fail until the cluster runs
}

variable "env" {
  type        = map(string)
  description = "More environment for the API: CONTEXT_API_URL, GCS_BUCKET, ..."
  default     = {}
}
