# Deploy Agencies Configuration

Deploy changes from local `agencies.yaml` to the production GTFS-RT archiver service.

**Optional filter:** $ARGUMENTS

## Workflow

### Step 1: Fetch Currently Deployed Configuration

```bash
gcloud secrets versions access latest \
  --secret=agencies-config \
  --project=gtfs-archiver \
  > .scratch/deployed-agencies.yaml
```

### Step 2: Compare Deployed vs Local

Generate a diff to identify changes:

```bash
diff -u .scratch/deployed-agencies.yaml agencies.yaml || true
```

**Analyze the differences:**

- **Lines starting with `-`**: Present in deployed, missing from local
- **Lines starting with `+`**: Present in local, missing from deployed
- **No diff output**: Configurations are identical

To compare agency lists specifically:

```bash
# List agencies in deployed config
grep -E "^  - id:" .scratch/deployed-agencies.yaml | sort

# List agencies in local config
grep -E "^  - id:" agencies.yaml | sort

# Find agencies only in deployed (potential loss)
comm -23 \
  <(grep -E "^  - id:" .scratch/deployed-agencies.yaml | sort) \
  <(grep -E "^  - id:" agencies.yaml | sort)

# Find agencies only in local (new additions)
comm -13 \
  <(grep -E "^  - id:" .scratch/deployed-agencies.yaml | sort) \
  <(grep -E "^  - id:" agencies.yaml | sort)
```

### Step 3: Handle Conflicts

If the deployed configuration has changes not present in local (e.g., another team member deployed), you **must** prompt the user to choose:

**Option A - Merge:** Incorporate deployed changes into local `agencies.yaml`, then deploy the combined result.

**Option B - Overwrite:** Deploy local version as-is, discarding any deployed-only changes.

**Option C - Abort:** Cancel deployment so the user can manually reconcile differences.

**Important:** Always show the diff and ask the user before proceeding if there are any differences where deployed has content that local does not.

### Step 4: Update Local (if merging)

If merging, update the local `agencies.yaml` to include changes from both versions. Ensure:

- All agencies from both configs are present
- Feed configurations are properly merged
- Auth configurations are preserved
- No duplicate agency IDs

Validate the merged YAML:

```bash
python -c "import yaml; yaml.safe_load(open('agencies.yaml'))"
```

### Step 5: Update Secret in GCP

Create a new secret version with the local configuration:

```bash
gcloud secrets versions add agencies-config \
  --project=gtfs-archiver \
  --data-file=agencies.yaml
```

Verify the new version was created:

```bash
gcloud secrets versions list agencies-config \
  --project=gtfs-archiver \
  --limit=3
```

### Step 6: Force Cloud Run Restart

Cloud Run caches secret values per revision. To load the new configuration, force a new revision by updating an environment variable:

```bash
gcloud run services update gtfs-rt-archiver \
  --region=us-central1 \
  --project=gtfs-archiver \
  --update-env-vars="DEPLOY_TIMESTAMP=$(date +%s)"
```

### Step 7: Verify Deployment

Wait for the new revision to become healthy:

```bash
# Check service status
gcloud run services describe gtfs-rt-archiver \
  --region=us-central1 \
  --project=gtfs-archiver \
  --format="value(status.conditions[0].status)"

# Check latest revision
gcloud run services describe gtfs-rt-archiver \
  --region=us-central1 \
  --project=gtfs-archiver \
  --format="value(status.latestReadyRevision)"

# Verify health endpoint
curl -s https://archiver.gtfsrt.io/health
```

Expected health response: `{"status":"healthy"}`

### Step 8: Cleanup

Remove the temporary deployed config file:

```bash
rm -f .scratch/deployed-agencies.yaml
```

## Quick Deploy (No Conflicts)

If you're confident there are no conflicts (e.g., you just added an agency):

```bash
# 1. Update secret
gcloud secrets versions add agencies-config \
  --project=gtfs-archiver \
  --data-file=agencies.yaml

# 2. Force restart
gcloud run services update gtfs-rt-archiver \
  --region=us-central1 \
  --project=gtfs-archiver \
  --update-env-vars="DEPLOY_TIMESTAMP=$(date +%s)"

# 3. Verify
curl -s https://archiver.gtfsrt.io/health
```

## Rollback

To rollback to a previous configuration version:

```bash
# List recent versions
gcloud secrets versions list agencies-config \
  --project=gtfs-archiver \
  --limit=5

# Access a specific version (e.g., version 3)
gcloud secrets versions access 3 \
  --secret=agencies-config \
  --project=gtfs-archiver

# Disable current version and enable previous
gcloud secrets versions disable CURRENT_VERSION \
  --secret=agencies-config \
  --project=gtfs-archiver

# Force restart to load the previous version
gcloud run services update gtfs-rt-archiver \
  --region=us-central1 \
  --project=gtfs-archiver \
  --update-env-vars="DEPLOY_TIMESTAMP=$(date +%s)"
```

## Reference

| Setting | Value |
|---------|-------|
| Secret ID | `agencies-config` |
| Project | `gtfs-archiver` |
| Service | `gtfs-rt-archiver` |
| Region | `us-central1` |
| Health URL | `https://archiver.gtfsrt.io/health` |
| Local file | `agencies.yaml` |
