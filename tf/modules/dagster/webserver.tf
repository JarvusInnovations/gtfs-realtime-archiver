# Cloud Run service for Dagster webserver (UI)

resource "google_cloud_run_v2_service" "webserver" {
  # Use beta provider for IAP support
  provider = google-beta

  name     = "dagster-webserver"
  location = var.region
  project  = var.project_id

  # Enable IAP (Preview feature) - requires BETA launch stage
  launch_stage = var.iap_allowed_domain != null ? "BETA" : null
  iap_enabled  = var.iap_allowed_domain != null

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

# Public access when IAP is disabled
# SECURITY WARNING: This allows unauthenticated access to the Dagster UI.
# Only created when var.iap_allowed_domain is null.
resource "google_cloud_run_v2_service_iam_member" "webserver_public_invoker" {
  count = var.iap_allowed_domain == null ? 1 : 0

  provider = google-beta
  name     = google_cloud_run_v2_service.webserver.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# IAP service agent invoker - allows IAP to call Cloud Run
resource "google_cloud_run_v2_service_iam_member" "webserver_iap_invoker" {
  count = var.iap_allowed_domain != null ? 1 : 0

  provider = google-beta
  name     = google_cloud_run_v2_service.webserver.name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${var.project_number}@gcp-sa-iap.iam.gserviceaccount.com"
}

# IAP access for Google Workspace domain
resource "google_iap_web_cloud_run_service_iam_member" "webserver_domain_access" {
  count = var.iap_allowed_domain != null ? 1 : 0

  provider               = google-beta
  project                = var.project_number # Must use project number, not ID
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.webserver.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = "domain:${var.iap_allowed_domain}"
}

# Custom domain mapping for webserver
resource "google_cloud_run_domain_mapping" "webserver" {
  count = var.custom_domain != null ? 1 : 0

  name     = var.custom_domain
  location = var.region
  project  = var.project_id

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.webserver.name
  }
}
