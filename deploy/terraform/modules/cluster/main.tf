# The GKE cluster Substrate runs on, with what it needs around it: its network, snapshot buckets, service accounts,
# a database on the API's Cloud SQL instance (and the account that prepares it), and the identity the API presents to
# Substrate's router. The API on Cloud Run reaches ate-api and the router through internal load balancers in this VPC
# (direct VPC egress).
#
# `var.emulator` skips only what floci-gcp cannot do: Cloud DNS, project-level IAM and the Cloud SQL IAM user.

locals {
  zone            = coalesce(var.zone, "${var.region}-b")
  snapshot_bucket = "${var.project_id}-ate-snapshots"
  assets_bucket   = "${local.snapshot_bucket}-assets"

  project_number = var.emulator ? "0" : data.google_project.this[0].number

  # Substrate's Kubernetes service accounts, as Workload Identity principals.
  wi_pool = "${var.project_id}.svc.id.goog"
  ksa = {
    ate_api = "principal://iam.googleapis.com/projects/${local.project_number}/locations/global/workloadIdentityPools/${local.wi_pool}/subject/ns/ate-system/sa/ate-api-server"
    atelet  = "principal://iam.googleapis.com/projects/${local.project_number}/locations/global/workloadIdentityPools/${local.wi_pool}/subject/ns/ate-system/sa/atelet"
  }

  # The router accepts the API by this SPIFFE ID (the sync passes it as an allowed ingress ID).
  router_client_spiffe_id = "spiffe://cluster.local/ns/homework/sa/api"

  # Secrets the API reads. The cluster sync adds their versions. The token is for clusters where the API has no Google
  # identity: production authenticates with Google ID tokens it fetches itself.
  sync_secrets = toset(concat(["ATE_CA", "ATE_TEMPLATE"], var.emulator ? ["ATE_TOKEN"] : []))
}

# floci-gcp has no Cloud Billing API to answer this read; the number only names Workload Identity principals, which the
# emulator does not evaluate.
data "google_project" "this" {
  count      = var.emulator ? 0 : 1
  project_id = var.project_id
}

# --- Network ---

resource "google_compute_network" "substrate" {
  name                    = "substrate"
  project                 = var.project_id
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "gke" {
  name                     = "gke"
  project                  = var.project_id
  region                   = var.region
  network                  = google_compute_network.substrate.id
  ip_cidr_range            = "10.10.0.0/20"
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }
}

# Cloud Run's direct VPC egress takes its instances' addresses from here.
resource "google_compute_subnetwork" "run" {
  name                     = "run"
  project                  = var.project_id
  region                   = var.region
  network                  = google_compute_network.substrate.id
  ip_cidr_range            = "10.40.0.0/24"
  private_ip_google_access = true
}

# The internal load balancer Services of ate-api and the router use these addresses.
resource "google_compute_address" "internal" {
  for_each     = toset(["ate-api", "router"])
  name         = each.key
  project      = var.project_id
  region       = var.region
  address_type = "INTERNAL"
  subnetwork   = google_compute_subnetwork.gke.id
}

# The servers' certificates carry only <service>.ate-system.svc names, so the API resolves those names to the load
# balancers.
resource "google_dns_managed_zone" "ate_system" {
  count      = var.emulator ? 0 : 1
  name       = "ate-system-svc"
  project    = var.project_id
  dns_name   = "ate-system.svc."
  visibility = "private"

  private_visibility_config {
    networks {
      network_url = google_compute_network.substrate.id
    }
  }
}

resource "google_dns_record_set" "ate_system" {
  for_each = var.emulator ? {} : {
    "api"           = google_compute_address.internal["ate-api"].address
    "atenet-router" = google_compute_address.internal["router"].address
  }
  name         = "${each.key}.ate-system.svc."
  project      = var.project_id
  managed_zone = google_dns_managed_zone.ate_system[0].name
  type         = "A"
  ttl          = 300
  rrdatas      = [each.value]
}

# --- Cluster ---

# GKE nodes run as this account instead of the Compute Engine default one, which usually holds Editor.
resource "google_service_account" "node" {
  project      = var.project_id
  account_id   = "ate-gke-node"
  display_name = "Substrate GKE nodes"
}

resource "google_project_iam_member" "node" {
  count   = var.emulator ? 0 : 1
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${google_service_account.node.email}"
}

