# Cloud SQL PostgreSQL instance for Dagster

resource "google_sql_database_instance" "dagster" {
  name             = "dagster-postgres"
  database_version = "POSTGRES_14"
  region           = var.region
  project          = var.project_id

  settings {
    tier              = var.cloudsql_tier
    availability_type = "ZONAL"
    disk_type         = "PD_SSD"
    disk_size         = 10
    disk_autoresize   = true

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      start_time                     = "03:00" # 3 AM UTC
      transaction_log_retention_days = 7

      backup_retention_settings {
        retained_backups = 7
        retention_unit   = "COUNT"
      }
    }

    ip_configuration {
      # Public IP required for Cloud Run's Cloud SQL socket mount
      ipv4_enabled = true

      # No authorized networks - Cloud Run uses Cloud SQL Auth Proxy
      # via socket mount, not direct IP connections
    }

    maintenance_window {
      day          = 7 # Sunday
      hour         = 4 # 4 AM UTC
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled  = true
      query_string_length     = 1024
      record_application_tags = true
      record_client_address   = false
    }
  }

  deletion_protection = true

  lifecycle {
    prevent_destroy = true
  }
}
