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

variable "github_repository_id" {
  type    = string
  default = "1430335211" # gh api repos/voxix-dev/justpass-ai -q .id
}

variable "github_owner_id" {
  type    = string
  default = "203257071" # gh api repos/voxix-dev/justpass-ai -q .owner.id
}

variable "firebase_project_id" {
  type        = string
  description = "Firebase project whose ID tokens the API accepts (FIREBASE_PROJECT_ID)."
}

variable "bootstrap_image" {
  type        = string
  description = "Image of the first revision. CI deploys the real one."
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "agent_url" {
  type        = string
  description = "Where the API reaches an agent (AGENT_URL). The API does not start without it or the ATE_* settings."
}

variable "env" {
  type        = map(string)
  description = "More environment for the API: CONTEXT_API_URL, GCS_BUCKET, ..."
  default     = {}
}
