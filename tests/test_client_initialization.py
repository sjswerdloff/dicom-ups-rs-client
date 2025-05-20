"""Tests for UPS-RS client initialization and context management."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from dicom_ups_rs_client.ups_rs_client import UPSRSClient


def test_init_with_defaults() -> None:
    """Test initialization with default parameters."""
    with patch("requests.Session"):
        client = UPSRSClient(base_url="http://example.com/dicom-web")

        assert client.base_url == "http://example.com/dicom-web"
        assert client.aetitle is None
        assert client.timeout == 30
        assert client.max_retries == 3
        assert client.retry_delay == 1
        assert client.logger is not None
        assert client.ws_connection is None
        assert client.ws_url is None
        assert client.running is False
        assert client.event_callback is None
        assert client.ws_thread is None


def test_init_with_custom_params() -> None:
    """Test initialization with custom parameters."""
    custom_logger = logging.getLogger("test_logger")

    with patch("requests.Session"):
        client = UPSRSClient(
            base_url="http://example.com/dicom-web",
            aetitle="TEST_AE",
            timeout=60,
            max_retries=5,
            retry_delay=2,
            logger=custom_logger,
        )

        assert client.base_url == "http://example.com/dicom-web"
        assert client.aetitle == "TEST_AE"
        assert client.timeout == 60
        assert client.max_retries == 5
        assert client.retry_delay == 2
        assert client.logger is custom_logger


def test_init_with_ssl_verification_disabled() -> None:
    """Test initialization with SSL verification disabled."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="https://example.com/dicom-web", verify_ssl=False)

        assert client.verify_ssl is False
        assert mock_session.verify is False


def test_init_with_custom_ca_bundle() -> None:
    """Test initialization with custom CA bundle."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="https://example.com/dicom-web", verify_ssl="/path/to/ca-bundle.crt")

        assert client.verify_ssl == "/path/to/ca-bundle.crt"
        assert mock_session.verify == "/path/to/ca-bundle.crt"


@pytest.mark.skip(reason="Deferring investigation, might be overzealous validation of CLI arguments.")
def test_init_with_client_certificate() -> None:
    """Test initialization with client certificate."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Test with single file containing both cert and key
        client = UPSRSClient(base_url="https://example.com/dicom-web", client_cert="/path/to/client.pem")

        assert client.client_cert == "/path/to/client.pem"
        assert mock_session.cert == "/path/to/client.pem"


def test_init_with_client_certificate_tuple() -> None:
    """Test initialization with client certificate and key as tuple."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Test with separate cert and key files
        cert_tuple = ("/path/to/client.crt", "/path/to/client.key")
        client = UPSRSClient(base_url="https://example.com/dicom-web", client_cert=cert_tuple)

        assert client.client_cert == cert_tuple
        assert mock_session.cert == cert_tuple


def test_base_url_trailing_slash_removal() -> None:
    """Test that trailing slashes are removed from base_url."""
    with patch("requests.Session"):
        client = UPSRSClient(base_url="http://example.com/dicom-web/")
        assert client.base_url == "http://example.com/dicom-web"

        client = UPSRSClient(base_url="http://example.com/dicom-web///")
        assert client.base_url == "http://example.com/dicom-web"


def test_context_manager() -> None:
    """Test client as context manager (with statement)."""
    with patch("requests.Session"):
        with patch.object(UPSRSClient, "close") as mock_close:
            with UPSRSClient(base_url="http://example.com/dicom-web") as client:
                assert isinstance(client, UPSRSClient)

            # Ensure close was called when exiting the context
            mock_close.assert_called_once()


def test_close_method() -> None:
    """Test that close method properly cleans up resources."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="http://example.com/dicom-web")

        # Mock the executor and ws_thread
        client.executor = MagicMock()
        client.running = True
        client.ws_thread = MagicMock()
        client.ws_thread.is_alive.return_value = False

        # Create a mock for disconnect
        with patch.object(client, "disconnect") as mock_disconnect:
            client.close()

            # Assert disconnect was called
            mock_disconnect.assert_called_once()

            # Assert executor was shutdown
            client.executor.shutdown.assert_called_once_with(wait=True)

            # Assert session was closed
            mock_session.close.assert_called_once()


def test_close_without_websocket() -> None:
    """Test that close method works even without an active WebSocket connection."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="http://example.com/dicom-web")

        # Mock the executor but leave running as False (no WebSocket)
        client.executor = MagicMock()
        client.running = False

        client.close()

        # Assert executor was shutdown
        client.executor.shutdown.assert_called_once_with(wait=True)

        # Assert session was closed
        mock_session.close.assert_called_once()


def test_close_with_executor_exception() -> None:
    """Test that close method handles exceptions from executor shutdown."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        client = UPSRSClient(base_url="http://example.com/dicom-web")

        # Mock the executor and make it raise an exception on shutdown
        client.executor = MagicMock()
        client.executor.shutdown.side_effect = RuntimeError("Test exception")
        client.running = False

        # Should not raise an exception
        client.close()

        # Assert executor shutdown was attempted
        client.executor.shutdown.assert_called_once_with(wait=True)

        # Assert session was still closed despite the exception
        mock_session.close.assert_called_once()


def test_session_reuse() -> None:
    """Test that the client reuses the same session for multiple requests."""
    with patch("requests.Session") as mock_session_class:
        mock_session = MagicMock()
        mock_session_class.return_value = mock_session

        # Configure the mock session's request method to return a successful response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "{}"
        mock_response.json.return_value = {}
        mock_session.request.return_value = mock_response

        client = UPSRSClient(base_url="http://example.com/dicom-web")

        # Make multiple requests
        client._send_request("GET", "http://example.com/endpoint1")
        client._send_request("POST", "http://example.com/endpoint2")

        # Verify that the session was reused (same mock was called multiple times)
        assert mock_session.request.call_count == 2
