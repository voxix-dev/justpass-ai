# Firebase Auth for the web app: anonymous sign-in and email link. The API verifies the ID tokens itself
# (FIREBASE_PROJECT_ID). The Firebase project and the default Hosting site (site id = project id) come from bootstrap.

locals {
  hosting_domains = ["${var.project_id}.web.app", "${var.project_id}.firebaseapp.com"]
}

resource "google_firebase_web_app" "web" {
  provider = google-beta

  project      = var.project_id
  display_name = "web"

  # Deleting it would invalidate the app id in the deployed web build.
  deletion_policy = "ABANDON"

  depends_on = [google_project_service.api]
}

data "google_firebase_web_app_config" "web" {
  provider = google-beta

  project    = var.project_id
  web_app_id = google_firebase_web_app.web.app_id
}

# Identity Platform is already on in the project, so Terraform takes over its config. The import fails on a
# fresh project: first open the Firebase console, Authentication, Get started (once, by hand).
import {
  provider = google-beta
  to       = google_identity_platform_config.this
  id       = "projects/${var.project_id}/config"
}

resource "google_identity_platform_config" "this" {
  # The Identity Toolkit API needs a quota project, which only the google-beta provider sets.
  provider = google-beta

  project = var.project_id

  authorized_domains = local.hosting_domains

  sign_in {
    allow_duplicate_emails = false

    anonymous {
      enabled = true
    }

    # Email provider with email link. The app offers only the link, but email/password sign-up
    # stays possible through the REST API.
    email {
      enabled           = true
      password_required = false
    }
  }

  depends_on = [google_project_service.api]
}
