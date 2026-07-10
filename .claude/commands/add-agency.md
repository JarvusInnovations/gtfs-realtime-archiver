# Add Transit Agency

Add a new transit agency to the GTFS-RT Archiver by searching for realtime and schedule feed sources, verifying URLs, and configuring API keys.

**Agency to add:** $ARGUMENTS

## Workflow

### Step 1: Search Local Mobility Database Catalogs

Search the local copy of the Mobility Database for GTFS-RT feeds and the agency's static GTFS schedule.

`.scratch/` is unversioned — if `.scratch/mobility-database-catalogs` doesn't exist yet, clone it first:

```bash
git clone --depth 1 https://github.com/MobilityData/mobility-database-catalogs .scratch/mobility-database-catalogs
```

```bash
# Search realtime feeds for agency by name (case-insensitive)
grep -ri "$ARGUMENTS" .scratch/mobility-database-catalogs/catalogs/sources/gtfs/realtime/

# Search static GTFS schedule feeds (data_type "gtfs")
grep -ri "$ARGUMENTS" .scratch/mobility-database-catalogs/catalogs/sources/gtfs/schedule/

# List all files matching agency name
ls .scratch/mobility-database-catalogs/catalogs/sources/gtfs/realtime/ \
   .scratch/mobility-database-catalogs/catalogs/sources/gtfs/schedule/ | grep -i "$ARGUMENTS"
```

Read matching catalog files to extract:

- `direct_download` URL for each feed type (vp, tu, sa)
- `authentication_type` (0=none, 1=api_key_in_query, 2=api_key_in_header)
- `authentication_info` URL for API key signup
- `api_key_parameter_name` for the auth header/query param

Schedule catalog entries (`data_type: "gtfs"` rather than `"gtfs-rt"`) provide the static GTFS zip — the candidate for `schedule_url`.

Prefer the first-party `direct_download` URL over the MobilityData-hosted `urls.latest` mirror so new versions come straight from the agency.

### Step 2: Search Transitland Atlas

Search the Transitland website for additional feed information:

1. Use dev-browser to navigate to `https://www.transit.land/feeds` and search for the agency
2. Look for realtime feed pages with URLs like `https://www.transit.land/feeds/f-{agency-slug}~rt`
3. Look for the static feed page (usually `https://www.transit.land/feeds/f-{agency-slug}` without `~rt`) for the GTFS schedule URL
4. Note feed URLs, authentication requirements, and any documentation links

**Transitland Atlas GitHub (alternative)**:

Search for the agency's DMFR file:

```
site:github.com/transitland/transitland-atlas {agency-name} gtfs realtime
```

Fetch raw JSON directly:

```
https://raw.githubusercontent.com/transitland/transitland-atlas/main/feeds/{domain}.dmfr.json
```

Look for `realtime_vehicle_positions`, `realtime_trip_updates`, and `realtime_alerts` URLs in the feed definition.

The same DMFR file usually also defines the static feed (spec `gtfs`): its `urls.static_current` is the agency's current GTFS schedule zip, a good candidate for `schedule_url`.

### Step 3: Find First-Party Documentation

Use dev-browser to explore the agency's developer portal:

1. Search for "{agency name} developer API" or "{agency name} GTFS realtime"
2. Navigate to the agency's developer portal
3. Look for:
   - API documentation
   - Developer registration/signup
   - API key requirements
   - Rate limits and terms of use
   - Static GTFS schedule zip download link (often on the same page as the realtime feeds)

**Common portal URL patterns:**

- `{agency}.org/developers`
- `{agency}.org/about/gtfs`
- `api.{agency}.org`
- `data.{agency}.org`

### Step 4: Verify Feed URLs

Test each feed URL with curl:

```bash
# For feeds without authentication
curl -s -o /dev/null -w "%{http_code}" "https://example.com/feed.pb"

# For feeds requiring header auth
curl -s -o /dev/null -w "%{http_code}" -H "X-Api-Key: YOUR_KEY" "https://example.com/feed.pb"

# For feeds requiring query param auth
curl -s -o /dev/null -w "%{http_code}" "https://example.com/feed.pb?api_key=YOUR_KEY"

# Verify protobuf content is returned (should show binary data)
curl -s "https://example.com/feed.pb" | head -c 100 | xxd | head -5
```

A valid GTFS-RT protobuf response should:

- Return HTTP 200
- Start with header bytes `0a0d 0a03 322e 30` (GTFS-RT version 2.0 header)

**Verify the schedule URL** (use `-L` — schedule URLs often redirect):

```bash
# Check status, following redirects
curl -sL -o /dev/null -w "%{http_code}" "https://example.com/gtfs.zip"

# Verify zip magic bytes (should start with "PK")
curl -sL "https://example.com/gtfs.zip" | head -c 4 | xxd

# Or download and confirm required GTFS files are present
curl -sL -o .scratch/gtfs.zip "https://example.com/gtfs.zip"
unzip -l .scratch/gtfs.zip | grep -E "stops.txt|trips.txt|stop_times.txt"
```

