terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
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
