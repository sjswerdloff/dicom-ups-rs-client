"""Tests for SSL/TLS functionality of the UPS-RS client."""

import asyncio
import ssl
import time
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dicom_ups_rs_client.ups_rs_client import UPSRSClient


@pytest.fixture
def ssl_client() -> Generator[UPSRSClient, None, None]:
    """Fixture that provides a UPS-RS client with common setup and guaranteed cleanup."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="https://example.com/dicom-web", aetitle="TEST_AE")
        client.ws_url = "wss://example.com/dicom-web/subscribers/TEST_AE"

        yield client

        # Always ensure cleanup
        try:
            if hasattr(client, "running") and client.running:
                client.disconnect()
                # Wait a moment for cleanup to complete
                time.sleep(0.1)
        except Exception as e:
            print(f"Cleanup error (can be ignored): {e}")


def test_ssl_websocket_connection_with_disabled_verification() -> None:
    """Test WebSocket connection with SSL verification disabled."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="https://example.com/dicom-web", verify_ssl=False)
        client.ws_url = "wss://example.com/dicom-web/subscribers/TEST_AE"

        # Instead of patching the client's method, we'll patch websockets.connect
        # and capture what's passed to it
        with patch("websockets.connect") as mock_connect:
            # Set up a mock async context manager to be returned by connect
            mock_websocket = AsyncMock()
            mock_connect.return_value = mock_websocket

            # Make recv() never return to keep the websocket loop running
            mock_websocket.__aenter__.return_value.recv = AsyncMock(side_effect=lambda: asyncio.Future())

            # Start the WebSocket connection
            client.connect_websocket()

            # Wait a short time for the thread to start and make the connection
            # This is more reliable since we've mocked the websocket connection
            time.sleep(0.2)

            # Verify connect was called and check the SSL context
            assert mock_connect.called, "websockets.connect was not called"
            args, kwargs = mock_connect.call_args

            # Check if SSL context was passed and has the correct settings
            assert "ssl" in kwargs, "SSL context was not passed to websockets.connect"
            ssl_context = kwargs["ssl"]
            assert isinstance(ssl_context, ssl.SSLContext), "SSL parameter is not an SSLContext"
            assert ssl_context.check_hostname is False, "check_hostname should be False"
            assert ssl_context.verify_mode == ssl.CERT_NONE, "verify_mode should be CERT_NONE"

            # Clean up
            client.disconnect()


def test_ssl_websocket_connection_with_custom_ca_bundle() -> None:
    """Test WebSocket connection with custom CA bundle."""
    import asyncio

    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="https://example.com/dicom-web", verify_ssl="/path/to/ca-bundle.crt")
        client.ws_url = "wss://example.com/dicom-web/subscribers/TEST_AE"

        # Mock SSLContext to avoid file not found errors
        with patch("ssl.create_default_context") as mock_create_context:
            mock_ssl_context = MagicMock()
            mock_create_context.return_value = mock_ssl_context

            # Patch websockets.connect to avoid actual connection
            with patch("websockets.connect") as mock_connect:
                # Create a proper mock for the websocket
                mock_websocket = AsyncMock()
                # Create a future that never completes to keep the websocket loop running
                future = asyncio.Future()
                mock_websocket.__aenter__.return_value.recv = AsyncMock(side_effect=lambda: future)
                mock_connect.return_value = mock_websocket

                try:
                    # Start the WebSocket connection
                    client.connect_websocket()

                    # Short wait for the thread to start
                    time.sleep(0.2)

                    # Check that load_verify_locations was called with our CA bundle
                    mock_ssl_context.load_verify_locations.assert_called_once_with("/path/to/ca-bundle.crt")
                finally:
                    # Set running to False to prevent disconnect() from trying to close the connection
                    client.running = False
                    # Clean up
                    client.disconnect()


