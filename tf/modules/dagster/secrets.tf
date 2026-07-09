# Database password - randomly generated
resource "random_password" "db_password" {
  length           = 32
  special          = true
  override_special = "_-" # Conservative set safe for connection strings
}

locals {
  effective_db_password = var.db_password != null ? var.db_password : random_password.db_password.result
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
  secret_data = local.effective_db_password
}

# Full PostgreSQL connection URL for Unix socket connections
# Format: postgresql://user:pass@/dbname?host=/cloudsql/connection
resource "google_secret_manager_secret" "postgres_url" {
  secret_id = "dagster-postgres-url"
  project   = var.project_id

  labels = local.common_labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "postgres_url" {
  secret      = google_secret_manager_secret.postgres_url.id
  secret_data = "postgresql://${var.db_user}:${local.effective_db_password}@/${var.db_name}?host=${local.db_socket_path}"
}
