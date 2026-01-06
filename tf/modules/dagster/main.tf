# Dagster Cloud Run Deployment Module
# Deploys Dagster to Google Cloud Run with:
# - Webserver (Cloud Run Service)
# - Daemon (Cloud Run Worker Pool)
# - Code Server (Cloud Run Service per code location)
# - Run Worker (Cloud Run Job per code location)

locals {
  # Labels applied to all resources
  common_labels = merge(var.labels, {
    managed-by = "terraform"
    component  = "dagster"
  })

  # Use provided logs bucket or create one
  logs_bucket_name = var.logs_bucket_name != null ? var.logs_bucket_name : google_storage_bucket.logs[0].name

  # Database connection string for Unix socket
  # Cloud Run mounts Cloud SQL at /cloudsql/{connection_name}
  db_socket_path = "/cloudsql/${var.cloud_sql_connection_name}"

  # Common environment variables for all Dagster components
  common_env = {
    GCP_PROJECT_ID         = var.project_id
    GCP_REGION             = var.region
    DAGSTER_HOME           = "/opt/dagster/dagster_home"
    DAGSTER_POSTGRES_HOST  = local.db_socket_path
    DAGSTER_POSTGRES_DB    = var.db_name
    DAGSTER_POSTGRES_USER  = var.db_user
    GCS_BUCKET_RT_PROTOBUF = var.protobuf_bucket_name
    GCS_BUCKET_RT_PARQUET  = var.parquet_bucket_name
    DAGSTER_LOGS_BUCKET    = local.logs_bucket_name
  }
}

# Get project data for default compute service account reference
data "google_project" "current" {
  project_id = var.project_id
}
