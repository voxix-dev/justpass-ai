# Run with `terraform test` in this directory. No credentials: the providers are mocked.

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789"
    }
  }
}

mock_provider "random" {}
mock_provider "tls" {}

variables {
  project_id               = "demo"
  cloudsql_instance        = "api-ab12"
  cloudsql_connection_name = "demo:europe-west1:api-ab12"
}

run "the_cluster_meets_substrates_requirements" {
  command = plan

  assert {
    condition = (
      google_container_cluster.substrate.location == "europe-west1-b"
      && google_container_cluster.substrate.min_master_version == "1.37"
      && google_container_cluster.substrate.datapath_provider == "ADVANCED_DATAPATH"
      && google_container_cluster.substrate.workload_identity_config[0].workload_pool == "demo.svc.id.goog"
      && google_container_cluster.substrate.remove_default_node_pool
    )
    error_message = "A zonal 1.37 cluster with Dataplane V2 and Workload Identity is required."
  }

  assert {
    condition = (
      toset(google_container_cluster.substrate.enable_k8s_beta_apis[0].enabled_apis)
      == toset(["certificates.k8s.io/v1beta1/podcertificaterequests", "certificates.k8s.io/v1beta1/clustertrustbundles"])
    )
    error_message = "Substrate needs the PodCertificateRequest and ClusterTrustBundle APIs."
  }

  # GKE keeps node auto-upgrade on in every release channel.
  assert {
    condition = (
      google_container_cluster.substrate.release_channel[0].channel == "UNSPECIFIED"
      && !google_container_node_pool.workers.management[0].auto_upgrade
      && google_container_node_pool.workers.management[0].auto_repair
    )
    error_message = "The cluster must be on no release channel, and its pool must not auto-upgrade."
  }

  assert {
    condition = (
      google_container_node_pool.workers.node_config[0].machine_type == "c3-standard-4"
      && google_container_node_pool.workers.node_config[0].spot != true
      && google_container_node_pool.workers.node_config[0].preemptible != true
      && google_container_node_pool.workers.node_config[0].workload_metadata_config[0].mode == "GKE_METADATA"
      && google_container_node_pool.workers.autoscaling[0].min_node_count == 1
      && google_container_node_pool.workers.autoscaling[0].max_node_count == 3
    )
    error_message = "The pool is one to three on-demand c3-standard-4 nodes with the GKE metadata server."
  }

  assert {
    condition     = google_container_cluster.substrate.deletion_protection && !google_container_node_pool.workers.ignore_node_count_changes
    error_message = "Deletion protection must default to on."
  }
}

run "the_network_has_a_subnet_per_job" {
  command = plan

  assert {
    condition = (
      !google_compute_network.substrate.auto_create_subnetworks
      && toset([for r in google_compute_subnetwork.gke.secondary_ip_range : r.range_name]) == toset(["pods", "services"])
      && google_compute_subnetwork.run.ip_cidr_range == "10.40.0.0/24"
    )
    error_message = "A custom VPC needs the GKE subnet with pod and service ranges, and the Cloud Run subnet."
  }

  assert {
    condition     = alltrue([for a in google_compute_address.internal : a.address_type == "INTERNAL"]) && length(google_compute_address.internal) == 2
    error_message = "ate-api and the router need an internal address each."
  }

  assert {
    condition     = google_dns_managed_zone.ate_system[0].dns_name == "ate-system.svc." && length(google_dns_record_set.ate_system) == 2
    error_message = "The ate-system.svc names of ate-api and the router must resolve inside the VPC."
  }
}

run "snapshots_and_the_database_are_granted_to_the_right_accounts" {
  command = plan

  assert {
    condition = (
      alltrue([for b in google_storage_bucket.snapshots : b.uniform_bucket_level_access && b.public_access_prevention == "enforced"])
      && google_storage_bucket.snapshots["demo-ate-snapshots-assets"].name == "demo-ate-snapshots-assets"
    )
    error_message = "Both buckets must be private with uniform access, the assets one named after the snapshot bucket."
  }

  assert {
    condition     = length(google_storage_bucket_iam_member.snapshots) == 6 && google_storage_bucket_iam_member.atelet_assets.role == "roles/storage.objectViewer"
    error_message = "ate-api (two identities) and the transfer account need object admin and bucket viewer; atelet reads the assets."
  }

  assert {
    condition     = length(google_service_account_iam_member.snapshot_transfer_token_creator) == 2
    error_message = "Both ate-api identities must create tokens for the transfer account."
  }

  assert {
    condition = (
      google_service_account_iam_member.cloudsql_workload_identity.member == "serviceAccount:demo.svc.id.goog[ate-system/ate-api-server]"
      && length(google_project_iam_member.cloudsql) == 2
      && google_sql_user.ate_api[0].type == "CLOUD_IAM_SERVICE_ACCOUNT"
      && google_sql_database.substrate.instance == "api-ab12"
    )
    error_message = "ate-api's Kubernetes account must act as the Cloud SQL account, an IAM database user on the shared instance."
  }

  assert {
    condition = (
      google_sql_user.substrate_admin.name == "substrate_admin"
      && google_sql_user.substrate_admin.instance == "api-ab12"
    )
    error_message = "Substrate's database needs a built-in admin user on the shared instance."
  }

  assert {
    condition     = length(google_project_iam_member.node) == 1
    error_message = "The node account needs its project role."
  }
}

run "the_router_client_is_a_spiffe_identity" {
  command = plan

  assert {
    condition = (
      tls_cert_request.router_client.uris == tolist(["spiffe://cluster.local/ns/homework/sa/api"])
      && tls_locally_signed_cert.router_client.validity_period_hours == 8760
      && tls_locally_signed_cert.router_client.early_renewal_hours > 0
      && contains(tls_locally_signed_cert.router_client.allowed_uses, "client_auth")
    )
    error_message = "The client certificate must carry the API's SPIFFE ID, last a year and renew early."
  }

  assert {
    condition     = toset(keys(google_secret_manager_secret.api)) == toset(["ATE_TOKEN", "ATE_CA", "ATE_TEMPLATE", "ATE_CLIENT_CERT"])
    error_message = "The sync's three secrets and the client certificate need a secret each."
  }

  assert {
    condition     = length(google_secret_manager_secret_version.placeholder) == 3
    error_message = "The sync's secrets need a first version for the first revision to start."
  }
}

run "emulator_skips_what_floci_cannot_do" {
  command = plan

  variables {
    emulator            = true
    deletion_protection = false
  }

  assert {
    condition = (
      length(google_dns_managed_zone.ate_system) == 0
      && length(google_dns_record_set.ate_system) == 0
      && length(google_project_iam_member.node) == 0
      && length(google_project_iam_member.cloudsql) == 0
      && length(google_sql_user.ate_api) == 0
    )
    error_message = "The emulator has no Cloud DNS, project IAM or Cloud SQL IAM users."
  }

  assert {
    condition     = google_container_node_pool.workers.ignore_node_count_changes && !google_container_cluster.substrate.deletion_protection
    error_message = "The emulator's node count must not show as drift."
  }
}
