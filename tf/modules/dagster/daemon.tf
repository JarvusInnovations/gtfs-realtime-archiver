# Cloud Run Worker Pool for Dagster daemon
# Worker Pools are designed for continuous background work without HTTP endpoints

resource "google_cloud_run_v2_worker_pool" "daemon" {
  name     = "dagster-daemon"
  location = var.region
  project  = var.project_id

  # Worker Pool is in BETA
  launch_stage = "BETA"

  # Prevent accidental deletion
  deletion_protection = false

  labels = local.common_labels

  # Manual scaling - daemon needs exactly 1 instance
  scaling {
    scaling_mode          = "MANUAL"
    manual_instance_count = 1
  }

  template {
    service_account = google_service_account.dagster.email

    # Cloud SQL volume mount
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.cloud_sql_connection_name]
      }
    }

    # Config files volume (from Secret Manager)
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

    volumes {
      name = "workspace-config"
      secret {
        secret       = google_secret_manager_secret.workspace_config.secret_id
        default_mode = 292 # 0444
        items {
          path    = "workspace.yaml"
          version = "latest"
        }
      }
    }

    containers {
      name  = "daemon"
      image = var.daemon_image

      # Run Dagster daemon
      command = ["dagster-daemon", "run"]

      # Mount Cloud SQL socket
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # Mount config secrets to separate directories
      # Symlinks in container point from DAGSTER_HOME to these mount points
      volume_mounts {
        name       = "dagster-config"
        mount_path = "/mnt/dagster-config"
      }

      volume_mounts {
        name       = "workspace-config"
        mount_path = "/mnt/workspace-config"
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
          cpu    = var.daemon_resources.cpu
          memory = var.daemon_resources.memory
        }
      }

      # Note: No liveness probe - dagster-daemon doesn't expose HTTP/TCP endpoints
      # Cloud Run's automatic restart policy handles daemon crashes
    }
  }

  # Ensure 100% of instances on latest revision
  instance_splits {
    type    = "INSTANCE_SPLIT_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_secret_manager_secret_iam_member.dagster_db_password,
    google_secret_manager_secret_iam_member.dagster_config,
    google_secret_manager_secret_iam_member.dagster_workspace,
    google_cloud_run_v2_service.code_server
  ]
}
