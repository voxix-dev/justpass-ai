# Run with `terraform test` in this directory. No credentials: the providers are mocked.

mock_provider "google" {
  mock_resource "google_sql_database_instance" {
    defaults = {
      connection_name = "demo:europe-west1:api"
      ip_address      = [{ ip_address = "10.0.0.2", type = "PRIMARY", time_to_retire = "" }]
    }
  }
}

mock_provider "random" {}

variables {
  project_id = "demo"
  image      = "example/api:1"
  env        = { AGENT_URL = "http://agent" }
}

run "production_uses_the_cloud_sql_volume_and_secret_references" {
  command = apply

  assert {
    condition     = length(google_cloud_run_v2_service.api.template[0].volumes) == 1
    error_message = "Production must mount the Cloud SQL volume."
  }

  assert {
    condition     = length(google_cloud_run_v2_service.api.template[0].containers[0].volume_mounts) == 1
    error_message = "Production must mount the Cloud SQL socket directory."
  }

  assert {
    condition = (
      toset([for e in google_cloud_run_v2_service.api.template[0].containers[0].env : e.name if length(e.value_source) == 1])
      == toset(["DATABASE_URL", "CONTEXT_TOKEN_SECRET"])
    )
    error_message = "DATABASE_URL and CONTEXT_TOKEN_SECRET must come from Secret Manager."
  }

  assert {
    condition     = google_secret_manager_secret_version.api["DATABASE_URL"].secret_data == "postgresql+asyncpg://app:${random_password.db.result}@/app?host=/cloudsql/demo:europe-west1:api"
    error_message = "Production must connect through the Cloud SQL unix socket."
  }

  assert {
    condition     = length(google_secret_manager_secret_iam_member.runtime) == 2 && length(google_project_iam_member.runtime_cloudsql) == 1
    error_message = "The runtime account needs secret access and the Cloud SQL client role."
  }

  assert {
    condition     = output.runtime_service_account_unique_id == google_service_account.runtime.unique_id
    error_message = "The runtime account's numeric id must be an output."
  }

  assert {
    condition     = google_sql_database_instance.db.settings[0].ip_configuration[0].ssl_mode == "ENCRYPTED_ONLY"
    error_message = "Cloud SQL must refuse unencrypted connections."
  }

  assert {
    condition     = google_sql_database_instance.db.deletion_protection && google_cloud_run_v2_service.api.deletion_protection
    error_message = "Deletion protection must default to on."
  }

  assert {
    condition = (
      google_storage_bucket.uploads.name == "demo-uploads"
      && !google_storage_bucket.uploads.force_destroy
      && google_storage_bucket.uploads.public_access_prevention == "enforced"
      && output.uploads_bucket == "demo-uploads"
      && contains([for e in google_cloud_run_v2_service.api.template[0].containers[0].env : e.value if e.name == "GCS_BUCKET"], "demo-uploads")
    )
    error_message = "The API must get its private uploads bucket as GCS_BUCKET."
  }

  assert {
    condition = (
      google_storage_bucket_iam_member.runtime_uploads.role == "roles/storage.objectUser"
      && google_storage_bucket_iam_member.runtime_uploads.bucket == google_storage_bucket.uploads.name
    )
    error_message = "The runtime account needs object access on the uploads bucket only."
  }
}

run "emulator_uses_tcp_and_plain_environment" {
  command = apply

  variables {
    emulator = true
  }

  assert {
    condition     = length(google_cloud_run_v2_service.api.template[0].volumes) == 0
    error_message = "The emulator has no Cloud SQL volume."
  }

  assert {
    condition = (
      toset([for e in google_cloud_run_v2_service.api.template[0].containers[0].env : e.name if e.value != ""])
      == toset(["AGENT_URL", "GCS_BUCKET", "DATABASE_URL", "CONTEXT_TOKEN_SECRET"])
    )
    error_message = "The emulator must get the secrets as plain values."
  }

  assert {
    condition     = google_secret_manager_secret_version.api["DATABASE_URL"].secret_data == "postgresql+asyncpg://app:${random_password.db.result}@10.0.0.2:5432/app"
    error_message = "The emulator must connect over TCP to the instance address."
  }

  assert {
    condition     = length(google_secret_manager_secret_iam_member.runtime) == 0 && length(google_project_iam_member.runtime_cloudsql) == 0
    error_message = "The emulator needs no secret or Cloud SQL grants."
  }
}

