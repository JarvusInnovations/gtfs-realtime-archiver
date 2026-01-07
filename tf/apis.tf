# Enable required GCP APIs
# These APIs must be enabled before creating resources that depend on them

resource "google_project_service" "sqladmin" {
  project = var.project_id
  service = "sqladmin.googleapis.com"

  # Don't disable on destroy to avoid breaking existing resources
  disable_on_destroy = false
}

# Cloud Run API (should already be enabled, but explicit dependency is good)
resource "google_project_service" "run" {
  project = var.project_id
  service = "run.googleapis.com"

  disable_on_destroy = false
}

# Secret Manager API
resource "google_project_service" "secretmanager" {
  project = var.project_id
  service = "secretmanager.googleapis.com"

  disable_on_destroy = false
}
