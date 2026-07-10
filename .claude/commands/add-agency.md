# Add Transit Agency

Add a new transit agency to the GTFS-RT Archiver by searching for realtime and schedule feed sources, verifying URLs, and configuring API keys.

**Agency to add:** $ARGUMENTS

## Ground Rules

These are hard requirements — do not skip them even if a step "looks obvious":

1. **Never add a URL you have not verified live in Step 4.** Catalog entries
   (Mobility Database, Transitland) are leads, not truth — they are frequently
   stale, and a dead or wrong URL in `agencies.yaml` fails silently until
   someone checks `/health/feeds`. Every URL in your final YAML must have a
   fresh HTTP 200 + content check behind it.
2. **Disambiguate the agency first.** Transit acronyms collide constantly
   (e.g. "WRTA" is both Worcester MA and Youngstown OH; "RTA" is Cleveland,
   Dayton, New Orleans, ...). Before searching, pin down the full legal name,
   city/county, and website. Confirm every source you use matches that
   location — the definitive check is `agency.txt` inside the schedule zip.
   If the id alone would be ambiguous, disambiguate it (e.g.
   `wrta-youngstown`).
3. **Work the steps in order** and confirm each step's expected outcome
   before moving on. If an expected outcome fails, stop and investigate —
   do not paper over it.

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

**Treat catalog results as leads only.** Check each entry's `status` field: `inactive` or `deprecated` entries usually point at dead or superseded URLs. Even entries with no status can be stale — agencies migrate realtime hosts without the catalog being updated (e.g. GCRTA's realtime entries still list a dead `gtfs.gcrta.org` host years after the move to Vontas Cloud). A catalog URL that fails verification in Step 4 means "keep looking" (Steps 2-3), not "give up" and not "add it anyway".

**Expected outcome of this step:** a list of candidate URLs per feed type (possibly empty) plus auth hints — nothing is confirmed yet.

### Step 2: Search Transitland Atlas

Search the Transitland website for additional feed information:

1. Browse or fetch `https://www.transit.land/feeds` and search for the agency
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

Explore the agency's developer portal (browser or web fetch):

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

**Fallback — vendor-conventional endpoints.** If Steps 1-3 yield no working
realtime URLs, the agency may still have live but undocumented GTFS-RT: most
agencies buy their realtime system from a handful of vendors, and each vendor
serves GTFS-RT at predictable paths on the agency's rider-facing tracker host.
Identify the vendor (from the tracker site's branding/URL structure, or the
`feed_info.txt` publisher inside the schedule zip), then probe the conventional
paths in the "Vendor Endpoint Conventions" reference table below. Rules for
probed endpoints:

- They must pass ALL Step 4 checks including the RT/schedule alignment check —
  a 200 response alone is not enough
- Flag them with a comment in `agencies.yaml` noting they are undocumented
  (the agency could rename or gate them without notice)

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

A valid GTFS-RT protobuf response must:

- Return HTTP 200
- Be a protobuf `FeedMessage`: the body starts with `0a` and a header-length
  byte (commonly `0d`, but it varies with which optional header fields are
  present), followed by `0a 03` and the version string. The reliable signature
  is `0a03 322e 30` ("2.0") — or `0a03 312e 30` ("1.0", which some vendors
  still declare and is also acceptable; the archiver stores raw bytes and does
  not enforce a version) — within the first few bytes. Anything else — HTML
  (`3c` / `<`), JSON (`7b` / `{`), empty body — is a failure.

A response that fails these checks disqualifies that URL. Do not add it; go
back to Steps 1-3 for a better candidate.

**Verify the schedule URL** (use `-L` — schedule URLs often redirect):

```bash
# Check status, following redirects
curl -sL -o /dev/null -w "%{http_code}" "https://example.com/gtfs.zip"

# Download once, then verify zip magic bytes (should start with "PK")
mkdir -p .scratch
curl -sL -o .scratch/gtfs.zip "https://example.com/gtfs.zip"
head -c 4 .scratch/gtfs.zip | xxd

# Confirm required GTFS files are present
unzip -l .scratch/gtfs.zip | grep -E "stops\.txt|trips\.txt|stop_times\.txt"
```

Keep `.scratch/gtfs.zip` — the alignment check below reuses it (cleanup comes
at the end of this step).

A valid GTFS schedule response should:

