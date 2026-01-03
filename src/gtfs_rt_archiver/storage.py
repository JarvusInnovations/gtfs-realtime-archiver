"""GCS storage writer with Hive-style partitioning."""

import asyncio
import base64
import json
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from gcloud.aio.storage import Storage

from gtfs_rt_archiver.fetcher import FetchResult
from gtfs_rt_archiver.models import FeedConfig

if TYPE_CHECKING:
    from aiohttp import ClientSession


def encode_url_to_base64url(url: str, query_params: dict[str, str] | None = None) -> str:
    """Encode a URL (with optional query params) to base64url format.

    Args:
        url: The base URL.
        query_params: Optional query parameters to append.

    Returns:
        Base64url-encoded string (URL-safe, no padding).
    """
    full_url = str(url)
    if query_params:
        full_url = f"{full_url}?{urlencode(query_params)}"

    # Encode to base64url (URL-safe alphabet, no padding)
    encoded = base64.urlsafe_b64encode(full_url.encode("utf-8")).decode("ascii")
    return encoded.rstrip("=")


def generate_storage_path(
    feed: FeedConfig,
    timestamp: datetime,
    extension: str = "pb",
) -> str:
    """Generate a Hive-style partitioned storage path.

    Path format:
    {feed_type}/date={YYYY-MM-DD}/hour={ISO8601}/base64url={encoded-url}/{timestamp}.{ext}

    Args:
        feed: Feed configuration.
        timestamp: Fetch timestamp for partitioning.
        extension: File extension (default: pb for protobuf).

    Returns:
        Full object path within the bucket.
    """
    # Format timestamp as ISO8601 for filename (with milliseconds)
    timestamp_str = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Date partition: YYYY-MM-DD
    date_str = timestamp.strftime("%Y-%m-%d")

    # Hour partition: ISO8601 truncated to hour boundary
    hour_str = timestamp.strftime("%Y-%m-%dT%H:00:00Z")

    # Base64url encode the full URL (including query params)
    url_encoded = encode_url_to_base64url(
        str(feed.url),
        feed.query_params if feed.query_params else None,
    )

    # Build path components
    parts = [
        feed.feed_type.value,
        f"date={date_str}",
        f"hour={hour_str}",
        f"base64url={url_encoded}",
        f"{timestamp_str}.{extension}",
    ]

    return "/".join(parts)


def generate_metadata(feed: FeedConfig, result: FetchResult) -> dict[str, object]:
    """Generate metadata dictionary for a fetch result.

    Args:
        feed: Feed configuration.
        result: Fetch result containing response metadata.

    Returns:
        Dictionary containing fetch metadata.
    """
    return {
        "feed_id": feed.id,
        "url": str(feed.url),
        "fetch_timestamp": result.fetch_timestamp.isoformat(),
        "duration_ms": result.duration_ms,
        "response_code": result.status_code,
        "content_length": result.content_length,
        "content_type": result.content_type,
        "headers": {
            k: v
            for k, v in result.headers.items()
            if k.lower() in ("etag", "last-modified", "content-type", "content-length")
        },
    }


class StorageWriter:
    """Async GCS storage writer for archiving GTFS-RT feeds."""

    def __init__(
        self,
        bucket: str,
        session: "ClientSession | None" = None,
        write_metadata: bool = True,
    ) -> None:
        """Initialize the storage writer.

        Args:
            bucket: GCS bucket name.
            session: Optional aiohttp ClientSession for connection reuse.
            write_metadata: Whether to write .meta sidecar files.
        """
        self.bucket = bucket
        self.write_metadata = write_metadata
        self._session = session
        self._storage: Storage | None = None
        self._lock = asyncio.Lock()

    async def _get_storage(self) -> Storage:
        """Get or create the GCS storage client.

        Uses a lock to prevent race conditions when multiple tasks
        call this method concurrently.
        """
        async with self._lock:
            if self._storage is None:
                self._storage = Storage(session=self._session)
            return self._storage

    async def write(self, feed: FeedConfig, result: FetchResult) -> str:
        """Write a fetch result to GCS.

        Args:
            feed: Feed configuration.
            result: Fetch result containing content and metadata.

        Returns:
            The GCS object path where the content was written.

        Raises:
            Exception: If the upload fails.
        """
        storage = await self._get_storage()

        # Generate paths
        content_path = generate_storage_path(
            feed=feed,
            timestamp=result.fetch_timestamp,
            extension="pb",
        )

        # Upload content
        await storage.upload(
            bucket=self.bucket,
            object_name=content_path,
            file_data=result.content,
            content_type="application/x-protobuf",
        )

        # Optionally upload metadata
        if self.write_metadata:
            metadata_path = generate_storage_path(
                feed=feed,
                timestamp=result.fetch_timestamp,
                extension="meta",
            )

            metadata = generate_metadata(feed, result)
            metadata_json = json.dumps(metadata, indent=2)

            await storage.upload(
                bucket=self.bucket,
                object_name=metadata_path,
                file_data=metadata_json.encode("utf-8"),
                content_type="application/json",
            )

        return content_path

    async def close(self) -> None:
        """Close the storage client and release resources."""
        if self._storage is not None:
            await self._storage.close()
            self._storage = None
