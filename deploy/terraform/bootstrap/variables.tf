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
