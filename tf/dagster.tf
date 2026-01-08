# Dagster deployment module instantiation

# Get project number for IAP service account
data "google_project" "current" {
  project_id = var.project_id
}

module "dagster" {
  source = "./modules/dagster"

  project_id                = var.project_id
  region                    = var.region
  cloud_sql_connection_name = google_sql_database_instance.dagster.connection_name

  protobuf_bucket_name = google_storage_bucket.protobuf.name
  parquet_bucket_name  = google_storage_bucket.parquet.name

  webserver_image = var.dagster_webserver_image
  daemon_image    = var.dagster_daemon_image

  code_locations = {
    gtfsrt = {
      image             = var.dagster_code_server_image
      module_name       = "dagster_pipeline.definitions"
      port              = 3030
      run_worker_cpu    = "2"
      run_worker_memory = "4Gi"
    }
  }

  # IAP configuration
  iap_allowed_domain = var.dagster_iap_allowed_domain
  project_number     = data.google_project.current.number
  custom_domain      = var.dagster_iap_allowed_domain != null ? var.dagster_domain : null

  labels = {
    project = "gtfs-rt-archiver"
  }

  depends_on = [
    google_sql_database_instance.dagster
  ]
}