- Return HTTP 200 (after redirects)
- Start with zip magic bytes `PK` (`504b`)
- Contain required GTFS files (`stops.txt`, `trips.txt`, `stop_times.txt`, etc.)

If the schedule zip sits behind the same API gateway as the realtime feeds, apply the same auth flags as the GTFS-RT checks above.

The pipeline reuses the agency's `auth` config for schedule downloads, so the same key applies automatically — no separate auth setup is needed.

**Verify the RT and schedule feeds align.** A live RT feed paired with the
wrong (or badly stale) schedule is a silent data-quality bug: both URLs return
200, but the trip IDs never join. Check that trip and route IDs in the RT feed
actually resolve against the schedule zip:

```bash
# Download the trip updates feed (add auth flags if required);
# .scratch/gtfs.zip is still present from the schedule check above
curl -sL -o .scratch/tripupdates.pb "https://example.com/tripupdates.pb"

uv run python - <<'EOF'
import csv, io, zipfile
from google.transit import gtfs_realtime_pb2

feed = gtfs_realtime_pb2.FeedMessage()
feed.ParseFromString(open(".scratch/tripupdates.pb", "rb").read())
rt_trips = {e.trip_update.trip.trip_id for e in feed.entity if e.HasField("trip_update")} - {""}
rt_routes = {e.trip_update.trip.route_id for e in feed.entity if e.HasField("trip_update")} - {""}

# endswith() tolerates feeds nested in a subfolder inside the zip
zf = zipfile.ZipFile(".scratch/gtfs.zip")
def sched_ids(filename, column):
    name = next(n for n in zf.namelist() if n.endswith(filename))
    return {r[column] for r in csv.DictReader(io.TextIOWrapper(zf.open(name), encoding="utf-8-sig"))}

sched_trips = sched_ids("trips.txt", "trip_id")
sched_routes = sched_ids("routes.txt", "route_id")

print(f"RT trip IDs: {len(rt_trips)}; in schedule: "
      f"{len(rt_trips & sched_trips) / len(rt_trips):.0%}" if rt_trips else "RT trip IDs: 0 (empty feed)")
print(f"RT route IDs: {len(rt_routes)}; in schedule: "
      f"{len(rt_routes & sched_routes) / len(rt_routes):.0%}" if rt_routes else "RT route IDs: 0")
EOF
```

Interpreting the result:

- **≥90% of RT trip IDs found in the schedule** → aligned, proceed.
- **Low or zero match** → wrong schedule URL, a stale zip (look for a newer
  one), or a feed that uses non-schedule trip IDs (e.g. PATH's community feed
  uses dummy IDs). Do not proceed until you can explain the mismatch; if the
  feed legitimately uses unmatched IDs, record that as a comment in
  `agencies.yaml`.
- **RT trip IDs: 0** → the feed is likely just empty right now (overnight /
  no service). Re-run during the agency's service hours before concluding
  anything; as a secondary signal, run the vehicle-positions fallback below.

**Vehicle-positions fallback** — for VP-only agencies (no trip_updates feed)
or an empty trip_updates feed, compare VP route IDs against `routes.txt`
instead (same ≥90% bar):

```bash
curl -sL -o .scratch/vehiclepositions.pb "https://example.com/vehiclepositions.pb"

uv run python - <<'EOF'
import csv, io, zipfile
from google.transit import gtfs_realtime_pb2

feed = gtfs_realtime_pb2.FeedMessage()
feed.ParseFromString(open(".scratch/vehiclepositions.pb", "rb").read())
rt_routes = {e.vehicle.trip.route_id for e in feed.entity if e.HasField("vehicle")} - {""}

zf = zipfile.ZipFile(".scratch/gtfs.zip")
name = next(n for n in zf.namelist() if n.endswith("routes.txt"))
sched_routes = {r["route_id"] for r in csv.DictReader(io.TextIOWrapper(zf.open(name), encoding="utf-8-sig"))}

print(f"VP route IDs: {len(rt_routes)}; in schedule: "
      f"{len(rt_routes & sched_routes) / len(rt_routes):.0%}" if rt_routes else "VP route IDs: 0 (empty feed)")
EOF
```

Clean up when done:

```bash
rm -f .scratch/tripupdates.pb .scratch/vehiclepositions.pb .scratch/gtfs.zip
```

### Step 5: Set Up API Keys (if required)

If the feed requires authentication:

