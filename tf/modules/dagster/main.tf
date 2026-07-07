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

  # Deployment topology toggles (see var.deployment_mode)
  is_consolidated = var.deployment_mode == "consolidated"
  is_split        = var.deployment_mode == "split"

  # Consolidated mode co-locates a single code location. Pick the (only) entry.
  consolidated_location_key = try(keys(var.code_locations)[0], null)
  consolidated_location     = try(var.code_locations[local.consolidated_location_key], null)

  # The Service that carries the webserver ingress, used to wire IAP, the public
  # invoker, and the custom domain mapping in both modes. Splats + one() stay safe
  # when the non-active resource has count = 0.
  webserver_service_name = local.is_consolidated ? one(google_cloud_run_v2_service.consolidated[*].name) : one(google_cloud_run_v2_service.webserver[*].name)
  webserver_service_uri  = local.is_consolidated ? one(google_cloud_run_v2_service.consolidated[*].uri) : one(google_cloud_run_v2_service.webserver[*].uri)

  # Use provided logs bucket or create one
  logs_bucket_name = var.logs_bucket_name != null ? var.logs_bucket_name : google_storage_bucket.logs[0].name

  # Database connection string for Unix socket
  # Cloud Run mounts Cloud SQL at /cloudsql/{connection_name}
  db_socket_path = "/cloudsql/${var.cloud_sql_connection_name}"

  # Common environment variables for all Dagster components
  # Note: Database connection is via DAGSTER_POSTGRES_URL secret (includes socket path)
  # Note: Run worker job name is hardcoded in deploy/dagster.yaml (Permissive config doesn't resolve env vars)
  common_env = {
    GCP_PROJECT_ID         = var.project_id
    GCP_REGION             = var.region
    DAGSTER_HOME           = "/opt/dagster/dagster_home"
    GCS_BUCKET_RT_PROTOBUF = var.protobuf_bucket_name
    GCS_BUCKET_RT_PARQUET  = var.parquet_bucket_name
    DAGSTER_LOGS_BUCKET    = local.logs_bucket_name
    AGENCIES_SECRET_ID     = var.agencies_secret_id
  }
}

# Get project data for default compute service account reference
data "google_project" "current" {
  project_id = var.project_id
}

# Note: Dagster config files are baked into container images at build time
# with environment variable placeholders. All values passed via env vars.
