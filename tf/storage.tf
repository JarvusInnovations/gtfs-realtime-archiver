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

  # NOTE: no SetStorageClass tiering here, on purpose. The bucket holds
  # millions of tiny protobuf objects; lifecycle transitions bill a Class A
  # operation per object at the destination class's rate, which cost ~9x the
  # storage itself (June 2026: ~$305/mo in transition ops vs ~$34/mo storage).
  # Objects stay STANDARD until the free age-365 delete reaps them.

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

  # Allow browser fetch from gtfsrt.io (and any origin for public data)
  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Content-Length"]
    max_age_seconds = 3600
  }

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
