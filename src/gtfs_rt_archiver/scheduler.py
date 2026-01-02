"""APScheduler-based job scheduler for GTFS-RT feed fetching."""

import hashlib
from typing import TYPE_CHECKING

from apscheduler import AsyncScheduler, CoalescePolicy
from apscheduler.triggers.interval import IntervalTrigger

from gtfs_rt_archiver.models import FeedConfig

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    FetchJobFunc = Callable[[FeedConfig], Awaitable[None]]


def should_handle_feed(feed: FeedConfig, shard_index: int, total_shards: int) -> bool:
    """Determine if this instance should handle a given feed.

    Uses consistent hashing based on feed ID to distribute feeds across shards.
    Uses MD5 for deterministic hashing (Python's hash() is randomized).

    Args:
        feed: Feed configuration.
        shard_index: Index of this shard (0-based).
        total_shards: Total number of shards.

    Returns:
        True if this shard should handle the feed.
    """
    if total_shards <= 1:
        return True

    # Use MD5 for deterministic hashing across processes
    feed_hash = int(hashlib.md5(feed.id.encode()).hexdigest(), 16)
    return feed_hash % total_shards == shard_index


class FeedScheduler:
    """Scheduler for periodic GTFS-RT feed fetching."""

    def __init__(
        self,
        feeds: list[FeedConfig],
        fetch_job: "FetchJobFunc",
        shard_index: int = 0,
        total_shards: int = 1,
        misfire_grace_time: float = 5.0,
    ) -> None:
        """Initialize the feed scheduler.

        Args:
            feeds: List of feed configurations to schedule.
            fetch_job: Async function to call for each feed fetch.
            shard_index: Index of this shard for distributed deployments.
            total_shards: Total number of shards.
            misfire_grace_time: Seconds after scheduled time to still run job.
        """
        self._feeds = feeds
        self._fetch_job = fetch_job
        self._shard_index = shard_index
        self._total_shards = total_shards
        self._misfire_grace_time = misfire_grace_time
        self._scheduler: AsyncScheduler | None = None
        self._active_feeds: list[FeedConfig] = []

    @property
    def active_feeds(self) -> list[FeedConfig]:
        """Get the list of feeds this instance is handling."""
        return self._active_feeds

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is running."""
        return self._scheduler is not None and self._scheduler.state.name == "started"

    async def start(self) -> None:
        """Start the scheduler and register all feed jobs."""
        # Filter feeds for this shard
        self._active_feeds = [
            feed
            for feed in self._feeds
            if should_handle_feed(feed, self._shard_index, self._total_shards)
        ]

        # Create scheduler
        self._scheduler = AsyncScheduler()

        # Register jobs for each feed
        for feed in self._active_feeds:
            trigger = IntervalTrigger(seconds=feed.interval_seconds)

            # Create a closure to capture the feed
            async def job_func(f: FeedConfig = feed) -> None:
                await self._fetch_job(f)

            await self._scheduler.add_schedule(
                job_func,
                trigger=trigger,
                id=f"feed-{feed.id}",
                misfire_grace_time=self._misfire_grace_time,
                coalesce=CoalescePolicy.latest,  # Skip missed, run latest only
            )

        # Start the scheduler
        await self._scheduler.start_in_background()

    async def stop(self, wait: bool = True) -> None:
        """Stop the scheduler.

        Args:
            wait: If True, wait for running jobs to complete.
        """
        if self._scheduler is not None:
            await self._scheduler.stop()
            if wait:
                await self._scheduler.wait_until_stopped()
            self._scheduler = None

    def get_job_count(self) -> int:
        """Get the number of scheduled jobs."""
        return len(self._active_feeds)

    async def run_once(self, feed: FeedConfig) -> None:
        """Run a single fetch job immediately (for testing/manual triggers).

        Args:
            feed: Feed to fetch.
        """
        await self._fetch_job(feed)


async def create_and_start_scheduler(
    feeds: list[FeedConfig],
    fetch_job: "FetchJobFunc",
    shard_index: int = 0,
    total_shards: int = 1,
) -> FeedScheduler:
    """Create and start a feed scheduler.

    Convenience function for creating and starting a scheduler in one step.

    Args:
        feeds: List of feed configurations.
        fetch_job: Async function to call for each feed fetch.
        shard_index: Index of this shard for distributed deployments.
        total_shards: Total number of shards.

    Returns:
        Started FeedScheduler instance.
    """
    scheduler = FeedScheduler(
        feeds=feeds,
        fetch_job=fetch_job,
        shard_index=shard_index,
        total_shards=total_shards,
    )
    await scheduler.start()
    return scheduler
