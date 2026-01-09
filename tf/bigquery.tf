# BigQuery dataset for GTFS-RT analytics
resource "google_bigquery_dataset" "gtfs_rt" {
  dataset_id  = "gtfs_rt"
  location    = "US"
  description = "GTFS Realtime data from Hive-partitioned Parquet files"

  access {
    role          = "OWNER"
    special_group = "projectOwners"
  }

  access {
    role          = "READER"
    user_by_email = google_service_account.metabase.email
  }
}

# Vehicle Positions - one external table for all feeds
resource "google_bigquery_table" "vehicle_positions" {
  dataset_id                   = google_bigquery_dataset.gtfs_rt.dataset_id
  table_id                     = "vehicle_positions"
  deletion_protection          = false
  ignore_auto_generated_schema = true

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = false
    source_uris   = ["gs://${google_storage_bucket.parquet.name}/vehicle_positions/*"]

    hive_partitioning_options {
      mode                     = "CUSTOM"
      source_uri_prefix        = "gs://${google_storage_bucket.parquet.name}/vehicle_positions/{date:DATE}/{base64url:STRING}"
      require_partition_filter = false
    }
  }

  schema = jsonencode([
    { name = "source_file", type = "STRING", mode = "REQUIRED" },
    { name = "feed_url", type = "STRING", mode = "REQUIRED" },
    { name = "feed_timestamp", type = "INT64", mode = "NULLABLE" },
    { name = "fetch_timestamp", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "entity_id", type = "STRING", mode = "REQUIRED" },
    { name = "trip_id", type = "STRING", mode = "NULLABLE" },
    { name = "route_id", type = "STRING", mode = "NULLABLE" },
    { name = "direction_id", type = "INT64", mode = "NULLABLE" },
    { name = "start_time", type = "STRING", mode = "NULLABLE" },
    { name = "start_date", type = "STRING", mode = "NULLABLE" },
    { name = "schedule_relationship", type = "INT64", mode = "NULLABLE" },
    { name = "vehicle_id", type = "STRING", mode = "NULLABLE" },
    { name = "vehicle_label", type = "STRING", mode = "NULLABLE" },
    { name = "license_plate", type = "STRING", mode = "NULLABLE" },
    { name = "latitude", type = "FLOAT64", mode = "NULLABLE" },
    { name = "longitude", type = "FLOAT64", mode = "NULLABLE" },
    { name = "bearing", type = "FLOAT64", mode = "NULLABLE" },
    { name = "odometer", type = "FLOAT64", mode = "NULLABLE" },
    { name = "speed", type = "FLOAT64", mode = "NULLABLE" },
    { name = "current_stop_sequence", type = "INT64", mode = "NULLABLE" },
    { name = "stop_id", type = "STRING", mode = "NULLABLE" },
    { name = "current_status", type = "INT64", mode = "NULLABLE" },
    { name = "timestamp", type = "INT64", mode = "NULLABLE" },
    { name = "congestion_level", type = "INT64", mode = "NULLABLE" },
    { name = "occupancy_status", type = "INT64", mode = "NULLABLE" },
    { name = "occupancy_percentage", type = "INT64", mode = "NULLABLE" },
  ])
}

