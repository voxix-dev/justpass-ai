variable "project_id" {
  type        = string
  description = "GCP project that holds the cluster, the buckets and the secrets."
}

variable "region" {
  type        = string
  description = "Region of the subnets, the buckets, the addresses and the secrets."
  default     = "europe-west1"
}

variable "zone" {
  type        = string
  description = "Zone of the cluster. Defaults to zone b of the region."
  default     = null
}

variable "name" {
  type        = string
  description = "Name of the GKE cluster."
  default     = "substrate"
}

variable "cloudsql_instance" {
  type        = string
  description = "Name of the Cloud SQL instance (modules/api) that gets Substrate's database."
}

variable "cloudsql_connection_name" {
  type        = string
  description = "Connection name (project:region:instance) of that instance."
}

variable "emulator" {
  type        = bool
  description = <<-E
    Run against floci-gcp, which has no Cloud DNS and does not apply project-level IAM or Cloud SQL IAM users.
    Those resources are skipped; the rest is created as in production.
  E
  default     = false
}

variable "deletion_protection" {
  type        = bool
  description = "Refuse to delete the cluster, and delete the buckets only when empty."
  default     = true
}
