# Private services access, so Cloud SQL gets a private IP inside this VPC.
resource "google_compute_global_address" "private_services" {
  name          = "justpass-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.main.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.main.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
  # Cloud SQL keeps the peering busy for a while after it is deleted; this removes it anyway.
  deletion_policy = "REMOVE_PEERING"
}

# One small instance with two databases: "app" for the api, "atepg" for Substrate.
# Both log in with IAM database auth through the Cloud SQL Auth Proxy: no passwords.
resource "google_sql_database_instance" "main" {
  name                = "justpass"
  database_version    = "POSTGRES_18"
  region              = var.region
  deletion_protection = var.deletion_protection

  settings {
    edition                     = "ENTERPRISE"
    tier                        = var.db_tier
    availability_type           = "ZONAL"
    disk_size                   = 10
    deletion_protection_enabled = var.deletion_protection

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.main.id
      ssl_mode        = "ENCRYPTED_ONLY"
    }

    database_flags {
      name  = "cloudsql.iam_authentication"
      value = "on"
    }

    backup_configuration {
      enabled = true
    }
  }

  depends_on = [google_service_networking_connection.private_services]
}

resource "google_sql_database" "db" {
  for_each = toset(["app", "atepg"])
  name     = each.value
  instance = google_sql_database_instance.main.name
  # Dropping fails while clients are still connected; the instance delete removes it anyway.
  deletion_policy = "ABANDON"
}

resource "google_sql_user" "iam" {
  for_each = {
    api     = google_service_account.api.email
    ate_api = google_service_account.ate_api.email
  }
  # Cloud SQL IAM user names are the service account email without ".gserviceaccount.com".
  name     = trimsuffix(each.value, ".gserviceaccount.com")
  instance = google_sql_database_instance.main.name
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
  # A user that owns tables cannot be dropped; the instance delete removes it anyway.
  deletion_policy = "ABANDON"
}
