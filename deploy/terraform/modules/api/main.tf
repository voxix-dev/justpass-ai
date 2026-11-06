# The API on Cloud Run with its Postgres on Cloud SQL.
#
# `var.emulator` switches only what floci-gcp cannot do (see its branches below):
#   production: the API reaches Postgres through the Cloud SQL volume and reads its secrets from Secret Manager;
#   emulator:   the API reaches Postgres over TCP and gets the same values as plain environment variables.

locals {
  db_name = "app"
  db_user = "app"

  secret_names = toset(["DATABASE_URL", "CONTEXT_TOKEN_SECRET"])

  # floci-gcp starts a Postgres container per instance, with the instance's IP on the container network.
  db_host = var.emulator ? "${google_sql_database_instance.db.ip_address[0].ip_address}:5432" : ""
  database_url = (var.emulator
    ? "postgresql+asyncpg://${local.db_user}:${random_password.db.result}@${local.db_host}/${local.db_name}"
    : "postgresql+asyncpg://${local.db_user}:${random_password.db.result}@/${local.db_name}?host=/cloudsql/${google_sql_database_instance.db.connection_name}"
  )
  secret_values = {
    DATABASE_URL         = local.database_url
    CONTEXT_TOKEN_SECRET = random_password.context_token.result
  }
}

# Letters and digits only: the password goes into a URL unescaped.
resource "random_password" "db" {
  length  = 32
  special = false
}

resource "random_password" "context_token" {
  length  = 48
  special = false
}

resource "google_sql_database_instance" "db" {
  name                = "api"
  project             = var.project_id
  region              = var.region
  database_version    = "POSTGRES_18"
  deletion_protection = var.deletion_protection

  settings {
    tier    = var.db_tier
    edition = "ENTERPRISE" # the default of newer versions, Enterprise Plus, has no custom tiers

    ip_configuration {
      ssl_mode = "ENCRYPTED_ONLY"
    }

    backup_configuration {
      enabled = true
    }
  }
}

resource "google_sql_database" "app" {
  name     = local.db_name
  project  = var.project_id
  instance = google_sql_database_instance.db.name
}

resource "google_sql_user" "app" {
  name     = local.db_user
  project  = var.project_id
  instance = google_sql_database_instance.db.name
  password = random_password.db.result
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "api-runtime"
  display_name = "API on Cloud Run"
}

resource "google_secret_manager_secret" "api" {
  for_each  = local.secret_names
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

resource "google_secret_manager_secret_version" "api" {
  for_each    = local.secret_names
  secret      = google_secret_manager_secret.api[each.key].id
  secret_data = local.secret_values[each.key]
}

# Only production reads secrets from Secret Manager and connects through Cloud SQL.
resource "google_secret_manager_secret_iam_member" "runtime" {
  for_each  = var.emulator ? toset([]) : local.secret_names
  project   = var.project_id
  secret_id = google_secret_manager_secret.api[each.key].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_project_iam_member" "runtime_cloudsql" {
  count   = var.emulator ? 0 : 1
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_run_v2_service" "api" {
  name                = "api"
  project             = var.project_id
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.deletion_protection

  # Always serve the latest revision, which deploy-api.sh checks after a deploy.
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    dynamic "volumes" {
      for_each = var.emulator ? [] : [1]
      content {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.db.connection_name]
        }
      }
    }

    containers {
      image = var.image

      ports {
        container_port = 8000
      }

      resources {
        cpu_idle = false # background sweeping needs CPU between requests
      }

      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = var.emulator ? local.secret_names : []
        content {
          name  = env.key
          value = local.secret_values[env.key]
        }
      }

      dynamic "env" {
        for_each = var.emulator ? [] : local.secret_names
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.api[env.key].secret_id
              version = "latest"
            }
          }
        }
      }

      dynamic "volume_mounts" {
        for_each = var.emulator ? [] : [1]
        content {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }
  }

  # CI deploys the image (deploy/gcp/deploy-api.sh); Terraform only sets the first one.
  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_secret_manager_secret_version.api,
    google_secret_manager_secret_iam_member.runtime,
    google_project_iam_member.runtime_cloudsql,
    google_sql_database.app,
    google_sql_user.app,
  ]
}

# The API checks Firebase ID tokens itself, so Cloud Run lets everyone call it.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
