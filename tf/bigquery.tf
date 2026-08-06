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
    # Added per #91; absent in pre-v0.9.3 partitions (read as NULL)
    { name = "wheelchair_accessible", type = "INT64", mode = "NULLABLE" },
    { name = "modified_trip_modifications_id", type = "STRING", mode = "NULLABLE" },
    { name = "modified_trip_affected_trip_id", type = "STRING", mode = "NULLABLE" },
    { name = "modified_trip_start_date", type = "STRING", mode = "NULLABLE" },
    { name = "modified_trip_start_time", type = "STRING", mode = "NULLABLE" },
    { name = "multi_carriage_details_json", type = "STRING", mode = "NULLABLE" },
    { name = "feed_version", type = "STRING", mode = "NULLABLE" },
    { name = "incrementality", type = "INT64", mode = "NULLABLE" },
    { name = "is_deleted", type = "BOOL", mode = "NULLABLE" },
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
    # Added per #91 (bindings 2.2.0 fields); absent in pre-v0.9.3 partitions,
    # which BigQuery reads as NULL
    { name = "license_plate", type = "STRING", mode = "NULLABLE" },
    { name = "arrival_scheduled_time", type = "INT64", mode = "NULLABLE" },
    { name = "departure_scheduled_time", type = "INT64", mode = "NULLABLE" },
    { name = "departure_occupancy_status", type = "INT64", mode = "NULLABLE" },
    { name = "assigned_stop_id", type = "STRING", mode = "NULLABLE" },
    { name = "stop_headsign", type = "STRING", mode = "NULLABLE" },
    { name = "pickup_type", type = "INT64", mode = "NULLABLE" },
    { name = "drop_off_type", type = "INT64", mode = "NULLABLE" },
    { name = "trip_properties_trip_id", type = "STRING", mode = "NULLABLE" },
    { name = "trip_properties_start_date", type = "STRING", mode = "NULLABLE" },
    { name = "trip_properties_start_time", type = "STRING", mode = "NULLABLE" },
    { name = "trip_properties_shape_id", type = "STRING", mode = "NULLABLE" },
    { name = "trip_properties_trip_headsign", type = "STRING", mode = "NULLABLE" },
    { name = "trip_properties_trip_short_name", type = "STRING", mode = "NULLABLE" },
    { name = "modified_trip_modifications_id", type = "STRING", mode = "NULLABLE" },
    { name = "modified_trip_affected_trip_id", type = "STRING", mode = "NULLABLE" },
    { name = "modified_trip_start_date", type = "STRING", mode = "NULLABLE" },
    { name = "modified_trip_start_time", type = "STRING", mode = "NULLABLE" },
    { name = "wheelchair_accessible", type = "INT64", mode = "NULLABLE" },
    { name = "feed_version", type = "STRING", mode = "NULLABLE" },
    { name = "incrementality", type = "INT64", mode = "NULLABLE" },
    { name = "is_deleted", type = "BOOL", mode = "NULLABLE" },
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
    # Added per #91 (cause_detail/effect_detail/image need bindings 2.2.0);
    # absent in pre-v0.9.3 partitions, which BigQuery reads as NULL
    { name = "direction_id", type = "INT64", mode = "NULLABLE" },
    { name = "cause_detail", type = "STRING", mode = "NULLABLE" },
    { name = "effect_detail", type = "STRING", mode = "NULLABLE" },
    { name = "tts_header_text", type = "STRING", mode = "NULLABLE" },
    { name = "tts_description_text", type = "STRING", mode = "NULLABLE" },
    { name = "image_url", type = "STRING", mode = "NULLABLE" },
    { name = "image_media_type", type = "STRING", mode = "NULLABLE" },
    { name = "image_alternative_text", type = "STRING", mode = "NULLABLE" },
    { name = "active_periods_json", type = "STRING", mode = "NULLABLE" },
    { name = "communication_periods_json", type = "STRING", mode = "NULLABLE" },
    { name = "impact_periods_json", type = "STRING", mode = "NULLABLE" },
    { name = "trip_start_time", type = "STRING", mode = "NULLABLE" },
    { name = "trip_start_date", type = "STRING", mode = "NULLABLE" },
    { name = "trip_schedule_relationship", type = "INT64", mode = "NULLABLE" },
    { name = "trip_modified_trip_modifications_id", type = "STRING", mode = "NULLABLE" },
    { name = "trip_modified_trip_affected_trip_id", type = "STRING", mode = "NULLABLE" },
    { name = "trip_modified_trip_start_date", type = "STRING", mode = "NULLABLE" },
    { name = "trip_modified_trip_start_time", type = "STRING", mode = "NULLABLE" },
    { name = "feed_version", type = "STRING", mode = "NULLABLE" },
    { name = "incrementality", type = "INT64", mode = "NULLABLE" },
    { name = "is_deleted", type = "BOOL", mode = "NULLABLE" },
  ])
}

