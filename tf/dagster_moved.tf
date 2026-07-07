# State moves for the dagster module's generalization of project-specific
# variables into generic bucket_grants / secret_grants maps.
#
# The module renamed its consumer-specific IAM resources to generic map-keyed
# ones; these moves keep the live bindings in place instead of destroy/create
# (which would open a brief permission gap for running sensors/jobs).
#
# Safe to delete once applied (moves are recorded in state) — and MUST be
# deleted when the module source switches to the Terraform Registry, since
# moved blocks may not reference resources across module packages.

moved {
  from = module.dagster.google_storage_bucket_iam_member.dagster_protobuf_reader
  to   = module.dagster.google_storage_bucket_iam_member.dagster_bucket["rt-protobuf"]
}

moved {
  from = module.dagster.google_storage_bucket_iam_member.run_worker_protobuf_reader["gtfsrt"]
  to   = module.dagster.google_storage_bucket_iam_member.run_worker_bucket["rt-protobuf:gtfsrt"]
}

moved {
  from = module.dagster.google_storage_bucket_iam_member.run_worker_parquet_writer["gtfsrt"]
  to   = module.dagster.google_storage_bucket_iam_member.run_worker_bucket["rt-parquet:gtfsrt"]
}

moved {
  from = module.dagster.google_secret_manager_secret_iam_member.run_worker_agencies_config["gtfsrt"]
  to   = module.dagster.google_secret_manager_secret_iam_member.run_worker_secret["agencies:gtfsrt"]
}
