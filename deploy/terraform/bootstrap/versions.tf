terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.36"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.36"
    }
  }

  # Bucket and prefix come from `terraform init -backend-config=...`; the bucket is made by hand once.
  backend "gcs" {}
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Firebase resources are beta. Quota is charged to the project itself, which needs serviceusage enabled.
provider "google-beta" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
}