# Trip Modifications - entity/message grain, repeated structures as JSON (#95)
# Extracted from trip_updates raw feeds in the same compaction pass;
# entity_id is the join target of {trip_updates,vehicle_positions}
# .modified_trip_modifications_id
resource "google_bigquery_table" "trip_modifications" {
  dataset_id                   = google_bigquery_dataset.gtfs_rt.dataset_id
  table_id                     = "trip_modifications"
  deletion_protection          = false
  ignore_auto_generated_schema = true

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = false
    source_uris   = ["gs://${google_storage_bucket.parquet.name}/trip_modifications/*"]

    hive_partitioning_options {
      mode                     = "CUSTOM"
      source_uri_prefix        = "gs://${google_storage_bucket.parquet.name}/trip_modifications/{date:DATE}/{base64url:STRING}"
      require_partition_filter = false
    }
  }

  schema = jsonencode([
    { name = "source_file", type = "STRING", mode = "REQUIRED" },
    { name = "feed_url", type = "STRING", mode = "REQUIRED" },
    { name = "feed_timestamp", type = "INT64", mode = "NULLABLE" },
    { name = "fetch_timestamp", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "entity_id", type = "STRING", mode = "REQUIRED" },
    { name = "selected_trips_json", type = "STRING", mode = "NULLABLE" },
    { name = "start_times_json", type = "STRING", mode = "NULLABLE" },
    { name = "service_dates_json", type = "STRING", mode = "NULLABLE" },
    { name = "modifications_json", type = "STRING", mode = "NULLABLE" },
    { name = "feed_version", type = "STRING", mode = "NULLABLE" },
    { name = "incrementality", type = "INT64", mode = "NULLABLE" },
    { name = "is_deleted", type = "BOOL", mode = "NULLABLE" },
  ])
}

# Shapes - detour replacement geometry entities (#96)
resource "google_bigquery_table" "shapes" {
  dataset_id                   = google_bigquery_dataset.gtfs_rt.dataset_id
  table_id                     = "shapes"
  deletion_protection          = false
  ignore_auto_generated_schema = true

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = false
    source_uris   = ["gs://${google_storage_bucket.parquet.name}/shapes/*"]

    hive_partitioning_options {
      mode                     = "CUSTOM"
      source_uri_prefix        = "gs://${google_storage_bucket.parquet.name}/shapes/{date:DATE}/{base64url:STRING}"
      require_partition_filter = false
    }
  }

  schema = jsonencode([
    { name = "source_file", type = "STRING", mode = "REQUIRED" },
    { name = "feed_url", type = "STRING", mode = "REQUIRED" },
    { name = "feed_timestamp", type = "INT64", mode = "NULLABLE" },
    { name = "fetch_timestamp", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "entity_id", type = "STRING", mode = "REQUIRED" },
    { name = "shape_id", type = "STRING", mode = "NULLABLE" },
    { name = "encoded_polyline", type = "STRING", mode = "NULLABLE" },
    { name = "feed_version", type = "STRING", mode = "NULLABLE" },
    { name = "incrementality", type = "INT64", mode = "NULLABLE" },
    { name = "is_deleted", type = "BOOL", mode = "NULLABLE" },
  ])
}

