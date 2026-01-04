# Protobuf bucket - raw GTFS-RT protobuf snapshots
# Written by archiver service, read by Dagster for compaction
resource "google_storage_bucket" "protobuf" {
  name     = var.protobuf_bucket_name
  location = var.bucket_location
  project  = var.project_id

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = 365 # Keep data for 1 year
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age = 30 # Move to Nearline after 30 days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 90 # Move to Coldline after 90 days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  versioning {
    enabled = false # No versioning needed for append-only archives
  }
}

# Parquet bucket - compacted GTFS-RT parquet files
# Written by Dagster, read by analytics tools
resource "google_storage_bucket" "parquet" {
  name     = var.parquet_bucket_name
  location = var.bucket_location
  project  = var.project_id

  uniform_bucket_level_access = true

  # Parquet files are read more frequently for analytics, so slower tiering
  lifecycle_rule {
    condition {
      age = 90 # Move to Nearline after 90 days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  lifecycle_rule {
    condition {
      age = 180 # Move to Coldline after 180 days
    }
    action {
      type          = "SetStorageClass"
      storage_class = "COLDLINE"
    }
  }

  # No auto-delete - keep parquet files indefinitely for historical analysis

  versioning {
    enabled = false
  }
}

# Public read access for parquet bucket
resource "google_storage_bucket_iam_member" "parquet_public_read" {
  bucket = google_storage_bucket.parquet.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