A valid GTFS schedule response should:

- Return HTTP 200 (after redirects)
- Start with zip magic bytes `PK` (`504b`)
- Contain required GTFS files (`stops.txt`, `trips.txt`, `stop_times.txt`, etc.)

If the schedule zip sits behind the same API gateway as the realtime feeds, apply the same auth flags as the GTFS-RT checks above.

The pipeline reuses the agency's `auth` config for schedule downloads, so the same key applies automatically — no separate auth setup is needed.

### Step 5: Set Up API Keys (if required)

If the feed requires authentication:

1. **Sign up for API key** using dev-browser on the agency's developer portal
2. **Store in GCP Secret Manager**:

   ```bash
   echo -n "YOUR_API_KEY" | gcloud secrets create {agency-id}-api-key \
     --project=gtfs-archiver \
     --replication-policy=automatic \
     --data-file=-
   ```

3. **Add IAM tag binding** for archiver access:

   ```bash
   gcloud resource-manager tags bindings create \
     --tag-value="gtfs-archiver/type/feed-key" \
     --parent="//secretmanager.googleapis.com/projects/284984087304/secrets/{agency-id}-api-key" \
     --location=global
   ```

4. **Verify tag binding**:

   ```bash
   gcloud resource-manager tags bindings list \
     --parent="//secretmanager.googleapis.com/projects/284984087304/secrets/{agency-id}-api-key" \
     --location=global
   ```

### Step 6: Add Agency Configuration

Add the agency to `agencies.yaml`:

```yaml
  - id: {agency-id}
    name: {Agency Name}
    # Static GTFS schedule — omit only if the agency truly publishes none
    schedule_url: https://example.com/gtfs.zip
    # Only include auth section if API key required
    auth:
      type: header  # or 'query'
      secret_name: {agency-id}-api-key
      key: X-Api-Key  # or the query parameter name
    feeds:
      - feed_type: vehicle_positions
        url: https://example.com/vehiclepositions.pb
      - feed_type: trip_updates
        url: https://example.com/tripupdates.pb
      - feed_type: service_alerts
        url: https://example.com/alerts.pb
```

**Schedule URL:**

- Technically optional, but expected — look for one in Steps 1-3 and omit only when the agency publishes no static GTFS
- Valid at agency level or system level; use `schedule_urls` (a list) when multiple zips apply
- Downloads reuse the agency's `auth` config, so a schedule behind the same API gateway as the RT feeds needs no extra setup
- Ingestion is automatic: a daily `gtfs_schedule_check` Dagster asset fingerprints every configured schedule URL and registers new ones as dynamic partitions
- `gtfs_schedule_ingest` then writes new versions as exploded parquet — no deploy steps beyond `/deploy-agencies`

**Auth types:**

- `header`: API key sent in HTTP header (more secure, preferred)
- `query`: API key sent as URL query parameter

**Feed types:**

- `vehicle_positions` (vp): Real-time vehicle locations
- `trip_updates` (tu): Arrival/departure predictions
- `service_alerts` (sa): Service disruption notices

### Step 7: Validate Configuration

```bash
# Validate YAML syntax
python -c "import yaml; yaml.safe_load(open('agencies.yaml'))"

# List all configured agencies
grep -E "^  - id:" agencies.yaml
```

## Reference

### Mobility Database Catalog Fields

```json
{
  "mdb_source_id": 1234,
  "data_type": "gtfs-rt",  // "gtfs" for static schedule entries
  "entity_type": ["vp"],  // vp, tu, sa (realtime only)
  "provider": "Agency Name",
  "urls": {
    "direct_download": "https://...",
    "authentication_type": 0,  // 0=none, 1=query, 2=header
    "authentication_info": "https://...",
    "api_key_parameter_name": "X-Api-Key"
  }
}
```

### Common Feed Providers

| Provider | Auth Type | Header/Param |
| ---------- | ----------- | -------------- |
| Azure API Management | header | `Ocp-Apim-Subscription-Key` |
| 511.org | query | `api_key` |
| Swiftly | header | `Authorization: Bearer {token}` |
| None (public) | - | - |

### Existing Agencies for Reference

See `agencies.yaml` for examples of:

- SEPTA (no auth, multiple systems)
- Metrolink (header auth)
- AC Transit (query auth)
- VTA via 511.org (shared API key)

## Notes

- Not all agencies provide all three feed types (VP, TU, SA)
- Some agencies have separate feeds per mode (bus, rail, etc.)
- Houston METRO notably lacks vehicle_positions
- Prefer header auth over query params (more secure, no URL logging)
