# Create database in the provided Cloud SQL instance
resource "google_sql_database" "dagster" {
  name     = var.db_name
  instance = local.cloud_sql_instance_name
  project  = var.project_id
}

# Create database user
resource "google_sql_user" "dagster" {
  name     = var.db_user
  instance = local.cloud_sql_instance_name
  password = random_password.db_password.result
  project  = var.project_id

  deletion_policy = "ABANDON"
}

# Local to extract instance name from connection name
locals {
  # Connection name format: project:region:instance
  cloud_sql_instance_name = element(split(":", var.cloud_sql_connection_name), 2)
}
