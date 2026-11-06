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
    condition     = google_sql_database_instance.db.deletion_protection && google_cloud_run_v2_service.api.deletion_protection
    error_message = "Deletion protection must default to on."
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
      == toset(["AGENT_URL", "DATABASE_URL", "CONTEXT_TOKEN_SECRET"])
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

# The state of the run above stays: a new image (CI's deploy) must not make Terraform redeploy the old one.
run "ci_owns_the_image" {
  command = plan

  variables {
    image = "example/api:2"
  }

  assert {
    condition     = google_cloud_run_v2_service.api.template[0].containers[0].image == "example/api:1"
    error_message = "Terraform must ignore changes to the image."
  }
}
