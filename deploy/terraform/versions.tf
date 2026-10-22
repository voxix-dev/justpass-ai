terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.8"
    }
  }

  # The state bucket is created once by hand (see README), then:
  # terraform init -backend-config="bucket=<project-id>-tfstate"
  backend "gcs" {
    prefix = "demo"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  # The Billing Budgets API needs a quota project when run with user credentials.
  user_project_override = true
  billing_project       = var.project_id
}

data "google_project" "this" {}
