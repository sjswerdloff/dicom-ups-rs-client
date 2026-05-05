"""WebSocket mixin for DICOM UPS-RS Client."""

import asyncio
import json
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydicom import Dataset

if TYPE_CHECKING:
    pass


class WebSocketMixin:
    """
    Mixin providing WebSocket connection and notification handling for UPSRSClient.

    This mixin manages WebSocket state and provides methods for connecting,
    disconnecting, and processing incoming UPS-RS event notifications.
    All state attributes are initialized by ``_init_websocket``, which must
    be called from the host class ``__init__``.
    """

    def _init_websocket(self) -> None:
        """
        Initialize WebSocket connection state.

        Must be called from UPSRSClient.__init__ before any WebSocket operations.
        """
        # WebSocket connection state
        self.ws_connection: Any = None
        self.ws_url: str | None = None
        self.running: bool = False
        self.event_callback: Callable[[dict[str, Any]], None] | None = None
        self.ws_thread: threading.Thread | None = None
        # Lock to serialize event_callback invocations from background threads.
        # event_callback may modify caller-owned shared state; callers must not
        # assume re-entrant safety, so we guarantee at-most-one concurrent call.
        self._callback_lock: threading.Lock = threading.Lock()

    def connect_websocket(self, event_callback: Callable[[dict[str, Any]], None] | None = None) -> bool:
        """
        Connect to the WebSocket for receiving notifications.

        Args:
            event_callback: Optional callback function to handle events

        Returns:
            bool: True if connection started, False otherwise

        """
        if not self.ws_url:
            self.logger.error("No WebSocket URL available. Create a subscription first.")  # type: ignore[attr-defined]
            return False

        self.event_callback = event_callback
        self.running = True

        # Start WebSocket connection in a separate thread
        self.ws_thread = threading.Thread(target=self._run_websocket_thread)
        self.ws_thread.daemon = True
        self.ws_thread.start()
        self.logger.info(f"Connecting to WebSocket: {self.ws_url}")  # type: ignore[attr-defined]

        return True

    def disconnect(self) -> None:
        """Disconnect the WebSocket connection."""
        self.logger.info("Disconnecting from WebSocket...")  # type: ignore[attr-defined]
        self.running = False

        # Actively close the WebSocket connection to interrupt recv()
        if self.ws_connection:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.ws_connection.close())
            else:
                # If no event loop is running in this thread, we need another approach.
                # This might be necessary if disconnect() is called from a different thread.
                self.logger.warning("No running event loop found; attempting synchronous close of WebSocket.")  # type: ignore[attr-defined]
                new_loop = asyncio.new_event_loop()
                new_loop.run_until_complete(self.ws_connection.close())
                new_loop.close()
            # Reset the connection reference immediately
            self.ws_connection = None

        # The actual connection will be closed in the WebSocket thread
        # when it detects that self.running is False

        if self.ws_thread and self.ws_thread.is_alive():
            self.ws_thread.join(timeout=2.0)
            if self.ws_thread.is_alive():
                self.logger.warning("WebSocket thread didn't terminate gracefully")  # type: ignore[attr-defined]

        self.logger.info("Disconnected from WebSocket")  # type: ignore[attr-defined]

    def _construct_default_websocket_url(
        self,
        base_url: str,
        endpoint: str,
    ) -> str:
        """
        Construct a default WebSocket URL when none is provided in the response.

        Args:
            base_url: The base URL used for the REST API connection
            endpoint: The endpoint used for the subscription request

        Returns:
            A WebSocket URL constructed based on the base_url and endpoint

        """
        from urllib.parse import urlparse, urlunparse

        base_parsed = urlparse(base_url)

        # Determine appropriate WebSocket scheme based on base URL
        ws_scheme = "wss" if base_parsed.scheme == "https" else "ws"

        # Use the host and port from the base URL
        ws_netloc = base_parsed.netloc

        # Extract the endpoint path without query parameters
        endpoint_parsed = urlparse(endpoint)
        base_path = base_parsed.path.rstrip("/")
        endpoint_path = endpoint_parsed.path.rstrip("/")
        self.logger.info(f"base_path {base_path}, endpoint_path {endpoint_path}")  # type: ignore[attr-defined]

        # Construct appropriate WebSocket path
        # Try to extract subscription ID if present
        path_segments = endpoint_path.split("/")
        if len(path_segments) > 2 and path_segments[-2] == "subscribers":
            subscription_id = path_segments[-1]
            ws_path = f"{base_path}/ws/subscribers/{subscription_id}"
        else:
            ws_path = f"{base_path}/ws"

        ws_url = urlunparse((ws_scheme, ws_netloc, ws_path, "", "", ""))
        self.logger.info(f"Constructed default WebSocket URL: {ws_url}")  # type: ignore[attr-defined]

        return ws_url

    def _run_websocket_thread(self) -> None:
        """WebSocket connection thread that runs the asyncio event loop."""
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Run the WebSocket client
            loop.run_until_complete(self._websocket_client())
        except Exception as e:
            self.logger.error(f"WebSocket thread error: {str(e)}")  # type: ignore[attr-defined]
        finally:
            loop.close()

    async def _handle_message(self, message: str) -> None:
        """
        Process incoming WebSocket messages with DICOM UPS-RS event notifications.

        WebSocket messages are always application/dicom+json per PS3.18 Section 8.10.5.

        Args:
            message: The received message in DICOM+JSON format

        """
        try:
            event_data = json.loads(message)
            self.logger.info(f"Received event: {event_data}")  # type: ignore[attr-defined]

            # Convert JSON to DICOM Dataset
            ds = Dataset.from_json(message)

            # Log the relevant DICOM attributes from the event
            affected_sop_instance_uid = ds.AffectedSOPInstanceUID if hasattr(ds, "AffectedSOPInstanceUID") else "Unknown"
            event_type_id = ds.EventTypeID if hasattr(ds, "EventTypeID") else "Unknown"

            self.logger.info(f"UPS Event Type: {event_type_id} with Affected SOP Instance UID: {affected_sop_instance_uid}")  # type: ignore[attr-defined]

            # Call user-provided event callback if it exists.
            # The lock ensures that at most one callback runs at a time, even
            # when invoked from background WebSocket threads.
            if self.event_callback:

                def _run_callback() -> None:
                    with self._callback_lock:
                        self.event_callback(event_data)  # type: ignore[misc]

                if threading.current_thread() is threading.main_thread():
                    _run_callback()
                else:
                    self.executor.submit(_run_callback)  # type: ignore[attr-defined]
            else:
                self.logger.warning("No event_callback assigned.  Check application level call to connect_websocket")  # type: ignore[attr-defined]

        except json.JSONDecodeError:
            self.logger.error(f"Failed to parse message as JSON: {message}")  # type: ignore[attr-defined]
        except Exception as e:
            self.logger.error(f"Error processing message: {str(e)}")  # type: ignore[attr-defined]

    async def _websocket_client(self) -> None:
        """Asynchronous WebSocket client implementation."""
        # Import websockets here to make it an optional dependency
        import ssl

        import websockets

        # Initial connection
        retry_delay = self.retry_delay  # type: ignore[attr-defined]
        max_retries = self.max_retries  # type: ignore[attr-defined]
        retry_count = 0

        # Configure SSL context for WebSocket
        ssl_context: ssl.SSLContext | None = None
        if self.ws_url and self.ws_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()

            if self.verify_ssl is False:  # type: ignore[attr-defined]
                # Disable SSL verification (use with caution)
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            elif isinstance(self.verify_ssl, str):  # type: ignore[attr-defined]
                # Use custom CA bundle
                ssl_context.load_verify_locations(self.verify_ssl)  # type: ignore[attr-defined]

            # Load client certificate if provided
            if self.client_cert:  # type: ignore[attr-defined]
                if isinstance(self.client_cert, tuple):  # type: ignore[attr-defined]
                    ssl_context.load_cert_chain(self.client_cert[0], self.client_cert[1])  # type: ignore[attr-defined]
                else:
                    ssl_context.load_cert_chain(self.client_cert)  # type: ignore[attr-defined]

        while self.running:
            try:
                self.logger.info(f"Connecting to WebSocket: {self.ws_url}")  # type: ignore[attr-defined]

                # Pass SSL context to websockets.connect only when needed
                connect_kwargs: dict[str, Any] = {}
                if ssl_context:
                    connect_kwargs["ssl"] = ssl_context

                async with websockets.connect(self.ws_url, **connect_kwargs) as websocket:
                    self.ws_connection = websocket
                    self.logger.info("WebSocket connection established")  # type: ignore[attr-defined]
                    retry_count = 0  # Reset retry counter on successful connection

                    # Keep receiving messages until connection is closed
                    while self.running:
                        try:
                            message = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                            await self._handle_message(message)
                        except TimeoutError:
                            # No message received within timeout, check if we should continue
                            if not self.running:
                                # Exit the inner loop immediately if shutdown was requested
                                break
                            continue
                        except websockets.exceptions.ConnectionClosed as e:
                            self.logger.warning(f"WebSocket connection closed: {e}")  # type: ignore[attr-defined]
                            break

                # Check running flag again after inner loop ends
                if not self.running:
                    break

            except (
                websockets.exceptions.WebSocketException,
                ConnectionRefusedError,
            ) as e:
                if not self.running:
                    break

                retry_count += 1
                if retry_count >= max_retries:
                    self.logger.error(f"Maximum retries exceeded. Last error: {e}")  # type: ignore[attr-defined]
                    self.running = False
                    break

                self.logger.info(f"Attempting to reconnect in {retry_delay} seconds... (Attempt {retry_count}/{max_retries})")  # type: ignore[attr-defined]
                await asyncio.sleep(retry_delay)

                # Exponential backoff for retry delay (capped at 60 seconds)
                retry_delay = min(retry_delay * 1.5, 60)

            except Exception as e:
                self.logger.error(f"Unexpected error: {str(e)}")  # type: ignore[attr-defined]
                if self.running:
                    self.logger.info(f"Attempting to reconnect in {retry_delay} seconds...")  # type: ignore[attr-defined]
                    await asyncio.sleep(retry_delay)
                else:
                    break

        self.ws_connection = None
