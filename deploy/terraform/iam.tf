locals {
  nodes_sa = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_service_account" "nodes" {
  account_id = "justpass-gke-nodes"
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "roles" {
  for_each = {
    nodes_default = { member = local.nodes_sa, role = "roles/container.defaultNodeServiceAccount" }
    nodes_images  = { member = local.nodes_sa, role = "roles/artifactregistry.reader" }
  }
  project = var.project_id
  member  = each.value.member
  role    = each.value.role
}