1. **Sign up for API key** via the agency's developer portal
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
- A sensor watching those check results then launches `gtfs_schedule_ingest`, which writes new versions as exploded parquet — no deploy steps beyond `/deploy-agencies`

**Auth types:**

- `header`: API key sent in HTTP header (more secure, preferred)
- `query`: API key sent as URL query parameter

**Feed types:**

- `vehicle_positions` (vp): Real-time vehicle locations
- `trip_updates` (tu): Arrival/departure predictions
- `service_alerts` (sa): Service disruption notices

### Step 7: Validate Configuration

Validate through the archiver's actual Pydantic models and flattening logic —
plain `yaml.safe_load` only catches syntax errors, not schema mistakes (wrong
field names, bad nesting, invalid feed types):

```bash
uv run python - <<'EOF'
import yaml
from gtfs_rt_archiver.models import AgenciesFileConfig
from gtfs_rt_archiver.config import flatten_agencies

parsed = AgenciesFileConfig.model_validate(yaml.safe_load(open("agencies.yaml")))
flat = flatten_agencies(parsed)
print(f"OK: {len(parsed.agencies)} agencies, {len(flat)} feeds")
for f in flat:
    print(f"  {f.id}: {f.interval_seconds}s auth={'yes' if f.auth else 'no'}")
EOF
```

This must print `OK` and list your new feed IDs (`{agency-id}-{feed-type}`)
with the expected intervals and auth. A Pydantic validation error means the
YAML structure is wrong — fix it before finishing.

### Step 8: Final Checklist

Confirm every line before declaring the agency added:

- [ ] Agency identity confirmed (right city/state; `agency.txt` matches)
- [ ] Every RT feed URL returned HTTP 200 with valid protobuf header bytes
      (fresh check, not a catalog claim)
- [ ] Schedule URL returned a valid zip containing `stops.txt`, `trips.txt`,
      `stop_times.txt` — or the omission of `schedule_url` is justified
- [ ] RT/schedule alignment check passed (or the mismatch is explained in a
      comment)
- [ ] Missing feed types (VP/TU/SA) are genuinely unavailable, not just
      undiscovered — and are noted
- [ ] If auth is required: secret created, tag binding added and verified
- [ ] Undocumented/probed endpoints are flagged with a comment
- [ ] Step 7 model validation prints `OK` and shows the new feed IDs

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

### Vendor Endpoint Conventions

For the Step 3 fallback: once you know which vendor runs the agency's
realtime tracker, probe these paths on the tracker host. Identify the vendor
from the tracker site itself, its URL structure, or the `feed_info.txt`
publisher inside the schedule zip.

| Vendor | GTFS-RT paths | Tell-tale signs |
| -------- | --------------- | ----------------- |
| Clever Devices (BusTime) | `/gtfsrt/vehicles`, `/gtfsrt/trips`, `/gtfsrt/alerts` | `/bustime/api/v3/*` JSON API, `createAccount.jsp`; e.g. Big Blue Bus, Madison, Mountain Line, Dayton RTA |
| GMV Syncromatics | `/gtfs-rt/vehiclepositions`, `/gtfs-rt/tripupdates`, `/gtfs-rt/alerts`; schedule at `/gtfs` | `feed_info.txt` publisher "GMV Syncromatics"; e.g. BCRTA (buztrakr.com) |
| Avail (InfoPoint/myStop) | `/infopoint/GTFS-Realtime.ashx?Type=VehiclePosition\|TripUpdate\|Alert`; schedule at `/InfoPoint/gtfs-zip.ashx` | `.ashx` handlers, InfoPoint branding; e.g. WRTA Youngstown, CityBus Lafayette |
| Vontas / Trapeze (TransitMaster) | `/TMGTFSRealTimeWebService/Vehicle/VehiclePositions.pb`, `/TripUpdate/TripUpdates.pb`, `/Alert/Alerts.pb` | `vontascloud.com` hosts; e.g. GCRTA |

GMV feeds often declare `gtfs_realtime_version: "1.0"` — see the Step 4 note.

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
- Some agencies are demand-response only (dial-a-ride/microtransit) and
  publish no GTFS at all — a legitimate "cannot add" outcome. Report it
  rather than forcing a config entry (e.g. CARTS, Columbiana County OH)
- Prefer stable schedule URLs (`latest`/handler endpoints) over date-stamped
  file paths (e.g. `/wp-content/uploads/2026/04/gtfs.zip`), which break on
  the next schedule update
