resource "google_cloud_run_v2_service" "archiver" {
  name     = var.service_name
  location = var.region
  project  = var.project_id

  template {
    service_account = google_service_account.archiver.email

    # Mount agencies config from Secret Manager
    volumes {
      name = "agencies-config"
      secret {
        secret       = var.agencies_secret_id
        default_mode = 292  # 0444 in octal
        items {
          path    = "agencies.yaml"
          version = "latest"
        }
      }
    }

    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
        cpu_idle = false  # Keep CPU allocated for scheduler
      }

      ports {
        container_port = 8080
      }

      # Mount agencies config as a file
      volume_mounts {
        name       = "agencies-config"
        mount_path = "/config"
      }

      # Core configuration
      env {
        name  = "CONFIG_PATH"
        value = "/config/agencies.yaml"
      }
      env {
        name  = "GCS_BUCKET_RT_PROTOBUF"
        value = google_storage_bucket.protobuf.name
      }
      env {
        name  = "GCS_BUCKET_RT_PARQUET"
        value = google_storage_bucket.parquet.name
      }
      env {
        name  = "MAX_CONCURRENT"
        value = tostring(var.max_concurrent)
      }
      env {
        name  = "LOG_LEVEL"
        value = var.log_level
      }
      env {
        name  = "LOG_FORMAT"
        value = "json"
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 5
        failure_threshold     = 3
        timeout_seconds       = 5
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        period_seconds    = 30
        failure_threshold = 3
        timeout_seconds   = 10
      }
    }

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_storage_bucket_iam_member.archiver_protobuf,
    google_storage_bucket_iam_member.archiver_parquet_read,
    google_storage_bucket_iam_member.archiver_parquet_write,
  ]
}

# Allow unauthenticated access to the service (for health checks and metrics)
resource "google_cloud_run_v2_service_iam_member" "public_access" {
  name     = google_cloud_run_v2_service.archiver.name
  location = google_cloud_run_v2_service.archiver.location
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "allUsers"
}
