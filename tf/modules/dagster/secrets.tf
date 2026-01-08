# Database password - randomly generated
resource "random_password" "db_password" {
  length           = 32
  special          = true
  override_special = "_-" # Conservative set safe for connection strings
}

# Store database password in Secret Manager
resource "google_secret_manager_secret" "db_password" {
  secret_id = "dagster-db-password"
  project   = var.project_id

  labels = local.common_labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db_password.result
}

# Note: Dagster config files are baked into container images at build time
# with environment variable placeholders. No Secret Manager storage needed.
