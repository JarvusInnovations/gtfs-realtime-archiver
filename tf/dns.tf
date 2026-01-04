# DNS records for GCS bucket custom domains
# Zone is externally managed, referenced via data source

data "google_dns_managed_zone" "gtfsrt_io" {
  name    = var.dns_zone_name
  project = var.project_id
}

resource "google_dns_record_set" "protobuf_bucket" {
  name         = "protobuf.${data.google_dns_managed_zone.gtfsrt_io.dns_name}"
  managed_zone = data.google_dns_managed_zone.gtfsrt_io.name
  project      = var.project_id
  type         = "CNAME"
  ttl          = 300
  rrdatas      = ["c.storage.googleapis.com."]
}

resource "google_dns_record_set" "parquet_bucket" {
  name         = "parquet.${data.google_dns_managed_zone.gtfsrt_io.dns_name}"
  managed_zone = data.google_dns_managed_zone.gtfsrt_io.name
  project      = var.project_id
  type         = "CNAME"
  ttl          = 300
  rrdatas      = ["c.storage.googleapis.com."]
}
