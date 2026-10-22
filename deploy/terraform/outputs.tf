# Values the Kubernetes manifests and Substrate's install need.
output "cluster" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.main.name} --zone ${var.zone} --project ${var.project_id}"
}
