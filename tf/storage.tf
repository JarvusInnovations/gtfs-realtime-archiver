resource "google_storage_bucket" "archive" {
  name     = var.bucket_name
  location = var.bucket_location
  project  = var.project_id

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = 365  # Keep data for 1 year
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age = 30  # Move to Nearline after 30 days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 90  # Move to Coldline after 90 days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  versioning {
    enabled = false  # No versioning needed for append-only archives
  }
}