def test_ssl_websocket_connection_with_client_cert() -> None:
    """Test WebSocket connection with client certificate."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        cert_tuple = ("/path/to/client.crt", "/path/to/client.key")
        client = UPSRSClient(base_url="https://example.com/dicom-web", client_cert=cert_tuple)
        client.ws_url = "wss://example.com/dicom-web/subscribers/TEST_AE"

        # Mock SSLContext to avoid file not found errors
        with patch("ssl.create_default_context") as mock_create_context:
            mock_ssl_context = MagicMock()
            mock_create_context.return_value = mock_ssl_context

            # Patch websockets.connect to avoid actual connection
            with patch("websockets.connect") as mock_connect:
                mock_websocket = AsyncMock()
                mock_connect.return_value = mock_websocket

                # Start the WebSocket connection
                client.connect_websocket()

                # Short wait for the thread to start
                time.sleep(0.2)

                # Check that load_cert_chain was called with our cert tuple
                mock_ssl_context.load_cert_chain.assert_called_once_with("/path/to/client.crt", "/path/to/client.key")

                # Clean up
                client.disconnect()


def test_non_ssl_websocket_connection() -> None:
    """Test that non-SSL WebSocket connections work without SSL context."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(
            base_url="http://example.com/dicom-web",
            verify_ssl=True,  # Default value
        )
        client.ws_url = "ws://example.com/dicom-web/subscribers/TEST_AE"  # Non-SSL WebSocket

        with patch("websockets.connect") as mock_connect:
            mock_websocket = AsyncMock()
            mock_connect.return_value = mock_websocket

            # Start the WebSocket connection
            client.connect_websocket()

            # Short wait for the thread to start
            time.sleep(0.2)

            # Check that websockets.connect was called without ssl context
            assert mock_connect.called, "websockets.connect was not called"
            args, kwargs = mock_connect.call_args

            # For non-SSL connections, ssl parameter should not be present
            assert "ssl" not in kwargs, "SSL context was unexpectedly provided for non-SSL connection"

            # Clean up
            client.disconnect()


@pytest.mark.asyncio
async def test_ssl_context_configuration() -> None:
    """Test that SSL context is properly configured based on client settings."""
    # We need to import asyncio here for the mock side_effect
    import asyncio

    # Create a client with SSL settings
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(
            base_url="https://example.com/dicom-web",
            verify_ssl=False,
            client_cert=("/path/to/client.crt", "/path/to/client.key"),
        )

        # Set a WebSocket SSL URL
        client.ws_url = "wss://example.com/dicom-web/subscribers/TEST_AE"

        # Mock SSLContext to avoid file not found errors
        with patch("ssl.create_default_context") as mock_create_context:
            mock_ssl_context = MagicMock()
            mock_create_context.return_value = mock_ssl_context

            # Mock websockets module with special handling for the asyncio test
            with patch("websockets.connect") as mock_connect:
                # Create a mock for the websocket that never returns from recv()
                mock_websocket = AsyncMock()
                # Create a future that never completes
                future = asyncio.Future()
                mock_websocket.__aenter__.return_value.recv = AsyncMock(side_effect=lambda: future)
                mock_connect.return_value = mock_websocket

                try:
                    # Start WebSocket connection
                    client.connect_websocket()

                    # Allow time for the websocket thread to start and make the call
                    # This needs to be longer for the async test
                    await asyncio.sleep(0.5)

                    # First, verify connect was called
                    assert mock_connect.called, "websockets.connect was not called"

                    # Verify the SSL context was configured correctly
                    assert mock_ssl_context.check_hostname is False
                    assert mock_ssl_context.verify_mode == ssl.CERT_NONE

                    # Check client cert was loaded - the client will load the cert in the SSL context
                    mock_ssl_context.load_cert_chain.assert_called_once_with("/path/to/client.crt", "/path/to/client.key")
                finally:
                    # Set running to False to prevent disconnect() from trying to close the connection
                    client.running = False
                    # Clean up safely
                    client.disconnect()
                    # Allow cleanup to finish
                    await asyncio.sleep(0.1)


def test_websocket_url_conversion_https_to_wss() -> None:
    """Test that WebSocket URLs are converted from ws:// to wss:// when using HTTPS."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="https://example.com:9443/dicom-web", aetitle="TEST_AE")

        # Mock response with ws:// URL
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {}
        mock_response.headers = {"Content-Location": "ws://localhost:80/ws/subscribers/TEST_AE"}
        mock_session.request.return_value = mock_response

        # Subscribe and check WebSocket URL conversion
        success, response = client._send_subscription_request(
            "https://example.com:9443/dicom-web/workitems/subscribers/TEST_AE"
        )

        assert success is True
        assert client.ws_url == "wss://example.com:9443/dicom-web/ws/subscribers/TEST_AE"


def test_websocket_url_override() -> None:
    """Test that WebSocket URL override works correctly."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(
            base_url="https://example.com/dicom-web",
            aetitle="TEST_AE",
            websocket_url_override="wss://custom.example.com:8443/ws/subscribers/{aetitle}",
        )

        # Mock response
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {}
        mock_response.headers = {"Content-Location": "ws://localhost:80/ws/subscribers/TEST_AE"}
        mock_session.request.return_value = mock_response

        # Subscribe and check WebSocket URL override
        success, response = client._send_subscription_request("https://example.com/workitems/subscribers/TEST_AE")

        assert success is True
        assert client.ws_url == "wss://custom.example.com:8443/ws/subscribers/TEST_AE"