# Stops - ad-hoc/replacement stop definition entities (#97); TranslatedString
# fields captured full-fidelity as [{"text","language"},...] JSON (#98)
resource "google_bigquery_table" "stops" {
  dataset_id                   = google_bigquery_dataset.gtfs_rt.dataset_id
  table_id                     = "stops"
  deletion_protection          = false
  ignore_auto_generated_schema = true

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = false
    source_uris   = ["gs://${google_storage_bucket.parquet.name}/stops/*"]

    hive_partitioning_options {
      mode                     = "CUSTOM"
      source_uri_prefix        = "gs://${google_storage_bucket.parquet.name}/stops/{date:DATE}/{base64url:STRING}"
      require_partition_filter = false
    }
  }

  schema = jsonencode([
    { name = "source_file", type = "STRING", mode = "REQUIRED" },
    { name = "feed_url", type = "STRING", mode = "REQUIRED" },
    { name = "feed_timestamp", type = "INT64", mode = "NULLABLE" },
    { name = "fetch_timestamp", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "entity_id", type = "STRING", mode = "REQUIRED" },
    { name = "stop_id", type = "STRING", mode = "NULLABLE" },
    { name = "stop_code_translations_json", type = "STRING", mode = "NULLABLE" },
    { name = "stop_name_translations_json", type = "STRING", mode = "NULLABLE" },
    { name = "tts_stop_name_translations_json", type = "STRING", mode = "NULLABLE" },
    { name = "stop_desc_translations_json", type = "STRING", mode = "NULLABLE" },
    { name = "stop_lat", type = "FLOAT64", mode = "NULLABLE" },
    { name = "stop_lon", type = "FLOAT64", mode = "NULLABLE" },
    { name = "zone_id", type = "STRING", mode = "NULLABLE" },
    { name = "stop_url_translations_json", type = "STRING", mode = "NULLABLE" },
    { name = "parent_station", type = "STRING", mode = "NULLABLE" },
    { name = "stop_timezone", type = "STRING", mode = "NULLABLE" },
    { name = "wheelchair_boarding", type = "INT64", mode = "NULLABLE" },
    { name = "level_id", type = "STRING", mode = "NULLABLE" },
    { name = "platform_code_translations_json", type = "STRING", mode = "NULLABLE" },
    { name = "feed_version", type = "STRING", mode = "NULLABLE" },
    { name = "incrementality", type = "INT64", mode = "NULLABLE" },
    { name = "is_deleted", type = "BOOL", mode = "NULLABLE" },
  ])
}

# --- GTFS Schedule tables ---
# Schedule data is stored as exploded parquet per feed version.
# Each table uses autodetect since GTFS columns are all strings.
#
# One external table per GTFS file type. Single wildcard matches across
# both base64url and _feed_digest path levels. AUTO hive partitioning
# detects both partition keys from the prefix.

locals {
  schedule_tables = [
    "agency", "stops", "routes", "trips", "stop_times",
    "calendar", "calendar_dates", "shapes", "feed_info",
  ]
}

resource "google_bigquery_dataset" "gtfs_schedule" {
  dataset_id  = "gtfs_schedule"
  location    = "US"
  description = "GTFS Schedule data from archived feeds"

  access {
    role          = "OWNER"
    special_group = "projectOwners"
  }

  access {
    role          = "READER"
    user_by_email = google_service_account.metabase.email
  }
}

resource "google_bigquery_table" "schedule" {
  for_each = toset(local.schedule_tables)

  dataset_id          = google_bigquery_dataset.gtfs_schedule.dataset_id
  table_id            = each.value
  deletion_protection = false

  external_data_configuration {
    source_format = "PARQUET"
    autodetect    = true
    source_uris   = ["gs://${google_storage_bucket.parquet.name}/schedules/*/${each.value}.parquet"]

    hive_partitioning_options {
      mode                     = "AUTO"
      source_uri_prefix        = "gs://${google_storage_bucket.parquet.name}/schedules/"
      require_partition_filter = false
    }
  }
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
