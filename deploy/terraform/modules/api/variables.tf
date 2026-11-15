variable "project_id" {
  type        = string
  description = "GCP project that holds the API and its database."
}

variable "region" {
  type        = string
  description = "Region of Cloud Run, Cloud SQL and the secrets."
  default     = "europe-west1"
}

variable "image" {
  type        = string
  description = "Container image of the first revision. CI deploys later images; Terraform ignores changes to it."
}

variable "env" {
  type        = map(string)
  description = "Plain environment variables for the API (AGENT_URL, CONTEXT_API_URL, FIREBASE_PROJECT_ID, ...)."
  default     = {}
}

variable "emulator" {
  type        = bool
  description = <<-E
    Run against floci-gcp, which emulates neither Cloud Run's Cloud SQL volume nor secret references.
    Then the API reaches Postgres over TCP and gets its secrets as plain environment values.
  E
  default     = false
}

variable "db_tier" {
  type        = string
  description = "Cloud SQL machine tier."
  default     = "db-custom-1-3840"
}

variable "deletion_protection" {
  type        = bool
  description = "Refuse to delete the database instance and the service."
  default     = true
}

variable "min_instances" {
  type        = number
  description = "Instances that stay up. The dispatcher sweeps in the background, so one must always be running."
  default     = 1
}

variable "max_instances" {
  type        = number
  description = "Upper bound on instances."
  default     = 3
}
