"""Main entry point for GTFS-RT Archiver."""

import asyncio
import signal
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
from aiohttp import ClientError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from gtfs_rt_archiver.config import (
    Settings,
    flatten_agencies,
    load_agencies_file,
    resolve_feed_secrets,
)
from gtfs_rt_archiver.fetcher import (
    NonRetryableError,
    create_http_client,
    fetch_feed,
)
from gtfs_rt_archiver.health import HealthServer
from gtfs_rt_archiver.logging import configure_logging, get_logger
from gtfs_rt_archiver.metrics import (
    record_fetch_attempt,
    record_fetch_error,
    record_fetch_success,
    record_upload_error,
    record_upload_success,
    set_active_feeds,
    set_scheduler_jobs,
)
from gtfs_rt_archiver.models import FeedConfig
from gtfs_rt_archiver.scheduler import FeedScheduler
from gtfs_rt_archiver.storage import StorageWriter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


async def create_fetch_job(
    http_client: httpx.AsyncClient,
    storage_writer: StorageWriter,
    semaphore: asyncio.Semaphore,
) -> "FetchJobCallable":
    """Create the fetch job function.

    Args:
        http_client: HTTP client for fetching feeds.
        storage_writer: Storage writer for uploading to GCS.
        semaphore: Semaphore to limit concurrent fetch operations.

    Returns:
        Async function to fetch and store a feed.
    """
    logger = get_logger(__name__)

    async def fetch_job(feed: FeedConfig) -> None:
        """Fetch a single feed and upload to storage."""
        feed_type = feed.feed_type.value
        agency = feed.agency_id

        # Acquire semaphore to limit concurrent operations
        async with semaphore:
            # Record attempt inside semaphore for accurate concurrency metrics
            record_fetch_attempt(feed.id, feed_type, agency)

            try:
                # Fetch the feed
                result = await fetch_feed(http_client, feed)

                # Record successful fetch
                record_fetch_success(
                    feed.id,
                    feed_type,
                    agency,
                    result.duration_ms / 1000.0,
                    result.content_length,
                )

                logger.info(
                    "fetch_success",
                    feed_id=feed.id,
                    feed_type=feed_type,
                    duration_ms=result.duration_ms,
                    content_length=result.content_length,
                )

                # Upload to storage with retry
                upload_start = datetime.now(UTC)
                try:
                    # Retry on network/IO errors only (not programming errors)
                    @retry(
                        stop=stop_after_attempt(3),
                        wait=wait_exponential(multiplier=1.0, max=10.0),
                        retry=retry_if_exception_type((ClientError, TimeoutError, ConnectionError)),
                        reraise=True,
                    )
                    async def upload_with_retry() -> str:
                        return await storage_writer.write(feed, result)

                    path = await upload_with_retry()
                    upload_duration = (datetime.now(UTC) - upload_start).total_seconds()

                    record_upload_success(feed.id, feed_type, agency, upload_duration)

                    logger.info(
                        "upload_success",
                        feed_id=feed.id,
                        path=path,
                        duration_seconds=upload_duration,
                    )

                except Exception as e:
                    record_upload_error(feed.id, feed_type, agency, type(e).__name__)
                    logger.error(
                        "upload_error",
                        feed_id=feed.id,
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )

            except NonRetryableError as e:
                error_type = f"http_{e.status_code}"
                record_fetch_error(feed.id, feed_type, agency, error_type)
                logger.warning(
                    "fetch_non_retryable",
                    feed_id=feed.id,
                    status_code=e.status_code,
                )

            except httpx.TimeoutException:
                record_fetch_error(feed.id, feed_type, agency, "timeout")
                logger.error("fetch_timeout", feed_id=feed.id)

            except httpx.TransportError as e:
                record_fetch_error(feed.id, feed_type, agency, "transport")
                logger.error(
                    "fetch_transport_error",
                    feed_id=feed.id,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

            except httpx.HTTPStatusError as e:
                error_type = f"http_{e.response.status_code}"
                record_fetch_error(feed.id, feed_type, agency, error_type)
                logger.error(
                    "fetch_http_error",
                    feed_id=feed.id,
                    status_code=e.response.status_code,
                )

            except Exception as e:
                record_fetch_error(feed.id, feed_type, agency, "unknown")
                logger.exception(
                    "fetch_unknown_error",
                    feed_id=feed.id,
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

    return fetch_job


# Type alias for the fetch job callable
type FetchJobCallable = Callable[[FeedConfig], Awaitable[None]]


async def run() -> None:
    """Run the GTFS-RT Archiver."""
    # Load settings from environment
    settings = Settings()  # type: ignore[call-arg]

    # Configure logging
    configure_logging(settings.log_level, settings.log_format)
    logger = get_logger(__name__)

    logger.info(
        "starting",
        config_path=str(settings.config_path),
        gcs_bucket=settings.gcs_bucket,
        shard_index=settings.shard_index,
        total_shards=settings.total_shards,
    )

    # Load and flatten agency configuration
    agencies_config = load_agencies_file(settings.config_path)
    feeds = flatten_agencies(agencies_config)

    logger.info(
        "loaded_agencies",
        agency_count=len(agencies_config.agencies),
        feed_count=len(feeds),
    )

    # Resolve authentication secrets (requires GCP_PROJECT_ID)
    feeds_with_auth = [f for f in feeds if f.auth is not None]
    if feeds_with_auth:
        if not settings.gcp_project_id:
            raise ValueError("GCP_PROJECT_ID is required when feeds have auth configured")
        logger.info("resolving_secrets", count=len(feeds_with_auth))
        await resolve_feed_secrets(feeds, settings.gcp_project_id)
        logger.info("secrets_resolved")

    # Create HTTP client
    http_client = create_http_client(settings.max_concurrent)

    # Create storage writer
    storage_writer = StorageWriter(
        bucket=settings.gcs_bucket,
    )

    # Create semaphore for concurrency limiting
    semaphore = asyncio.Semaphore(settings.max_concurrent)

    # Create fetch job
    fetch_job = await create_fetch_job(http_client, storage_writer, semaphore)

    # Create scheduler
    scheduler = FeedScheduler(
        feeds=feeds,
        fetch_job=fetch_job,
        shard_index=settings.shard_index,
        total_shards=settings.total_shards,
    )

    # Create health server
    health_server = HealthServer(
        port=settings.health_port,
        scheduler=scheduler,
    )

    # Set up shutdown handling
    shutdown_event = asyncio.Event()

    def handle_shutdown(signum: int, _frame: object) -> None:
        logger.info("shutdown_signal_received", signal=signum)
        shutdown_event.set()

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    try:
        # Start services
        await health_server.start()
        logger.info("health_server_started", port=settings.health_port)

        await scheduler.start()
        logger.info(
            "scheduler_started",
            active_feeds=len(scheduler.active_feeds),
        )

        # Update metrics
        set_active_feeds(len(scheduler.active_feeds))
        set_scheduler_jobs(scheduler.get_job_count())

        # Wait for shutdown signal
        await shutdown_event.wait()

    finally:
        # Graceful shutdown
        logger.info("shutting_down")

        await scheduler.stop(wait=True)
        logger.info("scheduler_stopped")

        await health_server.stop()
        logger.info("health_server_stopped")

        await storage_writer.close()
        await http_client.aclose()

        logger.info("shutdown_complete")


def main() -> None:
    """Entry point for the GTFS-RT Archiver."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
