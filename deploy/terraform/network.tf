resource "google_compute_network" "main" {
  name                    = "justpass"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "gke" {
  name                     = "justpass-gke"
  network                  = google_compute_network.main.id
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

# Nodes have no public IPs; they reach OpenRouter and the search API through Cloud NAT.
resource "google_compute_router" "nat" {
  name    = "justpass-nat"
  network = google_compute_network.main.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "justpass-nat"
  router                             = google_compute_router.nat.name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
