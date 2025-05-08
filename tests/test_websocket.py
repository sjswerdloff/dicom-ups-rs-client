"""Tests for UPS-RS client WebSocket functionality."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest
import websockets

from dicom_ups_rs_client.ups_rs_client import UPSRSClient


class MockWebSocketConnection:
    """Mock for WebSocket connection."""

    def __init__(self, events: list[dict] | None = None) -> None:
        """
        Initialize a mock WebSocket connection.

        Args:
            events: Optional list of events to return when receiving messages

        """
        self.events = events or []
        self.connected = True
        self.closed = False
        self.sent_messages = []
        self.current_event_index = 0

    async def __aenter__(self) -> "MockWebSocketConnection":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        """Async context manager exit."""
        self.closed = True
        self.connected = False

    async def recv(self) -> str:
        """
        Receive a message from the WebSocket.

        Returns:
            A JSON-encoded event message string

        Raises:
            websockets.exceptions.ConnectionClosed: If the connection is closed

        """
        if not self.connected:
            raise websockets.exceptions.ConnectionClosed(None, None)

        if self.current_event_index >= len(self.events):
            # No more events, just wait until connection is closed
            while self.connected:
                await asyncio.sleep(0.1)
            raise websockets.exceptions.ConnectionClosed(None, None)

        # Return the next event
        event = self.events[self.current_event_index]
        self.current_event_index += 1
        return json.dumps(event)

    async def send(self, message: str) -> None:
        """
        Send a message to the WebSocket.

        Args:
            message: Message string to send

        """
        self.sent_messages.append(message)

    async def close(self) -> None:
        """Close the WebSocket connection."""
        self.closed = True
        self.connected = False


@pytest.mark.asyncio
async def test_connect_websocket(mock_ups_rs_client: UPSRSClient, sample_event_notification: dict) -> None:
    """
    Test connecting to WebSocket for notifications.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_event_notification: Sample event notification to simulate

    """
    # Mock websockets.connect
    mock_websocket = MockWebSocketConnection([sample_event_notification])

    with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
        # Set WebSocket URL (would normally be obtained from subscription response)
        mock_ups_rs_client.ws_url = "ws://example.com/dicom-web/subscribers/TEST_AE"

        # Create a mock event callback
        event_callback = Mock()

        # Connect to WebSocket
        result = mock_ups_rs_client.connect_websocket(event_callback)

        # Should start a thread
        assert result is True
        assert mock_ups_rs_client.running is True
        assert mock_ups_rs_client.event_callback is event_callback
        assert mock_ups_rs_client.ws_thread is not None

        # Wait for the thread to process the event
        time.sleep(0.5)

        # Check that the callback was called with the event
        event_callback.assert_called_once()
        event_data = event_callback.call_args[0][0]
        assert event_data == sample_event_notification

        # Disconnect to clean up
        mock_ups_rs_client.disconnect()


@pytest.mark.asyncio
async def test_connect_websocket_no_url(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test connecting to WebSocket without a WebSocket URL.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """
    # Ensure no WebSocket URL is set
    mock_ups_rs_client.ws_url = None

    # Try to connect (should fail)
    result = mock_ups_rs_client.connect_websocket()

    # Should not start a thread
    assert result is False
    assert mock_ups_rs_client.running is False
    assert mock_ups_rs_client.ws_thread is None


@pytest.mark.asyncio
async def test_connect_websocket_multiple_events(mock_ups_rs_client: UPSRSClient, sample_event_notification: dict) -> None:
    """
    Test WebSocket receiving multiple events.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_event_notification: Sample event notification to simulate

    """
    # Create multiple events
    event1 = dict(sample_event_notification)
    event1["00001000"]["Value"] = ["1.2.3.4.5"]  # Different UID
    event1["00741000"]["Value"] = ["SCHEDULED"]

    event2 = dict(sample_event_notification)
    event2["00001000"]["Value"] = ["5.6.7.8.9"]  # Different UID
    event2["00741000"]["Value"] = ["IN PROGRESS"]

    event3 = dict(sample_event_notification)
    event3["00001000"]["Value"] = ["9.8.7.6.5"]  # Different UID
    event3["00741000"]["Value"] = ["COMPLETED"]

    mock_websocket = MockWebSocketConnection([event1, event2, event3])

    with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
        # Set WebSocket URL
        mock_ups_rs_client.ws_url = "ws://example.com/dicom-web/subscribers/TEST_AE"

        # Create a mock event callback
        event_callback = Mock()

        # Connect to WebSocket
        result = mock_ups_rs_client.connect_websocket(event_callback)
        logger = mock_ups_rs_client.logger
        logger.debug(f"Result: {result}")

        # Wait for the thread to process all events
        time.sleep(1.0)

        # Check that the callback was called for each event
        assert event_callback.call_count == 3

        # Verify the events were received in the correct order
        assert event_callback.call_args_list[0][0][0]["00001000"]["Value"] == ["1.2.3.4.5"]
        assert event_callback.call_args_list[1][0][0]["00001000"]["Value"] == ["5.6.7.8.9"]
        assert event_callback.call_args_list[2][0][0]["00001000"]["Value"] == ["9.8.7.6.5"]

        # Disconnect to clean up
        mock_ups_rs_client.disconnect()


