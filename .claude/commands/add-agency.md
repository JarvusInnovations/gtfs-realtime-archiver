# Add Transit Agency

Add a new transit agency to the GTFS-RT Archiver by searching for feed sources, verifying URLs, and configuring API keys.

**Agency to add:** $ARGUMENTS

## Workflow

### Step 1: Search Local Mobility Database Catalogs

Search the local copy of the Mobility Database for GTFS-RT feeds:

```bash
# Search for agency by name (case-insensitive)
grep -ri "$ARGUMENTS" .scratch/mobility-database-catalogs/catalogs/sources/gtfs/realtime/

# List all files matching agency name
ls .scratch/mobility-database-catalogs/catalogs/sources/gtfs/realtime/ | grep -i "$ARGUMENTS"
```

Read matching catalog files to extract:

- `direct_download` URL for each feed type (vp, tu, sa)
- `authentication_type` (0=none, 1=api_key_in_query, 2=api_key_in_header)
- `authentication_info` URL for API key signup
- `api_key_parameter_name` for the auth header/query param

### Step 2: Search Transitland Atlas

Search the Transitland website for additional feed information:

1. Use dev-browser to navigate to `https://www.transit.land/feeds` and search for the agency
2. Look for realtime feed pages with URLs like `https://www.transit.land/feeds/f-{agency-slug}~rt`
3. Note feed URLs, authentication requirements, and any documentation links

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

### Step 3: Find First-Party Documentation

Use dev-browser to explore the agency's developer portal:

1. Search for "{agency name} developer API" or "{agency name} GTFS realtime"
2. Navigate to the agency's developer portal
3. Look for:
   - API documentation
   - Developer registration/signup
   - API key requirements
   - Rate limits and terms of use

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
    # Optional: schedule_url for GTFS schedule reference
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
  "data_type": "gtfs-rt",
  "entity_type": ["vp"],  // vp, tu, sa
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
|----------|-----------|--------------|
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
