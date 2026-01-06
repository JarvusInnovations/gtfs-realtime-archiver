# Optional logs bucket (created only if logs_bucket_name not provided)
resource "google_storage_bucket" "logs" {
  count = var.logs_bucket_name == null ? 1 : 0

  name                        = "dagster-logs-${var.project_id}"
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = true
  force_destroy               = false

  labels = local.common_labels

  # Delete logs after 30 days
  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }

  versioning {
    enabled = false
  }
}
