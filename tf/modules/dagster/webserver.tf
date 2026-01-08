# Cloud Run service for Dagster webserver (UI)

resource "google_cloud_run_v2_service" "webserver" {
  name     = "dagster-webserver"
  location = var.region
  project  = var.project_id

  # Allow external access to the UI
  ingress = "INGRESS_TRAFFIC_ALL"

  # Allow replacement during development
  deletion_protection = false

  labels = local.common_labels

  template {
    service_account = google_service_account.dagster.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    # Cloud SQL volume mount for Unix socket connection
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.cloud_sql_connection_name]
      }
    }

    containers {
      name  = "webserver"
      image = var.webserver_image

      # Run Dagster webserver
      command = ["dagster-webserver", "--host", "0.0.0.0", "--port", "3000"]

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

      # Code server URLs for gRPC connections
      dynamic "env" {
        for_each = google_cloud_run_v2_service.code_server
        content {
          name  = "CODE_SERVER_HOST_${upper(env.key)}"
          value = trimprefix(env.value.uri, "https://")
        }
      }

      # Database connection URL from Secret Manager (includes password)
      env {
        name = "DAGSTER_POSTGRES_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.postgres_url.secret_id
            version = "latest"
          }
        }
      }

      ports {
        name           = "http1"
        container_port = 3000
      }

      resources {
        limits = {
          cpu    = var.webserver_resources.cpu
          memory = var.webserver_resources.memory
        }
        cpu_idle          = true # Scale to zero when not in use
        startup_cpu_boost = true
      }

      # Startup probe
      startup_probe {
        http_get {
          path = "/server_info"
          port = 3000
        }
        initial_delay_seconds = 5
        timeout_seconds       = 5
        period_seconds        = 10
        failure_threshold     = 12 # 2 minutes startup time
      }

      # Liveness probe
      liveness_probe {
        http_get {
          path = "/server_info"
          port = 3000
        }
        period_seconds    = 30
        timeout_seconds   = 5
        failure_threshold = 3
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.dagster_postgres_url
  ]
}

# SECURITY WARNING: Unauthenticated webserver access
# This allows public access to the Dagster UI without authentication.
#
# For production deployments, you should:
# 1. Use Cloud Run IAP (Identity-Aware Proxy) for authentication
# 2. Or use Cloud Run authentication with service accounts
# 3. Or place behind Cloud Load Balancer with IAP/OAuth
#
# To enable authentication, remove this resource and configure IAP:
# https://cloud.google.com/run/docs/authenticating/public
resource "google_cloud_run_v2_service_iam_member" "webserver_invoker" {
  name     = google_cloud_run_v2_service.webserver.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "allUsers"
}
