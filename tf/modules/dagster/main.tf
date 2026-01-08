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

  # Naming strategy: omit code location suffix for single location
  is_single_location = length(var.code_locations) == 1

  # Run worker job names for run launcher config
  run_worker_names = {
    for k, v in var.code_locations : k => local.is_single_location ? "dagster-run-worker" : "dagster-run-worker-${k}"
  }

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
    DAGSTER_RUN_JOB_NAME   = local.run_worker_names["gtfsrt"]
  }
}

# Get project data for default compute service account reference
data "google_project" "current" {
  project_id = var.project_id
}

# Note: Dagster config files are baked into container images at build time
# with environment variable placeholders. All values passed via env vars.
