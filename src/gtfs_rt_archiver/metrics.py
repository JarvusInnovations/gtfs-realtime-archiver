"""Prometheus metrics for GTFS-RT Archiver."""

import time

from prometheus_client import Counter, Gauge, Histogram

# In-memory last-success timestamps per feed (for /health/feeds endpoint)
_last_success_timestamps: dict[str, float] = {}

# Common histogram buckets for timing metrics (matches production v3)
TIMING_BUCKETS = [0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0, 15.0, 20.0, 25.0, 30.0]

# Fetch metrics
fetch_total = Counter(
    "gtfs_rt_fetch_total",
    "Total fetch attempts",
    ["feed_id", "feed_type", "agency"],
)

fetch_success = Counter(
    "gtfs_rt_fetch_success_total",
    "Successful fetches",
    ["feed_id", "feed_type", "agency"],
)

fetch_errors = Counter(
    "gtfs_rt_fetch_errors_total",
    "Failed fetches",
    ["feed_id", "feed_type", "agency", "error_type"],
)

fetch_duration = Histogram(
    "gtfs_rt_fetch_duration_seconds",
    "Time to fetch feed",
    ["feed_id", "feed_type", "agency"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
    unit="seconds",
)

fetch_bytes = Histogram(
    "gtfs_rt_fetch_bytes",
    "Response size in bytes",
    ["feed_id", "feed_type", "agency"],
    buckets=[1000, 10000, 50000, 100000, 500000, 1000000],
)

# Upload metrics
upload_success = Counter(
    "gtfs_rt_upload_success_total",
    "Successful GCS uploads",
    ["feed_id", "feed_type", "agency"],
)

upload_errors = Counter(
    "gtfs_rt_upload_errors_total",
    "Failed GCS uploads",
    ["feed_id", "feed_type", "agency", "error_type"],
)

upload_duration = Histogram(
    "gtfs_rt_upload_duration_seconds",
    "Time to upload to GCS",
    ["feed_id", "feed_type", "agency"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
    unit="seconds",
)

# System metrics
active_feeds = Gauge(
    "gtfs_rt_active_feeds",
    "Number of feeds being processed by this instance",
)

scheduler_jobs = Gauge(
    "gtfs_rt_scheduler_jobs",
    "Number of scheduled jobs",
)

last_fetch_timestamp = Gauge(
    "gtfs_rt_last_fetch_timestamp",
    "Unix timestamp of last fetch attempt",
    ["feed_id"],
)

# Delay/slippage metrics (3 separate measurements for diagnosing bottlenecks)
scheduler_delay = Histogram(
    "gtfs_rt_scheduler_delay_seconds",
    "Time from scheduled tick to job dispatch (APScheduler overhead)",
    ["feed_id", "feed_type", "agency"],
    buckets=TIMING_BUCKETS,
    unit="seconds",
)

queue_delay = Histogram(
    "gtfs_rt_queue_delay_seconds",
    "Time waiting for concurrency semaphore",
    ["feed_id", "feed_type", "agency"],
    buckets=TIMING_BUCKETS,
    unit="seconds",
)

total_delay = Histogram(
    "gtfs_rt_total_delay_seconds",
    "Total time from scheduled tick to job start (scheduler + queue)",
    ["feed_id", "feed_type", "agency"],
    buckets=TIMING_BUCKETS,
    unit="seconds",
)

# End-to-end processing time
processing_time = Histogram(
    "gtfs_rt_processing_time_seconds",
    "Total time to fetch and upload (end-to-end)",
    ["feed_id", "feed_type", "agency"],
    buckets=TIMING_BUCKETS,
    unit="seconds",
)

# Bytes counter with content_type label (cumulative throughput tracking)
processed_bytes = Counter(
    "gtfs_rt_processed_bytes_total",
    "Total bytes processed (downloaded and uploaded)",
    ["feed_id", "feed_type", "agency", "content_type"],
)

# Upload attempt counter (for symmetry with fetch_total)
upload_total = Counter(
    "gtfs_rt_upload_total",
    "Total upload attempts",
    ["feed_id", "feed_type", "agency"],
)


def get_labels(feed_id: str, feed_type: str, agency: str | None) -> dict[str, str]:
    """Get standard labels for a feed.

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed (vehicle_positions, trip_updates, service_alerts).
        agency: Agency identifier or None.

    Returns:
        Dictionary of label names to values.
    """
    return {
        "feed_id": feed_id,
        "feed_type": feed_type,
        "agency": agency or "unknown",
    }


def record_fetch_attempt(feed_id: str, feed_type: str, agency: str | None) -> None:
    """Record a fetch attempt.

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
    """
    labels = get_labels(feed_id, feed_type, agency)
    fetch_total.labels(**labels).inc()


def record_fetch_success(
    feed_id: str,
    feed_type: str,
    agency: str | None,
    duration_seconds: float,
    bytes_received: int,
) -> None:
    """Record a successful fetch.

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
        duration_seconds: Time taken to fetch in seconds.
        bytes_received: Size of response in bytes.
    """
    labels = get_labels(feed_id, feed_type, agency)
    fetch_success.labels(**labels).inc()
    fetch_duration.labels(**labels).observe(duration_seconds)
    fetch_bytes.labels(**labels).observe(bytes_received)
    last_fetch_timestamp.labels(feed_id=feed_id).set_to_current_time()


def record_fetch_error(
    feed_id: str,
    feed_type: str,
    agency: str | None,
    error_type: str,
) -> None:
    """Record a failed fetch.

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
        error_type: Type of error (e.g., "timeout", "connection", "http_4xx").
    """
    labels = get_labels(feed_id, feed_type, agency)
    fetch_errors.labels(**labels, error_type=error_type).inc()
    last_fetch_timestamp.labels(feed_id=feed_id).set_to_current_time()


def record_upload_success(
    feed_id: str,
    feed_type: str,
    agency: str | None,
    duration_seconds: float,
) -> None:
    """Record a successful upload.

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
        duration_seconds: Time taken to upload in seconds.
    """
    labels = get_labels(feed_id, feed_type, agency)
    upload_success.labels(**labels).inc()
    upload_duration.labels(**labels).observe(duration_seconds)


def record_upload_error(
    feed_id: str,
    feed_type: str,
    agency: str | None,
    error_type: str,
) -> None:
    """Record a failed upload.

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
        error_type: Type of error (e.g., "TimeoutError", "ConnectionError").
    """
    labels = get_labels(feed_id, feed_type, agency)
    upload_errors.labels(**labels, error_type=error_type).inc()


def set_active_feeds(count: int) -> None:
    """Set the number of active feeds.

    Args:
        count: Number of feeds being processed.
    """
    active_feeds.set(count)


def set_scheduler_jobs(count: int) -> None:
    """Set the number of scheduled jobs.

    Args:
        count: Number of scheduled jobs.
    """
    scheduler_jobs.set(count)


def record_scheduler_delay(
    feed_id: str,
    feed_type: str,
    agency: str | None,
    delay_seconds: float,
) -> None:
    """Record time from scheduled tick to job dispatch.

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
        delay_seconds: Delay in seconds.
    """
    labels = get_labels(feed_id, feed_type, agency)
    scheduler_delay.labels(**labels).observe(delay_seconds)


def record_queue_delay(
    feed_id: str,
    feed_type: str,
    agency: str | None,
    delay_seconds: float,
) -> None:
    """Record time waiting for concurrency semaphore.

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
        delay_seconds: Delay in seconds.
    """
    labels = get_labels(feed_id, feed_type, agency)
    queue_delay.labels(**labels).observe(delay_seconds)


def record_total_delay(
    feed_id: str,
    feed_type: str,
    agency: str | None,
    delay_seconds: float,
) -> None:
    """Record total delay from scheduled tick to job start.

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
        delay_seconds: Delay in seconds.
    """
    labels = get_labels(feed_id, feed_type, agency)
    total_delay.labels(**labels).observe(delay_seconds)


def record_processing_time(
    feed_id: str,
    feed_type: str,
    agency: str | None,
    duration_seconds: float,
) -> None:
    """Record end-to-end processing time (fetch + upload).

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
        duration_seconds: Duration in seconds.
    """
    labels = get_labels(feed_id, feed_type, agency)
    processing_time.labels(**labels).observe(duration_seconds)


def record_processed_bytes(
    feed_id: str,
    feed_type: str,
    agency: str | None,
    content_type: str,
    byte_count: int,
) -> None:
    """Record bytes processed (cumulative counter).

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
        content_type: Content-Type header value.
        byte_count: Number of bytes processed.
    """
    labels = get_labels(feed_id, feed_type, agency)
    processed_bytes.labels(**labels, content_type=content_type).inc(byte_count)


def record_feed_success(feed_id: str) -> None:
    """Record a successful fetch+upload cycle for a feed.

    Updates the in-memory timestamp used by the /health/feeds endpoint.

    Args:
        feed_id: Feed identifier.
    """
    _last_success_timestamps[feed_id] = time.time()


def get_last_success_timestamp(feed_id: str) -> float | None:
    """Get the timestamp of the last successful fetch+upload for a feed.

    Args:
        feed_id: Feed identifier.

    Returns:
        Unix timestamp of last success, or None if never succeeded.
    """
    return _last_success_timestamps.get(feed_id)


def record_upload_attempt(feed_id: str, feed_type: str, agency: str | None) -> None:
    """Record an upload attempt.

    Args:
        feed_id: Feed identifier.
        feed_type: Type of feed.
        agency: Agency identifier or None.
    """
    labels = get_labels(feed_id, feed_type, agency)
    upload_total.labels(**labels).inc()
