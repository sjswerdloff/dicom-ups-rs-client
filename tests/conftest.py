"""Fixtures for testing the DICOM UPS-RS client."""

import json
import logging
import re
from collections.abc import Callable, Generator, Iterator
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from pydicom.uid import generate_uid
from requests.structures import CaseInsensitiveDict
from websockets.exceptions import WebSocketException

from dicom_ups_rs_client.ups_rs_client import UPSRSClient


@pytest.fixture(autouse=True)
def configure_logging() -> Generator:
    """Configure logging for tests."""
    logger = logging.getLogger("ups_rs_client")
    # Clear any existing handlers
    logger.handlers = []

    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)  # Set to DEBUG for tests

    yield

    # Clean up
    logger.handlers = []


class MockResponse:
    """Mock for requests.Response object."""

    def __init__(
        self,
        status_code: int = 200,
        json_data: dict[str, Any] | list[dict[str, Any]] | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
        reason: str = "",
        url: str = "https://example.com/mock",
    ) -> None:
        """Initialize a mock response."""
        self.status_code = status_code
        self._json_data = json_data

        # Set text based on json_data if provided and text is empty
        if json_data is not None and not text:
            self.text = json.dumps(json_data)
        else:
            self.text = text

        # Convert text to bytes for content property
        self.content = self.text.encode("utf-8") if self.text else b""

        self.headers = CaseInsensitiveDict(headers or {})
        self.reason = reason
        self.url = url
        self.encoding = "utf-8"
        self.request = None  # Could be expanded if needed
        self.elapsed = timedelta(milliseconds=100)  # Fake response time
        self.cookies = {}
        self.history = []

    def json(self) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Return JSON data from the response."""
        if self._json_data is None:
            raise ValueError("No JSON data provided")
        return self._json_data

    @property
    def ok(self) -> bool:
        """Return True if status_code is less than 400."""
        return 200 <= self.status_code < 400

    def raise_for_status(self) -> None:
        """Raise an HTTPError if the status code is 4XX or 5XX."""
        if 400 <= self.status_code < 600:
            from requests.exceptions import HTTPError

            http_error_msg = f"{self.status_code} {self.reason} for url {self.url}"
            raise HTTPError(http_error_msg, response=self)

    def iter_content(self, chunk_size: int = 1, decode_unicode: bool = False) -> Iterator[bytes]:
        """Iterate over the response content."""
        if not self.content:
            return

        for i in range(0, len(self.content), chunk_size):
            if decode_unicode:
                yield self.content[i : i + chunk_size].decode("utf-8")
            else:
                yield self.content[i : i + chunk_size]

    def close(self) -> None:
        """Close the response."""
        pass


class MockSession:
    """Mock session for testing HTTP clients."""

    def __init__(self) -> None:
        """Initialize the mock session."""
        self.requests = []
        self.responses = {}  # Will store responses by exact URL
        self.pattern_responses = {}  # Will store responses by pattern

    def add_response(self, method: str, url_pattern: str, response: "MockResponse", exact_match: bool = False) -> None:
        """
        Add a response for a given request pattern.

        Args:
            method: HTTP method (GET, POST, etc.)
            url_pattern: URL pattern to match
            response: Response to return
            exact_match: If True, match the URL exactly; if False, use regex pattern matching

        """
        key = f"{method.upper()}:{url_pattern}"

        if exact_match:
            # Store by exact URL for precise matching
            if key not in self.responses:
                self.responses[key] = []
            self.responses[key].append(response)
        else:
            # Store by pattern for regex matching
            if key not in self.pattern_responses:
                self.pattern_responses[key] = []
            self.pattern_responses[key].append(response)

    def request(self, method: str, url: str, **kwargs: dict[str, any]) -> "MockResponse":
        """
        Mock the requests.Session.request method.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: URL to request
            **kwargs: Additional arguments

        Returns:
            MockResponse: The response for the request

        """
        # Store the request for later inspection
        self.requests.append({"method": method, "url": url, **kwargs})

        # Try exact match first (most specific)
        exact_key = f"{method.upper()}:{url}"
        if exact_key in self.responses and self.responses[exact_key]:
            response = self.responses[exact_key].pop(0)
            response.request = self.requests[-1]
            return response

        # Fall back to pattern matching
        for key, responses in list(self.pattern_responses.items()):
            key_method, url_pattern = key.split(":", 1)
            if method.upper() == key_method.upper() and re.match(url_pattern, url):
                if responses:
                    response = responses.pop(0)
                    response.request = self.requests[-1]
                    return response

        # Default response if no match found
        return MockResponse(
            status_code=404, text=f"No mock response configured for {method} {url}", reason="Not Found", url=url
        )


class MockWebSocket:
    """Mock for WebSocket connection."""

    def __init__(self) -> None:
        """Initialize the mock WebSocket."""
        self.connected = False
        self.events: list[dict[str, Any]] = []
        self.close_called = False
        self.received_messages: list[str] = []
        self.closed_code = None
        self.closed_reason = None
        self.ping_received = False
        self.pong_received = False
        self.auto_pong = True  # Automatically respond to pings

    async def __aenter__(self) -> "MockWebSocket":
        """Async context manager entry."""
        self.connected = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:  # noqa: ANN401
        """Async context manager exit."""
        self.connected = False
        self.close_called = True

    def add_event(self, event: dict[str, Any]) -> None:
        """
        Add an event to be returned by recv.

        Args:
            event: Event data to return

        """
        self.events.append(event)

    async def recv(self) -> str:
        """
        Simulate receiving a message.

        Returns:
            A JSON-encoded event message

        Raises:
            websockets.exceptions.ConnectionClosedOK: If connection closed normally
            websockets.exceptions.ConnectionClosedError: If connection closed with error

        """
        import websockets.exceptions

        if not self.connected:
            if self.closed_code in (1000, 1001):
                raise websockets.exceptions.ConnectionClosedOK(self.closed_code, self.closed_reason or "")
            else:
                raise websockets.exceptions.ConnectionClosedError(
                    self.closed_code or 1006, self.closed_reason or "Connection closed abnormally"
                )

        if not self.events:
            # Wait indefinitely or until connection closed
            import asyncio

            await asyncio.sleep(3600)
            raise websockets.exceptions.ConnectionClosedError(1006, "Connection closed while waiting for message")

        event = self.events.pop(0)
        return json.dumps(event) if isinstance(event, dict) else event

    async def send(self, message: str) -> None:
        """
        Simulate sending a message.

        Args:
            message: Message to send

        Raises:
            websockets.exceptions.ConnectionClosedError: If connection is closed

        """
        import websockets.exceptions

        if not self.connected:
            raise websockets.exceptions.ConnectionClosedError(
                self.closed_code or 1006, self.closed_reason or "Connection closed"
            )

        self.received_messages.append(message)

    async def ping(self, data: bytes = b"") -> None:
        """
        Simulate sending a ping frame.

        Args:
            data: Optional data to include in ping frame

        """
        self.ping_received = True
        if self.auto_pong:
            # Auto-respond with pong
            await self.pong(data)

    async def pong(self, data: bytes = b"") -> None:
        """
        Simulate sending a pong frame.

        Args:
            data: Optional data to include in pong frame

        """
        self.pong_received = True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """
        Simulate closing the connection.

        Args:
            code: Close code
            reason: Close reason

        """
        self.connected = False
        self.close_called = True
        self.closed_code = code
        self.closed_reason = reason


@pytest.fixture
def mock_session() -> MockSession:
    """
    Provide a mock requests.Session object for testing.

    Returns:
        A configured MockSession instance

    """
    return MockSession()


@pytest.fixture
def mock_websockets() -> Mock:
    """
    Provide a mock for websockets.connect.

    Returns:
        A mock for websockets.connect function

    """
    mock_ws = MagicMock()
    mock_ws.connect = AsyncMock()
    return mock_ws


@pytest.fixture
def sample_workitem() -> dict[str, Any]:
    """
    Provide a sample workitem for testing.

    Returns:
        A dictionary containing a valid UPS workitem

    """
    uid = str(generate_uid())
    now = datetime.now()
    scheduled_start = (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S")
    scheduled_end = (now + timedelta(hours=3)).strftime("%Y%m%d%H%M%S")

    return {
        "00080018": {"vr": "UI", "Value": [uid]},  # SOP Instance UID
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},  # Procedure Step State
        "00404041": {"vr": "CS", "Value": ["READY"]},  # Input Readiness State
        "00404005": {"vr": "DT", "Value": [scheduled_start]},  # Scheduled Procedure Step Start DateTime
        "00404011": {"vr": "DT", "Value": [scheduled_end]},  # Scheduled Procedure Step End DateTime
        "00741204": {"vr": "LO", "Value": ["Test Procedure"]},  # Procedure Step Label
        "00404000": {"vr": "CS", "Value": ["IMAGE_PROCESSING"]},  # Workitem Type
        "00400007": {"vr": "LO", "Value": ["Test procedure step description"]},  # Procedure Step Description
    }


@pytest.fixture
def sample_workitem_in_progress() -> dict[str, Any]:
    """
    Provide a sample workitem in IN PROGRESS state.

    Returns:
        A dictionary containing a valid UPS workitem in IN PROGRESS state

    """
    workitem = sample_workitem()
    workitem["00741000"]["Value"] = ["IN PROGRESS"]
    workitem["00081195"] = {"vr": "UI", "Value": [str(generate_uid())]}  # Transaction UID
    return workitem


@pytest.fixture
def sample_workitem_completed() -> dict[str, Any]:
    """
    Provide a sample workitem in COMPLETED state.

    Returns:
        A dictionary containing a valid UPS workitem in COMPLETED state

    """
    workitem = sample_workitem()
    workitem["00741000"]["Value"] = ["COMPLETED"]
    workitem["00081195"] = {"vr": "UI", "Value": [str(generate_uid())]}  # Transaction UID
    return workitem


@pytest.fixture
def sample_workitem_canceled() -> dict[str, Any]:
    """
    Provide a sample workitem in CANCELED state.

    Returns:
        A dictionary containing a valid UPS workitem in CANCELED state

    """
    workitem = sample_workitem()
    workitem["00741000"]["Value"] = ["CANCELED"]
    workitem["00081195"] = {"vr": "UI", "Value": [str(generate_uid())]}  # Transaction UID
    return workitem


@pytest.fixture
def sample_event_notification() -> dict[str, Any]:
    """
    Provide a sample UPS event notification.

    Returns:
        A dictionary containing a valid UPS event notification

    """
    return {
        "00001000": {"vr": "UI", "Value": [str(generate_uid())]},  # Affected SOP Instance UID
        "00001002": {"vr": "US", "Value": [1]},  # Event Type ID (1 = UPS State Report)
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},  # Procedure Step State
        "00404041": {"vr": "CS", "Value": ["READY"]},  # Input Readiness State
    }


@pytest.fixture
def sample_ups_assignment_notification() -> dict[str, Any]:
    """
    Provide a sample UPS assignment notification.

    Returns:
        A dictionary containing a valid UPS assignment notification

    """
    return {
        "00001000": {"vr": "UI", "Value": [str(generate_uid())]},  # Affected SOP Instance UID
        "00001002": {"vr": "US", "Value": [5]},  # Event Type ID (5 = UPS Assigned)
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},  # Procedure Step State
        "00404041": {"vr": "CS", "Value": ["READY"]},  # Input Readiness State
    }


@pytest.fixture
def mock_ups_rs_client(mock_session: MockSession) -> UPSRSClient:
    """
    Provide a UPS-RS client with mocked session.

    Args:
        mock_session: The mock session to use

    Returns:
        A UPS-RS client configured with the mock session

    """
    with patch("requests.Session", return_value=mock_session):
        client = UPSRSClient(
            base_url="http://example.com/dicom-web",
            aetitle="TEST_AE",
            timeout=30,
            max_retries=3,
            retry_delay=1,
        )
        client.session = mock_session
        return client


@pytest.fixture
def response_factory() -> Callable[..., MockResponse]:
    """
    Provide a factory function for creating mock responses.

    Returns:
        A factory function that creates MockResponse objects

    """

    def create_response(
        status_code: int = 200,
        json_data: dict[str, Any] | list[dict[str, Any]] | None = None,
        text: str = "",
        headers: dict[str, str] | None = None,
        reason: str = "",
    ) -> MockResponse:
        """
        Create a mock response with the given parameters.

        Args:
            status_code: HTTP status code
            json_data: JSON data to return
            text: Text content
            headers: HTTP headers
            reason: HTTP reason phrase

        Returns:
            A MockResponse object

        """
        return MockResponse(status_code=status_code, json_data=json_data, text=text, headers=headers or {}, reason=reason)

    return create_response


@pytest.fixture
def mock_websocket() -> MockWebSocket:
    """Create a basic mock WebSocket for testing."""
    return MockWebSocket()


@pytest.fixture
def mock_websocket_with_events(sample_event_notification: dict[str, Any]) -> MockWebSocket:
    """Create a mock WebSocket with the sample event notification."""
    ws = MockWebSocket()
    ws.add_event(sample_event_notification)
    return ws


@pytest.fixture
def websocket_connect_patch():  # noqa: ANN201
    """Patch websockets.connect to return a mock websocket."""
    mock_ws = MockWebSocket()

    # Create a function that matches the signature of websockets.connect
    # but immediately returns the mock (not a coroutine)
    def mock_connect(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        # This is important: return the mock directly, not as a coroutine
        # This avoids the need for awaiting
        return mock_ws

    # Patch the websockets.connect function
    with patch("websockets.connect", mock_connect) as patched:
        # Store the mock_websocket for easy access in tests
        patched.mock_websocket = mock_ws
        yield patched


@pytest.fixture
def websocket_with_events():  # noqa: ANN201
    """Create a fixture that provides a WebSocket with multiple events."""

    def _create_websocket_with_events(events):  # noqa: ANN001, ANN202
        """Create a MockWebSocket with the specified events."""
        mock_ws = MockWebSocket()
        for event in events:
            mock_ws.add_event(event)

        # Create a websockets.connect mock function
        def mock_connect(*args: tuple, **kwargs: dict[str, any]) -> MockWebSocket:
            return mock_ws

        return mock_connect, mock_ws

    return _create_websocket_with_events


@pytest.fixture
def websocket_error_connect():  # noqa: ANN201
    """
    Fixture to create a WebSocket connect function that raises exceptions.

    This fixture simulates a websocket connection that raises an exception
    during different parts of the connection lifecycle.
    """

    # Create a special MockWebSocket that raises exceptions
    class ErrorMockWebSocket(MockWebSocket):
        def __init__(self, error_type=None, error_location="aenter", error_msg="Connection error") -> None:  # noqa: ANN001
            super().__init__()
            self.error_type = error_type or WebSocketException
            self.error_location = error_location
            self.error_msg = error_msg

        async def __aenter__(self):  # noqa: ANN204
            """Async context manager entry with optional exception."""
            if self.error_location == "aenter":
                raise self.error_type(self.error_msg)
            self.connected = True
            return self

        async def recv(self):  # noqa: ANN202
            """Receive with optional exception."""
            if self.error_location == "recv":
                raise self.error_type(self.error_msg)
            return await super().recv()

        async def send(self, message) -> None:  # noqa: ANN001
            """Send with optional exception."""
            if self.error_location == "send":
                raise self.error_type(self.error_msg)
            await super().send(message)

    def _create_error_connect(error_type=None, error_location="aenter", error_msg="Connection error"):  # noqa: ANN001, ANN202
        """
        Create a mock websocket connect function that raises errors.

        Args:
            error_type: Exception class to raise (default: WebSocketException)
            error_location: Where to raise the exception - 'aenter', 'recv', or 'send'
            error_msg: Error message for the exception

        Returns:
            A mock connect function that returns a WebSocket that raises an exception

        """
        if error_type is None:
            error_type = WebSocketException

        # Create a mock WebSocket that will raise the exception
        mock_ws = ErrorMockWebSocket(error_type, error_location, error_msg)

        # Return a function that matches websockets.connect signature
        def mock_connect(*args: tuple, **kwargs: dict[str, any]):  # noqa: ANN202
            return mock_ws

        return mock_connect

    return _create_error_connect