resource "google_container_cluster" "substrate" {
  name                = var.name
  project             = var.project_id
  location            = local.zone
  network             = google_compute_network.substrate.id
  subnetwork          = google_compute_subnetwork.gke.id
  deletion_protection = var.deletion_protection

  # Substrate needs 1.37 on the control plane and every node pool.
  min_master_version = "1.37"

  # Upstream wants node upgrades off on worker pools, because an upgrade drains a worker and an actor still awake when
  # its grace period ends is lost. GKE no longer lets new clusters go without a release channel, and keeps node
  # auto-upgrade on in every channel. The exclusion below blocks minor and node upgrades until the version's end of
  # support, which is the documented replacement for no channel. Patches reach the control plane only.
  release_channel {
    channel = "REGULAR"
  }

  maintenance_policy {
    daily_maintenance_window {
      start_time = "03:00"
    }

    maintenance_exclusion {
      exclusion_name = "no-node-upgrades"
      start_time     = timeadd(plantimestamp(), "5m") # GKE refuses a start in the past

      exclusion_options {
        scope             = "NO_MINOR_OR_NODE_UPGRADES"
        end_time_behavior = "UNTIL_END_OF_SUPPORT"
      }
    }
  }

  datapath_provider = "ADVANCED_DATAPATH" # Dataplane V2

  # Served by 1.37 under v1 as well; this is what substrate-gke's setup-gcp requests.
  enable_k8s_beta_apis {
    enabled_apis = [
      "certificates.k8s.io/v1beta1/podcertificaterequests",
      "certificates.k8s.io/v1beta1/clustertrustbundles",
    ]
  }

  workload_identity_config {
    workload_pool = local.wi_pool
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # The node pool below replaces the default one.
  remove_default_node_pool = true
  initial_node_count       = 1

  # The exclusion's start is the first plan's time; later plans would move it, and GKE fills in its end. The
  # exclusions are a set, so only the whole block can be ignored; change an existing cluster's exclusion with gcloud.
  lifecycle {
    ignore_changes = [maintenance_policy[0].maintenance_exclusion]
  }

  depends_on = [google_compute_subnetwork.gke]
}

resource "google_container_node_pool" "workers" {
  name               = "workers"
  project            = var.project_id
  cluster            = google_container_cluster.substrate.name
  location           = local.zone
  initial_node_count = 1

  # floci-gcp does not emulate instance groups, so the node count would show as drift on every plan.
  ignore_node_count_changes = var.emulator

  autoscaling {
    min_node_count = 1
    max_node_count = 3
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = "c3-standard-4"
    service_account = google_service_account.node.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    # /dev/kvm for micro-VM actors; fixed at node creation.
    advanced_machine_features {
      threads_per_core             = 2
      enable_nested_virtualization = true
    }

    # What substrate-gke's setup-gcp sets for nodes that host many sandboxes.
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
}

# --- Snapshots ---

resource "google_storage_bucket" "snapshots" {
  for_each                    = toset([local.snapshot_bucket, local.assets_bucket])
  name                        = each.key
  project                     = var.project_id
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
}

# ate-api's credential exchange impersonates this account and downscopes its token per actor for each snapshot transfer.
resource "google_service_account" "snapshot_transfer" {
  project      = var.project_id
  account_id   = "ate-snapshot-transfer"
  display_name = "Substrate snapshot transfers"
}

# ate-api runs as the Cloud SQL account below once its Kubernetes service account is annotated with it, and as its own
# Workload Identity principal before that: both need the snapshot grants. Grants to a Workload Identity principal wait
# for the cluster, which creates the pool.
locals {
  ate_api_members = {
    principal = local.ksa.ate_api
    gsa       = "serviceAccount:${google_service_account.cloudsql.email}"
  }
  snapshot_members = merge(local.ate_api_members, {
    transfer = "serviceAccount:${google_service_account.snapshot_transfer.email}"
  })
  snapshot_grants = {
    for p in setproduct(keys(local.snapshot_members), ["roles/storage.objectAdmin", "roles/storage.bucketViewer"]) :
    "${p[0]} ${p[1]}" => { member = local.snapshot_members[p[0]], role = p[1] }
  }
}

resource "google_storage_bucket_iam_member" "snapshots" {
  for_each = local.snapshot_grants
  bucket   = google_storage_bucket.snapshots[local.snapshot_bucket].name
  role     = each.value.role
  member   = each.value.member

  depends_on = [google_container_cluster.substrate]
}

# atelet reads the micro-VM sandbox assets with its own identity and gets nothing on the snapshots.
resource "google_storage_bucket_iam_member" "atelet_assets" {
  bucket = google_storage_bucket.snapshots[local.assets_bucket].name
  role   = "roles/storage.objectViewer"
  member = local.ksa.atelet

  depends_on = [google_container_cluster.substrate]
}

resource "google_service_account_iam_member" "snapshot_transfer_token_creator" {
  for_each           = local.ate_api_members
  service_account_id = google_service_account.snapshot_transfer.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = each.value

  depends_on = [google_container_cluster.substrate]
}

# --- Database ---

# ate-api reaches Substrate's database on the API's Cloud SQL instance through the Auth Proxy sidecar, logging in as
# this account with IAM database authentication: no password exists.
resource "google_service_account" "cloudsql" {
  project      = var.project_id
  account_id   = "ate-api-server"
  display_name = "Substrate ate-api on Cloud SQL"
}

resource "google_service_account_iam_member" "cloudsql_workload_identity" {
  service_account_id = google_service_account.cloudsql.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.wi_pool}[ate-system/ate-api-server]"

  depends_on = [google_container_cluster.substrate]
}

resource "google_project_iam_member" "cloudsql" {
  for_each = var.emulator ? toset([]) : toset(["roles/cloudsql.client", "roles/cloudsql.instanceUser"])
  project  = var.project_id
  role     = each.key
  member   = "serviceAccount:${google_service_account.cloudsql.email}"
}

resource "google_sql_database" "substrate" {
  name     = "substrate"
  project  = var.project_id
  instance = var.cloudsql_instance
}

# The database user is the account's email without ".gserviceaccount.com".
resource "google_sql_user" "ate_api" {
  count    = var.emulator ? 0 : 1
  name     = trimsuffix(google_service_account.cloudsql.email, ".gserviceaccount.com")
  project  = var.project_id
  instance = var.cloudsql_instance
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}

# deploy/gke/substrate-db.sql runs as this account before ate-api's first start. It creates Substrate's roles and
# schema, which the IAM user cannot; Cloud SQL makes every user it creates a member of cloudsqlsuperuser. Letters and
# digits only, so the password is safe in any connection string.
resource "random_password" "substrate_admin" {
  length  = 32
  special = false
}

resource "google_sql_user" "substrate_admin" {
  name     = "substrate_admin"
  project  = var.project_id
  instance = var.cloudsql_instance
  password = random_password.substrate_admin.result
}

# --- The API's identity towards Substrate ---

resource "tls_private_key" "router_ca" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_self_signed_cert" "router_ca" {
  private_key_pem       = tls_private_key.router_ca.private_key_pem
  is_ca_certificate     = true
  validity_period_hours = 24 * 365 * 10
  allowed_uses          = ["cert_signing", "crl_signing"]

  subject {
    common_name = "justpass router clients"
  }
}

