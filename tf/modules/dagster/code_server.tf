# Cloud Run services for Dagster code servers (gRPC)
# One service per code location

resource "google_cloud_run_v2_service" "code_server" {
  for_each = var.code_locations

  name     = "dagster-code-server-${each.key}"
  location = var.region
  project  = var.project_id

  # Internal only - accessed by webserver and daemon
  ingress = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  # Allow replacement during development
  deletion_protection = false

  labels = local.common_labels

  template {
    service_account = google_service_account.dagster.email

    # No VPC connector - use Cloud SQL socket mount
    # Cloud Run connects to Cloud SQL via built-in Cloud SQL Auth Proxy

    # Dagster code server can only have 1 instance
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    # Config files volumes (from Secret Manager)
    # Order matches Cloud Run's stored order to prevent drift
    volumes {
      name = "dagster-config"
      secret {
        secret       = google_secret_manager_secret.dagster_config.secret_id
        default_mode = 292 # 0444
        items {
          path    = "dagster.yaml"
          version = "latest"
        }
      }
    }

    # Cloud SQL volume mount
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.cloud_sql_connection_name]
      }
    }

    containers {
      name  = "code-server"
      image = each.value.image

      # Run dagster gRPC API server
      command = [
        "dagster", "api", "grpc",
        "--host", "0.0.0.0",
        "--port", tostring(each.value.port),
        "--module-name", each.value.module_name
      ]

      # Mount config secrets to separate directories
      # Symlinks in container point from DAGSTER_HOME to these mount points
      volume_mounts {
        name       = "dagster-config"
        mount_path = "/mnt/dagster-config"
      }

      # Mount Cloud SQL socket
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # Environment variables
      dynamic "env" {
        for_each = local.common_env
        content {
          name  = env.key
          value = env.value
        }
      }

      # Database password from Secret Manager
      env {
        name = "DAGSTER_POSTGRES_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }

      # Current image for Dagster to identify the code location image
      env {
        name  = "DAGSTER_CURRENT_IMAGE"
        value = each.value.image
      }

      # gRPC port (h2c = HTTP/2 cleartext)
      ports {
        name           = "h2c"
        container_port = each.value.port
      }

      resources {
        limits = {
          cpu    = var.code_server_resources.cpu
          memory = var.code_server_resources.memory
        }
        cpu_idle = true # Scale to zero when not in use
      }

      # gRPC startup probe
      startup_probe {
        grpc {
          port    = each.value.port
          service = "DagsterApi"
        }
        initial_delay_seconds = 0
        timeout_seconds       = 30
        period_seconds        = 30
        failure_threshold     = 3
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.dagster_db_password
  ]
}

# Allow unauthenticated access (internal only anyway)
resource "google_cloud_run_v2_service_iam_member" "code_server_invoker" {
  for_each = google_cloud_run_v2_service.code_server

  name     = each.value.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "allUsers"
}
