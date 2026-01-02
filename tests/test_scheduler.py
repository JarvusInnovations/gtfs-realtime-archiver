"""Tests for scheduler module."""

import pytest

from gtfs_rt_archiver.models import FeedConfig
from gtfs_rt_archiver.scheduler import (
    FeedScheduler,
    create_and_start_scheduler,
    should_handle_feed,
)


def make_feed(feed_id: str) -> FeedConfig:
    """Create a minimal feed config for testing."""
    return FeedConfig(
        id=feed_id,
        name=f"Feed {feed_id}",
        url=f"https://example.com/{feed_id}.pb",
        feed_type="vehicle_positions",
        interval_seconds=30,
    )


class TestShouldHandleFeed:
    """Tests for should_handle_feed sharding function."""

    def test_single_shard_always_handles(self) -> None:
        """With total_shards=1, all feeds should be handled."""
        feed = make_feed("test-feed")
        assert should_handle_feed(feed, shard_index=0, total_shards=1) is True

    def test_zero_shards_always_handles(self) -> None:
        """With total_shards=0, all feeds should be handled (edge case)."""
        feed = make_feed("test-feed")
        assert should_handle_feed(feed, shard_index=0, total_shards=0) is True

    def test_deterministic_hashing(self) -> None:
        """Same feed should always map to same shard."""
        feed = make_feed("consistent-feed")
        results = [should_handle_feed(feed, shard_index=0, total_shards=3) for _ in range(10)]
        # All results should be identical
        assert all(r == results[0] for r in results)

    def test_each_feed_assigned_to_exactly_one_shard(self) -> None:
        """Each feed should be assigned to exactly one shard."""
        feed = make_feed("some-feed")
        total_shards = 5

        assigned_shards = [
            shard
            for shard in range(total_shards)
            if should_handle_feed(feed, shard_index=shard, total_shards=total_shards)
        ]

        assert len(assigned_shards) == 1

    def test_distribution_across_shards(self) -> None:
        """Feeds should be distributed across all shards."""
        total_shards = 4
        feeds = [make_feed(f"feed-{i}") for i in range(100)]

        shard_counts = [0] * total_shards
        for feed in feeds:
            for shard in range(total_shards):
                if should_handle_feed(feed, shard_index=shard, total_shards=total_shards):
                    shard_counts[shard] += 1

        # Each shard should have some feeds (not all in one shard)
        for count in shard_counts:
            assert count > 0, "Each shard should have at least one feed"

        # Total should equal number of feeds (each feed assigned once)
        assert sum(shard_counts) == len(feeds)

    def test_different_feed_ids_can_map_to_different_shards(self) -> None:
        """Different feed IDs should potentially map to different shards."""
        total_shards = 3
        feeds = [make_feed(f"feed-{i}") for i in range(20)]

        # Get which shard each feed maps to
        shard_assignments = set()
        for feed in feeds:
            for shard in range(total_shards):
                if should_handle_feed(feed, shard_index=shard, total_shards=total_shards):
                    shard_assignments.add(shard)

        # With 20 feeds and 3 shards, we should hit all shards
        assert len(shard_assignments) == total_shards


