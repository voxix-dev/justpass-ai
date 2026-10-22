locals {
  # Reading it from the cluster makes every binding below wait until the pool exists.
  wi_pool = google_container_cluster.main.workload_identity_config[0].workload_pool

  # Substrate's components are granted access as their Kubernetes service accounts directly.
  wi_principal = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${local.wi_pool}/subject/ns/ate-system/sa"
  atelet       = "${local.wi_principal}/atelet"
  ate_api      = "${local.wi_principal}/ate-api-server"

  nodes_sa   = "serviceAccount:${google_service_account.nodes.email}"
  api_sa     = "serviceAccount:${google_service_account.api.email}"
  ate_api_sa = "serviceAccount:${google_service_account.ate_api.email}"

  app_data  = google_storage_bucket.app_data.name
  snapshots = google_storage_bucket.snapshots.name
  assets    = google_storage_bucket.snapshot_assets.name
}

resource "google_service_account" "nodes" {
  account_id = "justpass-gke-nodes"
  depends_on = [google_project_service.apis]
}

# The api's Kubernetes service account is justpass/api.
resource "google_service_account" "api" {
  account_id = "justpass-api"
  depends_on = [google_project_service.apis]
}

# Cloud SQL IAM users must be Google service accounts, so Substrate's ate-api-server
# gets one for the database, under the name Substrate's tooling uses.
resource "google_service_account" "ate_api" {
  account_id = "ate-api-server"
  depends_on = [google_project_service.apis]
}

# The agent gets no Google identity at all: it reaches data only through the Context API.

resource "google_service_account_iam_member" "workload_identity" {
  for_each = {
    "justpass/api"              = google_service_account.api.name
    "ate-system/ate-api-server" = google_service_account.ate_api.name
  }
  service_account_id = each.value
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.wi_pool}[${each.key}]"
}

resource "google_project_iam_member" "roles" {
  for_each = {
    nodes_default   = { member = local.nodes_sa, role = "roles/container.defaultNodeServiceAccount" }
    nodes_images    = { member = local.nodes_sa, role = "roles/artifactregistry.reader" }
    api_sql_client  = { member = local.api_sa, role = "roles/cloudsql.client" }
    api_sql_user    = { member = local.api_sa, role = "roles/cloudsql.instanceUser" }
    ate_sql_client  = { member = local.ate_api_sa, role = "roles/cloudsql.client" }
    ate_sql_user    = { member = local.ate_api_sa, role = "roles/cloudsql.instanceUser" }
    atelet_registry = { member = local.atelet, role = "roles/artifactregistry.reader" }
  }
  project = var.project_id
  member  = each.value.member
  role    = each.value.role
}

resource "google_storage_bucket_iam_member" "grants" {
  for_each = {
    api_app_data    = { bucket = local.app_data, member = local.api_sa, role = "roles/storage.objectUser" }
    ate_api_objects = { bucket = local.snapshots, member = local.ate_api, role = "roles/storage.objectAdmin" }
    ate_api_bucket  = { bucket = local.snapshots, member = local.ate_api, role = "roles/storage.bucketViewer" }
    atelet_assets   = { bucket = local.assets, member = local.atelet, role = "roles/storage.objectViewer" }
    # ate-api-server's Kubernetes SA is annotated with its Google SA (for Cloud SQL), so the
    # pod reaches GCS as the Google SA, not as the principal above. Grant both.
    ate_gsa_objects = { bucket = local.snapshots, member = local.ate_api_sa, role = "roles/storage.objectAdmin" }
    ate_gsa_bucket  = { bucket = local.snapshots, member = local.ate_api_sa, role = "roles/storage.bucketViewer" }
  }
  bucket = each.value.bucket
  member = each.value.member
  role   = each.value.role
}
