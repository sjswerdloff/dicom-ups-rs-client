"""Tests for SSL/TLS functionality of the UPS-RS client."""

import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dicom_ups_rs_client.ups_rs_client import UPSRSClient


def test_ssl_websocket_connection_with_disabled_verification() -> None:
    """Test WebSocket connection with SSL verification disabled."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="https://example.com/dicom-web", verify_ssl=False)
        client.ws_url = "wss://example.com/dicom-web/subscribers/TEST_AE"

        with patch("websockets.connect") as mock_connect:
            # Start the WebSocket connection
            client.connect_websocket()

            # Give the thread time to start
            import time

            time.sleep(0.1)

            # Check that websockets.connect was called with ssl context
            assert mock_connect.called
            args, kwargs = mock_connect.call_args

            # Check if SSL context was passed
            if "ssl" in kwargs:
                ssl_context = kwargs["ssl"]
                assert isinstance(ssl_context, ssl.SSLContext)
                assert ssl_context.check_hostname is False
                assert ssl_context.verify_mode == ssl.CERT_NONE

            # Clean up
            client.disconnect()


def test_ssl_websocket_connection_with_custom_ca_bundle() -> None:
    """Test WebSocket connection with custom CA bundle."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="https://example.com/dicom-web", verify_ssl="/path/to/ca-bundle.crt")
        client.ws_url = "wss://example.com/dicom-web/subscribers/TEST_AE"

        with patch("websockets.connect") as mock_connect:  # noqa: F841
            # Mock SSLContext to avoid file not found errors
            with patch("ssl.create_default_context") as mock_create_context:
                mock_ssl_context = MagicMock()
                mock_create_context.return_value = mock_ssl_context

                # Start the WebSocket connection
                client.connect_websocket()

                # Give the thread time to start
                import time

                time.sleep(0.1)

                # Check that load_verify_locations was called with our CA bundle
                mock_ssl_context.load_verify_locations.assert_called_once_with("/path/to/ca-bundle.crt")

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

        with patch("websockets.connect") as mock_connect:  # noqa: F841
            # Mock SSLContext to avoid file not found errors
            with patch("ssl.create_default_context") as mock_create_context:
                mock_ssl_context = MagicMock()
                mock_create_context.return_value = mock_ssl_context

                # Start the WebSocket connection
                client.connect_websocket()

                # Give the thread time to start
                import time

                time.sleep(0.1)

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
            # Start the WebSocket connection
            client.connect_websocket()

            # Give the thread time to start
            import time

            time.sleep(0.1)

            # Check that websockets.connect was called without ssl context
            assert mock_connect.called
            args, kwargs = mock_connect.call_args

            # For non-SSL connections, ssl parameter should not be present
            assert "ssl" not in kwargs

            # Clean up
            client.disconnect()


@pytest.mark.asyncio
async def test_ssl_context_configuration() -> None:
    """Test that SSL context is properly configured based on client settings."""
    # Create a client with SSL settings
    client = UPSRSClient(
        base_url="https://example.com/dicom-web", verify_ssl=False, client_cert=("/path/to/client.crt", "/path/to/client.key")
    )

    # Set a WebSocket SSL URL
    client.ws_url = "wss://example.com/dicom-web/subscribers/TEST_AE"

    # Mock websockets and ssl modules
    with patch("websockets.connect") as mock_connect:
        mock_websocket = AsyncMock()
        mock_connect.return_value = mock_websocket

        # Start WebSocket connection
        client.connect_websocket()

        # Give the thread time to start
        import time

        time.sleep(0.1)

        # Clean up
        client.disconnect()


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