@pytest.mark.asyncio
async def test_disconnect(mock_ups_rs_client: UPSRSClient, sample_event_notification: dict) -> None:
    """
    Test disconnecting from WebSocket.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_event_notification: Sample event notification to simulate

    """
    # Mock websockets.connect
    mock_websocket = MockWebSocketConnection([sample_event_notification])

    with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
        # Set WebSocket URL
        mock_ups_rs_client.ws_url = "ws://example.com/dicom-web/subscribers/TEST_AE"

        # Connect to WebSocket
        result = mock_ups_rs_client.connect_websocket()
        assert result is True

        # Give it time to start
        time.sleep(0.2)

        # Disconnect
        mock_ups_rs_client.disconnect()

        # Check that the WebSocket connection was closed
        assert mock_ups_rs_client.running is False

        # Give thread time to stop
        time.sleep(0.2)

        # Thread should have terminated
        assert not mock_ups_rs_client.ws_thread.is_alive()


@pytest.mark.asyncio
async def test_websocket_connection_error(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test WebSocket connection error handling.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """
    # Mock websockets.connect to raise an exception
    with patch("websockets.connect", AsyncMock(side_effect=websockets.exceptions.WebSocketException("Connection error"))):
        # Set WebSocket URL
        mock_ups_rs_client.ws_url = "ws://example.com/dicom-web/subscribers/TEST_AE"

        # Connect to WebSocket
        result = mock_ups_rs_client.connect_websocket()
        assert result is True  # Initial result should be True as thread is started

        # Give it time to try connecting
        time.sleep(0.5)

        # Should eventually set running to False after max retries
        assert mock_ups_rs_client.running is False


@pytest.mark.asyncio
async def test_event_callback_exception_handling(mock_ups_rs_client: UPSRSClient, sample_event_notification: dict) -> None:
    """
    Test handling exceptions in event callbacks.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_event_notification: Sample event notification to simulate

    """
    # Mock websockets.connect
    mock_websocket = MockWebSocketConnection([sample_event_notification])

    with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
        # Set WebSocket URL
        mock_ups_rs_client.ws_url = "ws://example.com/dicom-web/subscribers/TEST_AE"

        # Create a callback that raises an exception
        def event_callback_with_exception(event_data: dict) -> None:
            raise ValueError("Test exception in callback")

        # Connect to WebSocket with the problematic callback
        result = mock_ups_rs_client.connect_websocket(event_callback_with_exception)
        assert result is True

        # Wait for the thread to process the event
        time.sleep(0.5)

        # Should still be running despite the callback exception
        assert mock_ups_rs_client.running is True

        # Disconnect to clean up
        mock_ups_rs_client.disconnect()


@pytest.mark.asyncio
async def test_no_event_callback(mock_ups_rs_client: UPSRSClient, sample_event_notification: dict) -> None:
    """
    Test WebSocket connection without an event callback.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_event_notification: Sample event notification to simulate

    """
    # Mock websockets.connect
    mock_websocket = MockWebSocketConnection([sample_event_notification])

    with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
        # Set WebSocket URL
        mock_ups_rs_client.ws_url = "ws://example.com/dicom-web/subscribers/TEST_AE"

        # Connect to WebSocket without a callback
        result = mock_ups_rs_client.connect_websocket()
        assert result is True

        # Wait for the thread to process the event
        time.sleep(0.5)

        # Should still be running despite no callback
        assert mock_ups_rs_client.running is True

        # Disconnect to clean up
        mock_ups_rs_client.disconnect()


@pytest.mark.asyncio
async def test_json_decode_error(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test handling JSON decode errors in WebSocket messages.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """

    # Create a mock WebSocket that returns invalid JSON
    class InvalidJSONWebSocketConnection(MockWebSocketConnection):
        async def recv(self) -> str:
            if not self.connected:
                raise websockets.exceptions.ConnectionClosed(None, None)
            return "invalid json data"

    mock_websocket = InvalidJSONWebSocketConnection()

    with patch("websockets.connect", AsyncMock(return_value=mock_websocket)):
        # Set WebSocket URL
        mock_ups_rs_client.ws_url = "ws://example.com/dicom-web/subscribers/TEST_AE"

        # Create a mock callback
        event_callback = Mock()

        # Connect to WebSocket
        result = mock_ups_rs_client.connect_websocket(event_callback)
        assert result is True

        # Wait for the thread to try processing the message
        time.sleep(0.5)

        # Callback should not have been called due to JSON error
        event_callback.assert_not_called()

        # But connection should still be running
        assert mock_ups_rs_client.running is True

        # Disconnect to clean up
        mock_ups_rs_client.disconnect()
