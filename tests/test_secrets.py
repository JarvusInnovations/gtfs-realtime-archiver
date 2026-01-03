"""Tests for secrets module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gtfs_rt_archiver.models import AuthConfig, AuthType
from gtfs_rt_archiver.secrets import (
    SecretManagerError,
    clear_cache,
    get_secret,
    resolve_auth_config,
)


class TestGetSecret:
    """Tests for get_secret function."""

    @pytest.fixture(autouse=True)
    def clear_secret_cache(self) -> None:
        """Clear cache before each test."""
        clear_cache()

    @patch("gtfs_rt_archiver.secrets.secretmanager_v1.SecretManagerServiceAsyncClient")
    async def test_fetches_secret(self, mock_client_class: MagicMock) -> None:
        """Test successful secret fetch."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.payload.data = b"secret-value"
        mock_client.access_secret_version.return_value = mock_response

        result = await get_secret("my-project", "my-secret")

        assert result == "secret-value"
        mock_client.access_secret_version.assert_called_once()

    @patch("gtfs_rt_archiver.secrets.secretmanager_v1.SecretManagerServiceAsyncClient")
    async def test_caches_secret(self, mock_client_class: MagicMock) -> None:
        """Test that secrets are cached."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.payload.data = b"secret-value"
        mock_client.access_secret_version.return_value = mock_response

        # Fetch twice
        await get_secret("my-project", "my-secret")
        await get_secret("my-project", "my-secret")

        # Should only call API once
        assert mock_client.access_secret_version.call_count == 1

    @patch("gtfs_rt_archiver.secrets.secretmanager_v1.SecretManagerServiceAsyncClient")
    async def test_different_secrets_not_cached(self, mock_client_class: MagicMock) -> None:
        """Test that different secrets are fetched separately."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.payload.data = b"secret-value"
        mock_client.access_secret_version.return_value = mock_response

        # Fetch two different secrets
        await get_secret("my-project", "secret-1")
        await get_secret("my-project", "secret-2")

        # Should call API twice
        assert mock_client.access_secret_version.call_count == 2

    @patch("gtfs_rt_archiver.secrets.secretmanager_v1.SecretManagerServiceAsyncClient")
    async def test_raises_on_error(self, mock_client_class: MagicMock) -> None:
        """Test error handling."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client
        mock_client.access_secret_version.side_effect = Exception("API error")

        with pytest.raises(SecretManagerError) as exc_info:
            await get_secret("my-project", "my-secret")

        assert exc_info.value.secret_name == "my-secret"
        assert "API error" in str(exc_info.value)


class TestResolveAuthConfig:
    """Tests for resolve_auth_config function."""

    @pytest.fixture(autouse=True)
    def clear_secret_cache(self) -> None:
        """Clear cache before each test."""
        clear_cache()

    @patch("gtfs_rt_archiver.secrets.get_secret")
    async def test_resolves_bearer_token(self, mock_get_secret: AsyncMock) -> None:
        """Test secret value resolution with Bearer template."""
        mock_get_secret.return_value = "api-key-123"

        auth = AuthConfig(
            type=AuthType.HEADER,
            secret_name="my-secret",
            key="Authorization",
            value="Bearer ${SECRET}",
        )

        await resolve_auth_config(auth, "my-project")

        assert auth.resolved_value == "Bearer api-key-123"
        mock_get_secret.assert_called_once_with("my-project", "my-secret")

    @patch("gtfs_rt_archiver.secrets.get_secret")
    async def test_resolves_plain_secret(self, mock_get_secret: AsyncMock) -> None:
        """Test plain secret without template."""
        mock_get_secret.return_value = "api-key-123"

        auth = AuthConfig(
            type=AuthType.QUERY,
            secret_name="my-secret",
            key="api_key",
            value="${SECRET}",
        )

        await resolve_auth_config(auth, "my-project")

        assert auth.resolved_value == "api-key-123"

    @patch("gtfs_rt_archiver.secrets.get_secret")
    async def test_resolves_custom_template(self, mock_get_secret: AsyncMock) -> None:
        """Test custom template with secret."""
        mock_get_secret.return_value = "my-key"

        auth = AuthConfig(
            type=AuthType.HEADER,
            secret_name="my-secret",
            key="X-API-Key",
            value="Key: ${SECRET}!",
        )

        await resolve_auth_config(auth, "my-project")

        assert auth.resolved_value == "Key: my-key!"


class TestClearCache:
    """Tests for clear_cache function."""

    @patch("gtfs_rt_archiver.secrets.secretmanager_v1.SecretManagerServiceAsyncClient")
    async def test_clear_cache_forces_refetch(self, mock_client_class: MagicMock) -> None:
        """Test that clearing cache forces refetch."""
        mock_client = AsyncMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.payload.data = b"secret-value"
        mock_client.access_secret_version.return_value = mock_response

        # Fetch once
        await get_secret("my-project", "my-secret")

        # Clear cache
        clear_cache()

        # Fetch again - should call API again
        await get_secret("my-project", "my-secret")

        assert mock_client.access_secret_version.call_count == 2