run "the_model_key_is_a_secret_when_given" {
  command = apply

  variables {
    openrouter_api_key = "sk-test"
  }

  assert {
    condition     = contains([for e in google_cloud_run_v2_service.api.template[0].containers[0].env : e.name if length(e.value_source) == 1], "OPENROUTER_API_KEY")
    error_message = "OPENROUTER_API_KEY must come from Secret Manager."
  }

  assert {
    condition     = google_secret_manager_secret.api["OPENROUTER_API_KEY"].secret_id == "api-OPENROUTER_API_KEY" && length(google_secret_manager_secret_iam_member.runtime) == 3
    error_message = "The key needs its secret, readable by the runtime account."
  }
}

run "the_model_key_is_plain_in_the_emulator" {
  command = apply

  variables {
    emulator           = true
    openrouter_api_key = "sk-test"
  }

  assert {
    condition     = contains([for e in google_cloud_run_v2_service.api.template[0].containers[0].env : e.name if e.value == "sk-test"], "OPENROUTER_API_KEY")
    error_message = "The emulator must get the key as a plain value."
  }
}

run "production_mounts_secrets_and_uses_the_vpc" {
  command = apply

  variables {
    vpc          = { network = "projects/demo/global/networks/n", subnetwork = "projects/demo/regions/europe-west1/subnetworks/s" }
    secret_files = { ca = { secret_id = "other-ca", path = "/ate/ca/ca.pem" } }
  }

  assert {
    condition = (
      length(google_cloud_run_v2_service.api.template[0].vpc_access) == 1
      && google_cloud_run_v2_service.api.template[0].vpc_access[0].egress == "PRIVATE_RANGES_ONLY"
    )
    error_message = "The service must use direct VPC egress for private ranges only."
  }

  assert {
    condition = (
      google_cloud_run_v2_service.api.template[0].volumes[0].secret[0].secret == "other-ca"
      && google_cloud_run_v2_service.api.template[0].volumes[0].secret[0].items[0].version == "latest"
      && google_cloud_run_v2_service.api.template[0].volumes[0].secret[0].items[0].path == "ca.pem"
      && google_cloud_run_v2_service.api.template[0].containers[0].volume_mounts[0].mount_path == "/ate/ca"
    )
    error_message = "A secret file must be mounted from its own directory at its latest version."
  }

  assert {
    condition     = length(google_secret_manager_secret_iam_member.runtime_external) == 1
    error_message = "The runtime account must read the mounted secret."
  }

  assert {
    condition     = [for f in google_sql_database_instance.db.settings[0].database_flags : f.name] == ["cloudsql.iam_authentication"]
    error_message = "The instance must allow IAM database authentication."
  }
}

run "emulator_skips_the_vpc_and_secret_mounts" {
  command = apply

  variables {
    emulator     = true
    vpc          = { network = "projects/demo/global/networks/n", subnetwork = "projects/demo/regions/europe-west1/subnetworks/s" }
    secret_files = { ca = { secret_id = "other-ca", path = "/ate/ca/ca.pem" } }
  }

  assert {
    condition = (
      length(google_cloud_run_v2_service.api.template[0].vpc_access) == 0
      && length(google_cloud_run_v2_service.api.template[0].volumes) == 0
      && length(google_secret_manager_secret_iam_member.runtime_external) == 0
    )
    error_message = "floci-gcp can neither give a service a VPC nor mount secrets."
  }
}

# The state of the run above stays: a new image must be rolled out by Terraform together with the environment.
run "terraform_owns_the_image" {
  command = plan

  variables {
    image = "example/api:2"
  }

  assert {
    condition     = google_cloud_run_v2_service.api.template[0].containers[0].image == "example/api:2"
    error_message = "A new image must change the Cloud Run revision template."
  }
}
