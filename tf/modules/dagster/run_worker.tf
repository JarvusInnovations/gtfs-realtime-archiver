# Cloud Run jobs for Dagster run workers
# One job per code location, launched by CloudRunRunLauncher

resource "google_cloud_run_v2_job" "run_worker" {
  for_each = var.code_locations

  name     = "dagster-run-worker-${each.key}"
  location = var.region
  project  = var.project_id

  # Allow replacement during development
  deletion_protection = false

  labels = local.common_labels

  template {
    template {
      # Use per-code-location service account for fine-grained IAM
      service_account = google_service_account.run_worker[each.key].email

      # No retries - Dagster handles run failure
      max_retries = 0

      # Job timeout
      timeout = "${var.run_timeout_seconds}s"

      # Cloud SQL volume mount
      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [var.cloud_sql_connection_name]
        }
      }

      containers {
        name  = "run-worker"
        image = each.value.image

        # Command is set dynamically by CloudRunRunLauncher via overrides
        # Default command won't be used - launcher provides dagster api execute_run args

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

        resources {
          limits = {
            cpu    = each.value.run_worker_cpu
            memory = each.value.run_worker_memory
          }
        }
      }
    }
  }

  depends_on = [
    google_secret_manager_secret_iam_member.run_worker_db_password
  ]
}
