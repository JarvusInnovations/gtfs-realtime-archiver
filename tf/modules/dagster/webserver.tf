# Cloud Run service for Dagster webserver (UI)

resource "google_cloud_run_v2_service" "webserver" {
  # Only created in split mode; consolidated mode uses google_cloud_run_v2_service.consolidated
  count = local.is_split ? 1 : 0

  # Use beta provider for IAP support
  provider = google-beta

  name     = "dagster-webserver"
  location = var.region
  project  = var.project_id

  # Cloud Run IAP is GA (Google auto-promoted deployed services' launch_stage;
  # forcing BETA here would show as a perpetual GA -> BETA plan diff).
  iap_enabled = var.iap_allowed_domain != null

  # Ingress stays INGRESS_TRAFFIC_ALL so other Cloud Run services in the same
  # project can reach it without a VPC connector — Cloud-Run-to-Cloud-Run
  # traffic uses the public endpoint by default. Access control happens
  # entirely at the IAM layer (the `allUsers` invoker binding below is only
  # created when public_ingress = true; in private mode the consumer grants
  # `roles/run.invoker` narrowly to a specific SA).
  ingress = "INGRESS_TRAFFIC_ALL"

  # Allow replacement during development
  deletion_protection = false

  labels = local.common_labels

  template {
    service_account = google_service_account.dagster.email

    scaling {
      min_instance_count = var.webserver_min_instances
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

      # Run Dagster webserver. --path-prefix lets the UI generate URLs that
      # match what a reverse proxy forwards (e.g. /dagster). Empty string keeps
      # the UI at root (the default).
      command = concat(
        ["dagster-webserver", "--host", "0.0.0.0", "--port", "3000"],
        var.path_prefix != "" ? ["--path-prefix", var.path_prefix] : []
      )

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
          path = "${var.path_prefix}/server_info"
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
          path = "${var.path_prefix}/server_info"
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

# Public unauthenticated access — only when the caller asked for it explicitly
# (public_ingress = true AND no IAP gating). In private mode the caller is
# expected to add a `roles/run.invoker` binding for the specific service
# account that will reach this webserver, outside the module.
resource "google_cloud_run_v2_service_iam_member" "webserver_public_invoker" {
  count = var.iap_allowed_domain == null && var.public_ingress ? 1 : 0

  provider = google-beta
  name     = local.webserver_service_name
  location = var.region
  project  = var.project_id
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# IAP service agent invoker - allows IAP to call Cloud Run
resource "google_cloud_run_v2_service_iam_member" "webserver_iap_invoker" {
  count = var.iap_allowed_domain != null ? 1 : 0

  provider = google-beta
  name     = local.webserver_service_name
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
  cloud_run_service_name = local.webserver_service_name
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
    route_name = local.webserver_service_name
  }
}