# Trip Updates - denormalized (one row per stop_time_update)
resource "google_bigquery_table" "trip_updates" {
  dataset_id                   = google_bigquery_dataset.gtfs_rt.dataset_id
  table_id                     = "trip_updates"
  deletion_protection          = false
  ignore_auto_generated_schema = true

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = false
    source_uris   = ["gs://${google_storage_bucket.parquet.name}/trip_updates/*"]

    hive_partitioning_options {
      mode                     = "CUSTOM"
      source_uri_prefix        = "gs://${google_storage_bucket.parquet.name}/trip_updates/{date:DATE}/{base64url:STRING}"
      require_partition_filter = false
    }
  }

  schema = jsonencode([
    { name = "source_file", type = "STRING", mode = "REQUIRED" },
    { name = "feed_url", type = "STRING", mode = "REQUIRED" },
    { name = "feed_timestamp", type = "INT64", mode = "NULLABLE" },
    { name = "fetch_timestamp", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "entity_id", type = "STRING", mode = "REQUIRED" },
    { name = "trip_id", type = "STRING", mode = "NULLABLE" },
    { name = "route_id", type = "STRING", mode = "NULLABLE" },
    { name = "direction_id", type = "INT64", mode = "NULLABLE" },
    { name = "start_time", type = "STRING", mode = "NULLABLE" },
    { name = "start_date", type = "STRING", mode = "NULLABLE" },
    { name = "schedule_relationship", type = "INT64", mode = "NULLABLE" },
    { name = "vehicle_id", type = "STRING", mode = "NULLABLE" },
    { name = "vehicle_label", type = "STRING", mode = "NULLABLE" },
    { name = "trip_timestamp", type = "INT64", mode = "NULLABLE" },
    { name = "trip_delay", type = "INT64", mode = "NULLABLE" },
    { name = "stop_sequence", type = "INT64", mode = "NULLABLE" },
    { name = "stop_id", type = "STRING", mode = "NULLABLE" },
    { name = "arrival_delay", type = "INT64", mode = "NULLABLE" },
    { name = "arrival_time", type = "INT64", mode = "NULLABLE" },
    { name = "arrival_uncertainty", type = "INT64", mode = "NULLABLE" },
    { name = "departure_delay", type = "INT64", mode = "NULLABLE" },
    { name = "departure_time", type = "INT64", mode = "NULLABLE" },
    { name = "departure_uncertainty", type = "INT64", mode = "NULLABLE" },
    { name = "stop_schedule_relationship", type = "INT64", mode = "NULLABLE" },
  ])
}

# Service Alerts - denormalized (one row per informed_entity)
resource "google_bigquery_table" "service_alerts" {
  dataset_id                   = google_bigquery_dataset.gtfs_rt.dataset_id
  table_id                     = "service_alerts"
  deletion_protection          = false
  ignore_auto_generated_schema = true

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = false
    source_uris   = ["gs://${google_storage_bucket.parquet.name}/service_alerts/*"]

    hive_partitioning_options {
      mode                     = "CUSTOM"
      source_uri_prefix        = "gs://${google_storage_bucket.parquet.name}/service_alerts/{date:DATE}/{base64url:STRING}"
      require_partition_filter = false
    }
  }

  schema = jsonencode([
    { name = "source_file", type = "STRING", mode = "REQUIRED" },
    { name = "feed_url", type = "STRING", mode = "REQUIRED" },
    { name = "feed_timestamp", type = "INT64", mode = "NULLABLE" },
    { name = "fetch_timestamp", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "entity_id", type = "STRING", mode = "REQUIRED" },
    { name = "cause", type = "INT64", mode = "NULLABLE" },
    { name = "effect", type = "INT64", mode = "NULLABLE" },
    { name = "severity_level", type = "INT64", mode = "NULLABLE" },
    { name = "active_period_start", type = "INT64", mode = "NULLABLE" },
    { name = "active_period_end", type = "INT64", mode = "NULLABLE" },
    { name = "header_text", type = "STRING", mode = "NULLABLE" },
    { name = "description_text", type = "STRING", mode = "NULLABLE" },
    { name = "url", type = "STRING", mode = "NULLABLE" },
    { name = "agency_id", type = "STRING", mode = "NULLABLE" },
    { name = "route_id", type = "STRING", mode = "NULLABLE" },
    { name = "route_type", type = "INT64", mode = "NULLABLE" },
    { name = "stop_id", type = "STRING", mode = "NULLABLE" },
    { name = "trip_id", type = "STRING", mode = "NULLABLE" },
    { name = "trip_route_id", type = "STRING", mode = "NULLABLE" },
    { name = "trip_direction_id", type = "INT64", mode = "NULLABLE" },
  ])
}

# Feeds metadata - lookup table for agency/system/interval by base64url
resource "google_bigquery_table" "feeds" {
  dataset_id          = google_bigquery_dataset.gtfs_rt.dataset_id
  table_id            = "feeds"
  deletion_protection = false

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = true
    source_uris   = ["gs://${google_storage_bucket.parquet.name}/feeds.parquet"]
  }
}
