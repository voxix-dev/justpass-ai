variable "project_id" {
  description = "GCP project id. Set it in terraform.tfvars (git-ignored)."
  type        = string
}

variable "billing_account_id" {
  description = "Billing account the budget alert is attached to (XXXXXX-XXXXXX-XXXXXX)."
  type        = string
}

variable "budget_amount" {
  description = "Monthly budget, in the billing account's currency. Alerts at 50%, 90% and 100%."
  type        = number
  default     = 100
}

variable "region" {
  description = "Region for every regional resource. EU only (minors' data)."
  type        = string
  default     = "europe-west1"
}

variable "zone" {
  description = "Zone of the GKE cluster. A zonal cluster's management fee is covered by the GKE free tier."
  type        = string
  default     = "europe-west1-b"

  validation {
    condition     = startswith(var.zone, "${var.region}-")
    error_message = "zone must be in region."
  }
}

variable "gke_version" {
  description = "Minimum GKE version. Substrate needs 1.36+ (1.36 only with the beta APIs set in gke.tf)."
  type        = string
  default     = "1.36"
}

variable "machine_type" {
  description = "Node machine type. T2D runs on a single CPU platform, so gVisor snapshots restore on any node."
  type        = string
  default     = "t2d-standard-4"
}

variable "node_count" {
  description = "Nodes in the Substrate pool. Set to 0 to pause the cluster when nobody is using it."
  type        = number
  default     = 1
}

variable "spot_nodes" {
  description = "Use Spot VMs (about 70% cheaper). On reclaim, running actors get ~15 s to suspend."
  type        = bool
  default     = true
}

variable "deletion_protection" {
  description = "Protects the cluster (and, later, the database and buckets). Set to false only to tear down."
  type        = bool
  default     = true
}