resource "tls_private_key" "router_client" {
  algorithm   = "ECDSA"
  ecdsa_curve = "P256"
}

resource "tls_cert_request" "router_client" {
  private_key_pem = tls_private_key.router_client.private_key_pem
  uris            = [local.router_client_spiffe_id]
}

# A year long, replaced by the first apply in its last 30 days; the new secret version reaches the API through the
# volume mount ("latest") without a redeploy.
resource "tls_locally_signed_cert" "router_client" {
  cert_request_pem      = tls_cert_request.router_client.cert_request_pem
  ca_private_key_pem    = tls_private_key.router_ca.private_key_pem
  ca_cert_pem           = tls_self_signed_cert.router_ca.cert_pem
  validity_period_hours = 24 * 365
  early_renewal_hours   = 24 * 30
  allowed_uses          = ["digital_signature", "client_auth"]
}

# --- Secrets ---

resource "google_secret_manager_secret" "api" {
  for_each  = setunion(local.sync_secrets, ["ATE_CLIENT_CERT"])
  project   = var.project_id
  secret_id = "api-${each.key}"

  replication {
    user_managed {
      replicas {
        location = var.region
      }
    }
  }
}

# One file for the API: ssl load_cert_chain reads the certificate and the key from it.
resource "google_secret_manager_secret_version" "client_cert" {
  secret      = google_secret_manager_secret.api["ATE_CLIENT_CERT"].id
  secret_data = "${tls_locally_signed_cert.router_client.cert_pem}${tls_private_key.router_client.private_key_pem}"

  lifecycle {
    create_before_destroy = true
  }
}

# The first Cloud Run revision needs a version to start. The cluster sync adds newer ones, which the API reads as
# "latest", and Terraform leaves them alone.
resource "google_secret_manager_secret_version" "placeholder" {
  for_each    = local.sync_secrets
  secret      = google_secret_manager_secret.api[each.key].id
  secret_data = "set by the cluster sync"
}
