# Tag key and value for feed API key secrets
# Secrets with this tag will be accessible by the archiver service account

resource "google_tags_tag_key" "secret_type" {
  parent      = "projects/${var.project_id}"
  short_name  = "type"
  description = "Type of secret for IAM access control"
}

resource "google_tags_tag_value" "feed_key" {
  parent      = google_tags_tag_key.secret_type.id
  short_name  = "feed-key"
  description = "Feed API key secrets"
}
