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

  # Consumer-domain wiring: the module is generic, this project's buckets and
  # secrets are expressed as env + grants (see modules/dagster/variables.tf).
  extra_env = {
    GCS_BUCKET_RT_PROTOBUF = google_storage_bucket.protobuf.name
    GCS_BUCKET_RT_PARQUET  = google_storage_bucket.parquet.name
    AGENCIES_SECRET_ID     = var.agencies_secret_id
  }

  bucket_grants = {
    # Sensors (primary SA) discover feeds by listing; run workers read source
    # protobufs for compaction.
    rt-protobuf = {
      bucket          = google_storage_bucket.protobuf.name
      dagster_role    = "roles/storage.objectViewer"
      run_worker_role = "roles/storage.objectViewer"
    }
    # Run workers write compacted parquet output.
    rt-parquet = {
      bucket          = google_storage_bucket.parquet.name
      run_worker_role = "roles/storage.objectUser"
    }
  }

  secret_grants = {
    # agencies.yaml config, read by the feeds_metadata asset in run workers.
    agencies = {
      secret_id  = var.agencies_secret_id
      run_worker = true
    }
  }

  deployment_mode = var.dagster_deployment_mode

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
