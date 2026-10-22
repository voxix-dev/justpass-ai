# Names only. Values are added by hand: gcloud secrets versions add <name> --data-file=-
resource "google_secret_manager_secret" "app" {
  for_each  = toset(["OPENROUTER_API_KEY", "SEARCH_API_KEY"])
  secret_id = each.value

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }

  depends_on = [google_project_service.apis]
}
