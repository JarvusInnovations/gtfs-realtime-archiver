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

# Dagster configuration (dagster.yaml) stored in Secret Manager
resource "google_secret_manager_secret" "dagster_config" {
  secret_id = "dagster-config"
  project   = var.project_id

  labels = local.common_labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "dagster_config" {
  secret      = google_secret_manager_secret.dagster_config.id
  secret_data = templatefile("${path.module}/config/dagster.yaml.tftpl", local.dagster_config_vars)
}

# Workspace configuration (workspace.yaml) stored in Secret Manager
resource "google_secret_manager_secret" "workspace_config" {
  secret_id = "dagster-workspace"
  project   = var.project_id

  labels = local.common_labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "workspace_config" {
  secret      = google_secret_manager_secret.workspace_config.id
  secret_data = templatefile("${path.module}/config/workspace.yaml.tftpl", local.workspace_config_vars)
}

# Workspace config access for primary SA
resource "google_secret_manager_secret_iam_member" "dagster_workspace" {
  secret_id = google_secret_manager_secret.workspace_config.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.dagster.email}"
  project   = var.project_id
}
