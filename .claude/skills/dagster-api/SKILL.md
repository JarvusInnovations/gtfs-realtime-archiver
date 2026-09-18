---
name: dagster-api
description: Query and control this project's production Dagster instance (gtfs-archiver) from the terminal — sensor status and tick errors, dynamic partition registration, backfills, run history — through its IAP-gated GraphQL API. Use this whenever you need to know what the pipeline is actually doing rather than what the code says it should do: an agency missing from gtfsrt.io or inventory.json, a feed archiving raw protobufs but producing no parquet, a sensor or schedule that may not be firing, a stuck or failed run, or any need to register a partition or launch a backfill. Reach for it before guessing at pipeline state from code, logs, or GCS — and before concluding a sensor is "switched off", since a sensor that fails every tick looks identical from outside.
---

# Dagster API access (gtfs-archiver)

The pipeline's real state lives in the Dagster instance, not in the repo. Code
tells you what *should* happen; this tells you what *did*. The gap between
those two is where this project's subtle bugs live — see the worked example at
the bottom.

## The one thing that is easy to get wrong

`dagster.gtfsrt.io` sits behind IAP using a **Google-managed OAuth client**.
Google does not support the usual impersonated-ID-token flow for those, so
this — the method a reasonable person tries first, and which older comments in
`tf/agent_access.tf` still describe — **always fails**:

```bash
# Returns: Invalid IAP credentials: Invalid JWT audience
gcloud auth print-identity-token \
  --impersonate-service-account=agent-graphql@... --audiences=<anything>
```

No audience value rescues it: not the custom domain, not the `run.app` URL,
not the browser flow's client id, not the `/projects/.../iap_web/...` resource
path. The failure is in the flow, not the argument.

What works is a **service-account self-signed JWT**, signed through the
`iamcredentials` API so no key file ever exists:

- Signer: `agent-graphql@gtfs-archiver.iam.gserviceaccount.com` — an
  impersonation target holding `roles/iap.httpsResourceAccessor` and nothing
  else. `chris@jarv.us` has `roles/iam.serviceAccountTokenCreator` on it. Both
  bindings live in `tf/agent_access.tf`.
- Audience: **`https://dagster.gtfsrt.io/*`** — the trailing `/*` is
  load-bearing. The bare URL and the trailing-slash form both fail with
  `Audience specified does not match requested endpoint`. A concrete path like
  `https://dagster.gtfsrt.io/graphql` also works.

`scripts/dagster-graphql` implements this. Prefer it over hand-rolling curl.

## Usage

```bash
.claude/skills/dagster-api/scripts/dagster-graphql <command>
```

| Command | What it answers |
| --- | --- |
| `sensors` | Which sensors exist, their status, and how the last tick ended |
| `ticks <sensor> [limit]` | Recent ticks with the first line of any error |
| `partitions <asset>` | Which partition keys are registered, per dimension |
| `add-partition <def> <key>` | Register one dynamic partition |
| `backfill <asset[,asset2]> <key>...` | Launch a backfill over partition keys |
| `runs [limit]` | Recent run history |
| `query '<graphql>' [vars]` | Anything the above doesn't cover |

Needs an authenticated `gcloud`, plus `curl` and `jq`. If signing fails with a
reauth prompt, ask the user to run `! gcloud auth login` — it cannot be done
non-interactively.

Defaults target production and can be overridden with `DAGSTER_HOST`,
`DAGSTER_IAP_AUDIENCE`, `DAGSTER_AGENT_SA`, `DAGSTER_CODE_LOCATION`,
`DAGSTER_REPOSITORY`.

## Reading pipeline state honestly

**A failing sensor and a stopped sensor are different, and the difference
matters.** `sensors` shows `RUNNING` for both a healthy sensor and one whose
every tick dies, because status reflects whether it is *enabled*, not whether
it *works*. Always pair status with the last tick outcome, and when that is
`FAILURE`, read `ticks` for the reason before forming a theory.

Tick errors are also layered. `feed_discovery_sensor` reports
`DagsterUserCodeUnreachableError: Unable to reach the user code server`, which
reads like a dead code server — but the underlying cause was the sensor
function exceeding its 60-second budget. Check the code server's actual health
before believing the surface message; the outer error is the daemon's
interpretation, not the diagnosis.

## Mutations touch production

`add-partition` and `backfill` change shared state that everyone's runs and
the public site read from. They are the right tools when a feed needs
rescuing, but they are not exploratory. Know which partition keys you intend
to create, and prefer registering a partition and letting the existing daily
schedule pick it up over launching a wide backfill, which costs real compute
per partition.

Several assets can share one non-subsettable `@multi_asset`, and Dagster
refuses a backfill that names only part of the group. `trip_updates_parquet`
is one of these: backfilling it also requires `trip_modifications_parquet`,
`shapes_parquet`, and `stops_parquet`. Pass them comma-separated; the error
message names whichever member is still missing, so it converges in a couple
of tries.

Partition keys are scheme-stripped feed URLs — `https://` dropped, `~` marking
`http://` — so `https://s3.amazonaws.com/kcm/vp.pb` becomes
`s3.amazonaws.com/kcm/vp.pb`. `url_to_partition_key()` in
`src/dagster_pipeline/defs/assets/compaction.py` is the authority.

Claude Code's sandbox classifies these as shared-resource modifications and
may block them; that prompt is the user's decision to make, so surface it
rather than routing around it.

## Worked example: the feed that archived but never appeared

King County Metro merged, deployed, and archived cleanly for nine days without
reaching gtfsrt.io. `/health/feeds` was green throughout, because the archiver
side genuinely was fine.

`sensors` showed `feed_discovery_sensor` `RUNNING` with `last=FAILURE`, and
`ticks` showed every tick timing out. `feed_discovery_sensor` is the only path
that registers a feed into the `*_feeds` dynamic partitions, while the daily
schedules iterate only *already-registered* ones — so established feeds kept
compacting and looked healthy while every newly-added feed was dropped
silently.

The generalizable lesson: when something is missing downstream, check the
sensor that was supposed to notice it before re-examining the thing that is
missing. Health endpoints report the layer they own, and a green archiver says
nothing about compaction.
