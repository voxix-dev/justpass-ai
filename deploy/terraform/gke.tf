resource "google_container_cluster" "main" {
  name                = "justpass"
  location            = var.zone
  min_master_version  = var.gke_version
  deletion_protection = var.deletion_protection

  network           = google_compute_network.main.id
  subnetwork        = google_compute_subnetwork.gke.id
  datapath_provider = "ADVANCED_DATAPATH"

  # The node pool is managed below; GKE needs a default pool at creation, so it is removed.
  remove_default_node_pool = true
  initial_node_count       = 1
  node_config {
    service_account = google_service_account.nodes.email
  }

  release_channel {
    channel = "REGULAR"
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # Nodes are private. The control plane keeps a public endpoint (Google IAM still guards it)
  # so kubectl works from laptops without a VPN.
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # GKE honors these only at cluster creation. Without them Substrate's install
  # hangs at "Waiting for podcertificate ClusterTrustBundles to be ready".
  enable_k8s_beta_apis {
    enabled_apis = [
      "certificates.k8s.io/v1beta1/podcertificaterequests",
      "certificates.k8s.io/v1beta1/clustertrustbundles",
    ]
  }

  managed_opentelemetry_config {
    scope = "COLLECTION_AND_INSTRUMENTATION_COMPONENTS"
  }

  addons_config {
    gcp_filestore_csi_driver_config {
      enabled = false
    }
  }
}

resource "google_container_node_pool" "substrate" {
  name       = "substrate-node-pool"
  cluster    = google_container_cluster.main.id
  location   = var.zone
  node_count = var.node_count

  node_config {
    machine_type    = var.machine_type
    spot            = var.spot_nodes
    disk_type       = "pd-balanced"
    disk_size_gb    = 50
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    # Substrate's sysctls for many sandboxes per node.
    linux_node_config {
      sysctls = {
        "net.ipv4.neigh.default.gc_thresh1" = "4096"
        "net.ipv4.neigh.default.gc_thresh2" = "16384"
        "net.ipv4.neigh.default.gc_thresh3" = "32768"
        "fs.inotify.max_user_instances"     = "65536"
        "fs.inotify.max_user_watches"       = "1048576"
      }
    }
  }

  depends_on = [google_project_iam_member.roles]
}
