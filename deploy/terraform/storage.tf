# users/{id}/uploads and runs/{id}/artifacts. Only the api reads and writes it.
resource "google_storage_bucket" "app_data" {
  name                        = "${var.project_id}-app-data"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  depends_on                  = [google_project_service.apis]
}

# Substrate actor snapshots. The api deletes them after 48 h; the 3-day rule is the safety net.
# It covers only actor snapshots in the "justpass" atespace: tag (golden) snapshots must stay.
resource "google_storage_bucket" "snapshots" {
  name                        = "${var.project_id}-snapshots"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  depends_on                  = [google_project_service.apis]

  lifecycle_rule {
    condition {
      age            = 3
      matches_prefix = ["atespaces/justpass/actors/"]
    }
    action {
      type = "Delete"
    }
  }

  # Snapshots are large and disposable: don't pay to keep deleted ones for 7 days.
  soft_delete_policy {
    retention_duration_seconds = 0
  }
}

# Substrate requires "<snapshot bucket>-assets" next to the snapshot bucket (sandbox assets).
resource "google_storage_bucket" "snapshot_assets" {
  name                        = "${google_storage_bucket.snapshots.name}-assets"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
}

resource "google_artifact_registry_repository" "images" {
  repository_id = "justpass"
  location      = var.region
  format        = "DOCKER"

  # Stay inside the 0.5 GB free tier: keep the 10 newest images, delete the rest after a week.
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      older_than = "604800s"
    }
  }

  depends_on = [google_project_service.apis]
}
