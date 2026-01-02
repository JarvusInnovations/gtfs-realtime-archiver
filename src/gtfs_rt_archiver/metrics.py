"""Prometheus metrics for GTFS-RT Archiver."""

from prometheus_client import Counter, Gauge, Histogram

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