class TestFeedScheduler:
    """Tests for FeedScheduler class."""

    @pytest.fixture
    def feeds(self) -> list[FeedConfig]:
        """Create a list of test feeds."""
        return [make_feed(f"feed-{i}") for i in range(5)]

    @pytest.fixture
    def mock_fetch_job(self) -> list[FeedConfig]:
        """Create a mock fetch job that tracks calls."""
        calls: list[FeedConfig] = []

        async def fetch_job(feed: FeedConfig) -> None:
            calls.append(feed)

        # Return the calls list but attach the function
        calls.fetch_job = fetch_job  # type: ignore[attr-defined]
        return calls

    def test_initialization(self, feeds: list[FeedConfig]) -> None:
        """Test scheduler initialization."""

        async def dummy_job(feed: FeedConfig) -> None:
            pass

        scheduler = FeedScheduler(
            feeds=feeds,
            fetch_job=dummy_job,
            shard_index=0,
            total_shards=1,
        )

        assert scheduler.active_feeds == []  # Empty until started
        assert scheduler.is_running is False
        assert scheduler.get_job_count() == 0

    async def test_start_and_stop(self, feeds: list[FeedConfig]) -> None:
        """Test scheduler start and stop lifecycle."""
        calls: list[FeedConfig] = []

        async def fetch_job(feed: FeedConfig) -> None:
            calls.append(feed)

        scheduler = FeedScheduler(
            feeds=feeds,
            fetch_job=fetch_job,
            shard_index=0,
            total_shards=1,
        )

        # Start scheduler
        await scheduler.start()

        assert scheduler.is_running is True
        assert len(scheduler.active_feeds) == len(feeds)
        assert scheduler.get_job_count() == len(feeds)

        # Stop scheduler
        await scheduler.stop(wait=True)

        assert scheduler.is_running is False

    async def test_start_filters_feeds_by_shard(self, feeds: list[FeedConfig]) -> None:
        """Test that start() filters feeds based on shard assignment."""

        async def fetch_job(feed: FeedConfig) -> None:
            pass

        # Create scheduler for shard 0 of 2
        scheduler = FeedScheduler(
            feeds=feeds,
            fetch_job=fetch_job,
            shard_index=0,
            total_shards=2,
        )

        await scheduler.start()

        # Should have fewer feeds than total
        assert 0 < len(scheduler.active_feeds) < len(feeds)

        await scheduler.stop()

    async def test_stop_without_start(self) -> None:
        """Test that stop() is safe to call without start()."""

        async def fetch_job(feed: FeedConfig) -> None:
            pass

        scheduler = FeedScheduler(
            feeds=[],
            fetch_job=fetch_job,
        )

        # Should not raise
        await scheduler.stop()

    async def test_run_once_executes_job(self, feeds: list[FeedConfig]) -> None:
        """Test run_once executes the fetch job for a feed."""
        calls: list[FeedConfig] = []

        async def fetch_job(feed: FeedConfig) -> None:
            calls.append(feed)

        scheduler = FeedScheduler(
            feeds=feeds,
            fetch_job=fetch_job,
        )

        # run_once should work without starting scheduler
        await scheduler.run_once(feeds[0])

        assert len(calls) == 1
        assert calls[0] == feeds[0]

    async def test_is_running_reflects_scheduler_state(self, feeds: list[FeedConfig]) -> None:
        """Test is_running property reflects actual scheduler state."""

        async def fetch_job(feed: FeedConfig) -> None:
            pass

        scheduler = FeedScheduler(
            feeds=feeds,
            fetch_job=fetch_job,
        )

        assert scheduler.is_running is False

        await scheduler.start()
        assert scheduler.is_running is True

        await scheduler.stop(wait=True)
        assert scheduler.is_running is False


class TestCreateAndStartScheduler:
    """Tests for create_and_start_scheduler factory function."""

    async def test_creates_and_starts_scheduler(self) -> None:
        """Test that factory creates and starts a scheduler."""
        feeds = [make_feed("test-feed")]

        async def fetch_job(feed: FeedConfig) -> None:
            pass

        scheduler = await create_and_start_scheduler(
            feeds=feeds,
            fetch_job=fetch_job,
        )

        try:
            assert scheduler.is_running is True
            assert len(scheduler.active_feeds) == 1
        finally:
            await scheduler.stop()

    async def test_creates_with_sharding(self) -> None:
        """Test factory with sharding parameters."""
        feeds = [make_feed(f"feed-{i}") for i in range(10)]

        async def fetch_job(feed: FeedConfig) -> None:
            pass

        scheduler = await create_and_start_scheduler(
            feeds=feeds,
            fetch_job=fetch_job,
            shard_index=1,
            total_shards=3,
        )

        try:
            assert scheduler.is_running is True
            # Should have filtered to only feeds for shard 1
            assert 0 < len(scheduler.active_feeds) < len(feeds)
        finally:
            await scheduler.stop()
