"""DICOM UPS-RS Client."""

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import requests
from pydicom import Dataset, uid
from pydicom.uid import generate_uid

from dicom_ups_rs_client.enums import InputReadinessState, UPSState
from dicom_ups_rs_client.exceptions import UPSRSError, UPSRSRequestError, UPSRSResponseError, UPSRSValidationError
from dicom_ups_rs_client.serialization import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_XML,
    make_headers,
    parse_response_body,
    serialize_request,
)

__all__ = [
    "UPSRSClient",
    "UPSState",
    "InputReadinessState",
    "UPSRSError",
    "UPSRSResponseError",
    "UPSRSRequestError",
    "UPSRSValidationError",
    "CONTENT_TYPE_JSON",
    "CONTENT_TYPE_XML",
]


class UPSRSClient:
    """
    Unified client for interacting with DICOM UPS-RS services.

    This client provides a comprehensive interface to the UPS-RS (Unified Procedure Step - RESTful Services)
    API as defined in the DICOM standard. It supports all operations including creating, retrieving,
    updating, and changing the state of workitems, as well as subscription management for event notifications.
    """

    def __init__(
        self,
        base_url: str,
        aetitle: str | None = None,
        timeout: int = 30,
        max_retries: int = 3,
        retry_delay: int = 1,
        logger: logging.Logger | None = None,
        verify_ssl: bool | str = True,
        client_cert: str | tuple[str, str] | None = None,
        websocket_url_override: str | None = None,
        content_type: str = CONTENT_TYPE_JSON,
    ) -> None:
        """
        Initialize the UPS-RS client.

        Args:
            base_url: The base URL of the UPS-RS server
            aetitle: Optional Application Entity Title for subscription operations
            timeout: Request timeout in seconds
            max_retries: Maximum number of request retries
            retry_delay: Delay between retries in seconds
            logger: Optional logger instance
            verify_ssl: SSL certificate verification. Can be:
                - True: Verify SSL certificates (default)
                - False: Disable SSL certificate verification (use with caution)
                - str: Path to CA bundle file or directory with certificates
            client_cert: Client-side certificate for authentication. Can be:
                - None: No client certificate (default)
                - str: Path to .pem file containing certificate and key
                - tuple: (cert_file, key_file) paths
            websocket_url_override: Optional override for WebSocket URL template.
                Can include {aetitle} placeholder.
                Example: "wss://example.com:9443/ws/subscribers/{aetitle}"
            content_type: MIME type for request/response content negotiation.
                Defaults to ``application/dicom+json``.
                Use ``application/dicom+xml`` for XML content type.
                WebSocket notifications always use JSON per PS3.18 Section 8.10.5.

        """
        self.base_url = base_url.rstrip("/")
        self.aetitle = aetitle
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.verify_ssl = verify_ssl
        self.client_cert = client_cert
        self.content_type = content_type

        # Validate client_cert tuple shape early
        if self.client_cert is not None:
            if not (
                isinstance(self.client_cert, tuple)
                and len(self.client_cert) == 2
                and all(isinstance(path, str) for path in self.client_cert)
            ):
                raise ValueError("client_cert must be a two-element tuple of strings (cert_file, key_file)")
        self.websocket_url_override = websocket_url_override

        # Set up logging
        self.logger = logger or logging.getLogger("ups_rs_client")
        if not logger:
            self.logger.setLevel(logging.WARNING)
            if not self.logger.handlers:
                handler = logging.StreamHandler()
                formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
                handler.setFormatter(formatter)
                self.logger.addHandler(handler)

        # WebSocket connection state
        self.ws_connection = None
        self.ws_url = None
        self.running = False
        self.event_callback = None
        self.ws_thread = None
        # Lock to serialize event_callback invocations from background threads.
        # event_callback may modify caller-owned shared state; callers must not
        # assume re-entrant safety, so we guarantee at-most-one concurrent call.
        self._callback_lock: threading.Lock = threading.Lock()

        # Session for connection pooling
        self.session = requests.Session()
        self.session.verify = self.verify_ssl
        if self.client_cert:
            self.session.cert = self.client_cert

        # Thread pool for async operations
        self.executor = ThreadPoolExecutor(max_workers=5)

    def __enter__(self) -> "UPSRSClient":
        """Enter the runtime context for this client."""
        return self

    def __exit__(self, exc_type: type | None, exc_val: Exception | None, exc_tb: object | None) -> bool:
        """Exit the runtime context for this client."""
        self.close()
        return False  # Propagate exceptions

    def close(self) -> None:
        """Close the client and release all resources."""
        # Disconnect WebSocket if connected
        if self.running:
            try:
                self.disconnect()
            except Exception as e:
                # Log the exception but continue closing resources
                if hasattr(self, "logger"):
                    self.logger.error(f"Error during disconnect: {e}")

        # Close thread pool
        if hasattr(self, "executor"):
            try:
                self.executor.shutdown(wait=True)
            except Exception as e:
                # Log the exception but continue closing resources
                if hasattr(self, "logger"):
                    self.logger.error(f"Error shutting down executor: {e}")

        # Close HTTP session
        if hasattr(self, "session"):
            try:
                self.session.close()
            except Exception as e:
                # Log the exception
                if hasattr(self, "logger"):
                    self.logger.error(f"Error closing session: {e}")

    # ========== Header helpers ==========

    def _make_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """
        Build HTTP headers with the negotiated content type.

        Args:
            extra: Optional additional headers to merge. Keys in ``extra``
                override the defaults only when they do not duplicate the
                Content-Type or Accept keys (those are always derived from
                ``self.content_type``).

        Returns:
            A dict containing at minimum ``Content-Type`` and ``Accept`` headers.

        """
        return make_headers(self.content_type, extra)

    # ========== Core Operations ==========

    def create_workitem(
        self,
        workitem_data: dict[str, Any] | None = None,
        workitem_uid: str | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Create a new workitem on the UPS-RS server.

        Args:
            workitem_data: dictionary containing the workitem dataset
            workitem_uid: Optional UID for the workitem. If not provided,
                          it will be generated or assigned by the server.

        Returns:
            tuple containing success status and either the response data or error message

        """
        # Use default workitem data if none provided
        if workitem_data is None:
            workitem_data = self._create_default_workitem()

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems"
        if workitem_uid:
            if not self.validate_uid(workitem_uid):
                return (
                    False,
                    f"Invalid DICOM UID format for workitem_uid: {workitem_uid}",
                )
            endpoint = f"{endpoint}?workitem={workitem_uid}"

        headers = self._make_headers()

        return self._send_request("POST", endpoint, headers=headers, json_data=workitem_data, success_code=201)

    def retrieve_workitem(self, workitem_uid: str) -> tuple[bool, dict[str, Any] | str]:
        """
        Retrieve a workitem from the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to retrieve

        Returns:
            tuple containing success status and either the response data or error message

        """
        # Validate the DICOM UID format
        if not self.validate_uid(workitem_uid):
            return False, f"Invalid DICOM UID format: {workitem_uid}"

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/{workitem_uid}"

        # Set headers according to DICOM PS3.18 specification
        # Override Accept to include Cache-Control; Content-Type is not needed for GET
        headers = self._make_headers(extra={"Cache-Control": "no-cache"})
        # GET requests should not send Content-Type
        headers.pop("Content-Type", None)

        return self._send_request("GET", endpoint, headers=headers)

    def search_workitems(
        self,
        match_parameters: dict[str, str],
        include_fields: list[str] | None = None,
        fuzzy_matching: bool = False,
        offset: int = 0,
        limit: int | None = None,
        no_cache: bool = False,
    ) -> tuple[bool, list[dict[str, Any]] | str]:
        """
        Search for workitems on the UPS-RS server.

        Args:
            match_parameters: dictionary of attribute/value pairs to match
            include_fields: Optional list of additional fields to include in results
            fuzzy_matching: Whether to use fuzzy matching (default: False)
            offset: Starting position of results (default: 0)
            limit: Maximum number of results to return (default: None)
            no_cache: Whether to request non-cached results (default: False)

        Returns:
            tuple containing success status and either the response data or error message

        """
        params = dict(match_parameters)
        # Add include fields if provided
        if include_fields:
            for field in include_fields:
                if "includefield" in params:
                    params["includefield"] += f",{field}"
                else:
                    params["includefield"] = field

        # Add fuzzy matching if enabled
        if fuzzy_matching:
            params["fuzzymatching"] = "true"

        # Add paging parameters if provided
        params["offset"] = str(offset)
        if limit is not None:
            params["limit"] = str(limit)

        # Set endpoint URL with query parameters
        endpoint = f"{self.base_url}/workitems?{urlencode(params, doseq=True)}"

        # Build headers — GET does not send a body, so no Content-Type
        extra: dict[str, str] = {}
        if no_cache:
            extra["Cache-Control"] = "no-cache"
        headers = self._make_headers(extra=extra)
        headers.pop("Content-Type", None)

        success, response = self._send_request("GET", endpoint, headers=headers)

        # Handle 204 No Content response
        if success and isinstance(response, dict) and response.get("status_code") == 204:
            return True, []

        # Handle 206 Partial Content
        if success and isinstance(response, dict) and response.get("status_code") == 206:
            self.logger.info("Search returned partial results (more available)")

        return success, response

    def update_workitem(
        self,
        workitem_uid: str,
        transaction_uid: str | None,
        update_data: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Update a workitem on the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to update
            transaction_uid: Transaction UID (required for updates to IN PROGRESS workitems)
            update_data: dictionary containing the workitem attributes to update

        Returns:
            tuple containing success status and either the response data or error message

        """
        # Validate the DICOM UID format
        if not self.validate_uid(workitem_uid):
            return False, f"Invalid DICOM UID format for workitem_uid: {workitem_uid}"

        if transaction_uid and not self.validate_uid(transaction_uid):
            return (
                False,
                f"Invalid DICOM UID format for transaction_uid: {transaction_uid}",
            )

        # Set endpoint URL with transaction-uid query parameter
        if transaction_uid:
            endpoint = f"{self.base_url}/workitems/{workitem_uid}?transaction-uid={transaction_uid}"
        else:
            # Unless the assumption is that the procedure step state is SCHEDULED
            # and one wants to test the UPS-RS server for its response to this.
            # or test its response to a missing transaction uid when it is required
            endpoint = f"{self.base_url}/workitems/{workitem_uid}"

        headers = self._make_headers()

        return self._send_request("PUT", endpoint, headers=headers, json_data=update_data)

    def change_workitem_state(
        self,
        workitem_uid: str,
        new_state: str | UPSState,
        transaction_uid: str | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Change the state of a workitem on the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to change
            new_state: New state for the workitem ("IN PROGRESS", "COMPLETED", or "CANCELED")
                       Can be either a string or a UPSState enum value
            transaction_uid: Transaction UID (required for IN PROGRESS, COMPLETED, and CANCELED states)
                             If None, a new one will be generated for IN PROGRESS state

        Returns:
            tuple containing success status and either the response data or error message

        """
        # Validate the DICOM UID format
        if not self.validate_uid(workitem_uid):
            return False, f"Invalid DICOM UID format for workitem_uid: {workitem_uid}"

        # Convert UPSState enum to string if needed
        if isinstance(new_state, UPSState):
            new_state = str(new_state)

        # Validate state
        valid_states = ["IN PROGRESS", "COMPLETED", "CANCELED"]
        if new_state not in valid_states:
            error_msg = f"Invalid state: {new_state}. Must be one of {valid_states}"
            self.logger.error(error_msg)
            return False, error_msg

        # Handle Transaction UID
        if new_state == "IN PROGRESS" and transaction_uid is None:
            # Generate a new Transaction UID for IN PROGRESS state
            transaction_uid = str(generate_uid())
            self.logger.info(f"Generated new Transaction UID: {transaction_uid}")
        elif new_state in {"COMPLETED", "CANCELED"} and transaction_uid is None:
            error_msg = f"Transaction UID is required for {new_state} state"
            self.logger.error(error_msg)
            return False, error_msg

        if transaction_uid and not self.validate_uid(transaction_uid):
            return (
                False,
                f"Invalid DICOM UID format for transaction_uid: {transaction_uid}",
            )

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/{workitem_uid}/state"

        headers = self._make_headers()

        # Prepare payload
        payload: dict[str, Any] = {
            # Procedure Step State (0074,1000)
            "00741000": {"vr": "CS", "Value": [new_state]}
        }

        # Add Transaction UID if provided
        if transaction_uid:
            # Transaction UID (0008,1195)
            payload["00081195"] = {"vr": "UI", "Value": [transaction_uid]}

        success, response = self._send_request("PUT", endpoint, headers=headers, json_data=payload)

        if success and isinstance(response, dict):
            response["transaction_uid"] = transaction_uid

        return success, response

    def request_cancellation(
        self,
        workitem_uid: str,
        reason: str | None = None,
        contact_name: str | None = None,
        contact_uri: str | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Request cancellation of a workitem on the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to request cancellation for
            reason: Optional reason for the cancellation request
            contact_name: Optional display name of the contact person
            contact_uri: Optional URI for contacting the requestor

        Returns:
            tuple containing success status and either the response data or error message

        """
        # Validate the DICOM UID format
        if not self.validate_uid(workitem_uid):
            return False, f"Invalid DICOM UID format for workitem_uid: {workitem_uid}"

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/{workitem_uid}/cancelrequest"

        # Prepare payload with cancellation request information
        payload: dict[str, Any] = {}

        # Add Reason For Cancellation (0074,1238) if provided
        if reason:
            payload["00741238"] = {"vr": "LT", "Value": [reason]}

        # Add Contact Display Name (0074,100E) if provided
        if contact_name:
            payload["0074100E"] = {"vr": "PN", "Value": [contact_name]}

        # Add Contact URI (0074,100F) if provided
        if contact_uri:
            payload["0074100F"] = {"vr": "UT", "Value": [contact_uri]}

        headers = self._make_headers()

        return self._send_request("POST", endpoint, headers=headers, json_data=payload, success_code=202)

    # ========== Event Management ==========

    def subscribe_to_worklist(self, deletion_lock: bool = False) -> tuple[bool, dict[str, Any] | str]:
        """
        Subscribe to all workitems in the worklist.

        Args:
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:
            return False, "AE Title is required for subscription operations"

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/1.2.840.10008.5.1.4.34.5/subscribers/{self.aetitle}"

        # Add deletion lock parameter if requested
        if deletion_lock:
            endpoint += "?deletionlock=true"

        return self._send_subscription_request(endpoint)

    def subscribe_to_filtered_worklist(
        self, filter_params: dict[str, str], deletion_lock: bool = False
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Subscribe to workitems matching the specified filter criteria.

        Args:
            filter_params: dictionary of attribute/value pairs to filter on
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:
            return False, "AE Title is required for subscription operations"

        # Build filter parameter string
        filter_str = ",".join([f"{key}={value}" for key, value in filter_params.items()])

        # Set endpoint URL with filter parameter
        endpoint = f"{self.base_url}/workitems/1.2.840.10008.5.1.4.34.5.1/subscribers/{self.aetitle}"
        endpoint += f"?filter={filter_str}"

        # Add deletion lock parameter if requested
        if deletion_lock:
            endpoint += "&deletionlock=true"

        return self._send_subscription_request(endpoint)

    def subscribe_to_workitem(self, workitem_uid: str, deletion_lock: bool = False) -> tuple[bool, dict[str, Any] | str]:
        """
        Subscribe to a specific workitem.

        Args:
            workitem_uid: UID of the workitem to subscribe to
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:
            return False, "AE Title is required for subscription operations"

        # Validate the DICOM UID format
        if not self.validate_uid(workitem_uid):
            return False, f"Invalid DICOM UID format for workitem_uid: {workitem_uid}"

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/{workitem_uid}/subscribers/{self.aetitle}"

        # Add deletion lock parameter if requested
        if deletion_lock:
            endpoint += "?deletionlock=true"

        return self._send_subscription_request(endpoint)

    def unsubscribe_from_worklist(self, deletion_lock: bool = False) -> tuple[bool, dict[str, Any] | str]:
        """
        Unsubscribe from all workitems in the worklist.

        Args:
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:
            return False, "AE Title is required for subscription operations"

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/1.2.840.10008.5.1.4.34.5/subscribers/{self.aetitle}"

        # Add deletion lock parameter if requested
        if deletion_lock:
            endpoint += "?deletionlock=true"

        return self._send_unsubscription_request(endpoint)

    def unsubscribe_from_filtered_worklist(
        self, filter_params: dict[str, str], deletion_lock: bool = False
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Unsubscribe from workitems matching the specified filter criteria.

        Args:
            filter_params: dictionary of attribute/value pairs to filter on
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:
            return False, "AE Title is required for subscription operations"

        # Build filter parameter string
        filter_str = ",".join([f"{key}={value}" for key, value in filter_params.items()])

        # Set endpoint URL with filter parameter
        endpoint = f"{self.base_url}/workitems/1.2.840.10008.5.1.4.34.5.1/subscribers/{self.aetitle}"
        endpoint += f"?filter={filter_str}"

        # Add deletion lock parameter if requested
        if deletion_lock:
            endpoint += "&deletionlock=true"

        return self._send_unsubscription_request(endpoint)

    def unsubscribe_from_workitem(self, workitem_uid: str, deletion_lock: bool = False) -> tuple[bool, dict[str, Any] | str]:
        """
        Unsubscribe from a specific workitem.

        Args:
            workitem_uid: UID of the workitem to unsubscribe from
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:
            return False, "AE Title is required for subscription operations"

        # Validate the DICOM UID format
        if not self.validate_uid(workitem_uid):
            return False, f"Invalid DICOM UID format for workitem_uid: {workitem_uid}"

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/{workitem_uid}/subscribers/{self.aetitle}"

        # Add deletion lock parameter if requested
        if deletion_lock:
            endpoint += "?deletionlock=true"

        return self._send_unsubscription_request(endpoint)

    def connect_websocket(self, event_callback: Callable[[dict[str, Any]], None] | None = None) -> bool:
        """
        Connect to the WebSocket for receiving notifications.

        Args:
            event_callback: Optional callback function to handle events

        Returns:
            bool: True if connection started, False otherwise

        """
        if not self.ws_url:
            self.logger.error("No WebSocket URL available. Create a subscription first.")
            return False

        self.event_callback = event_callback
        self.running = True

        # Start WebSocket connection in a separate thread
        self.ws_thread = threading.Thread(target=self._run_websocket_thread)
        self.ws_thread.daemon = True
        self.ws_thread.start()
        self.logger.info(f"Connecting to WebSocket: {self.ws_url}")

        return True

    def disconnect(self) -> None:
        """Disconnect the WebSocket connection."""
        self.logger.info("Disconnecting from WebSocket...")
        self.running = False

        # Actively close the WebSocket connection to interrupt recv()
        if self.ws_connection:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.ws_connection.close())
            else:
                # If no event loop is running in this thread, we need another approach.
                # This might be necessary if disconnect() is called from a different thread.
                self.logger.warning("No running event loop found; attempting synchronous close of WebSocket.")
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
                self.logger.warning("WebSocket thread didn't terminate gracefully")

        self.logger.info("Disconnected from WebSocket")

    # ========== Asynchronous Methods ==========

    async def create_workitem_async(
        self,
        workitem_data: dict[str, Any] | None = None,
        workitem_uid: str | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Asynchronously create a new workitem on the UPS-RS server.

        Args:
            workitem_data: dictionary containing the workitem dataset
            workitem_uid: Optional UID for the workitem

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, lambda: self.create_workitem(workitem_data, workitem_uid))

    async def retrieve_workitem_async(self, workitem_uid: str) -> tuple[bool, dict[str, Any] | str]:
        """
        Asynchronously retrieve a workitem from the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to retrieve

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, lambda: self.retrieve_workitem(workitem_uid))

    async def search_workitems_async(
        self,
        match_parameters: dict[str, str],
        include_fields: list[str] | None = None,
        fuzzy_matching: bool = False,
        offset: int = 0,
        limit: int | None = None,
        no_cache: bool = False,
    ) -> tuple[bool, list[dict[str, Any]] | str]:
        """
        Asynchronously search for workitems on the UPS-RS server.

        Args:
            match_parameters: dictionary of attribute/value pairs to match
            include_fields: Optional list of additional fields to include in results
            fuzzy_matching: Whether to use fuzzy matching (default: False)
            offset: Starting position of results (default: 0)
            limit: Maximum number of results to return (default: None)
            no_cache: Whether to request non-cached results (default: False)

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self.search_workitems(match_parameters, include_fields, fuzzy_matching, offset, limit, no_cache),
        )

    async def update_workitem_async(
        self,
        workitem_uid: str,
        transaction_uid: str | None,
        update_data: dict[str, Any],
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Asynchronously update a workitem on the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to update
            transaction_uid: Transaction UID for the update
            update_data: dictionary containing the workitem attributes to update

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor, lambda: self.update_workitem(workitem_uid, transaction_uid, update_data)
        )

    async def change_workitem_state_async(
        self,
        workitem_uid: str,
        new_state: str | UPSState,
        transaction_uid: str | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Asynchronously change the state of a workitem on the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to update
            new_state: New state for the workitem
            transaction_uid: Transaction UID (required for state changes)

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self.change_workitem_state(workitem_uid, new_state, transaction_uid),
        )

    async def request_cancellation_async(
        self,
        workitem_uid: str,
        reason: str | None = None,
        contact_name: str | None = None,
        contact_uri: str | None = None,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Asynchronously request cancellation of a workitem on the UPS-RS server.

        Args:
            workitem_uid: UID of the workitem to request cancellation for
            reason: Optional reason for the cancellation request
            contact_name: Optional display name of the contact person
            contact_uri: Optional URI for contacting the requestor

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self.request_cancellation(workitem_uid, reason, contact_name, contact_uri),
        )

    # ========== Utility Methods ==========

    @staticmethod
    def validate_uid(uid_string: str) -> bool:
        """
        Validate that a string is a valid DICOM UID.

        Args:
            uid_string: The UID string to validate

        Returns:
            bool: True if valid, False otherwise

        """
        return uid.UID(uid_string).is_valid

    def _create_default_workitem(self) -> dict[str, Any]:
        """
        Create a default workitem dataset with required attributes.

        Returns:
            dictionary containing a default workitem dataset

        """
        # Current time
        now = datetime.now()

        # Scheduled start and end times (start in 1 hour, end 2 hours after that)
        scheduled_start = (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S")
        scheduled_end = (now + timedelta(hours=3)).strftime("%Y%m%d%H%M%S")

        # Create a workitem with required attributes
        # This is a simplified example - in practice, you would include all required attributes
        # according to the DICOM standard (PS3.4 Table CC.2.5-3)
        workitem = {
            # Procedure Step State (0074,1000)
            "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},
            # Input Readiness State (0040,4041)
            "00404041": {"vr": "CS", "Value": ["READY"]},
            # Scheduled Procedure Step Start DateTime (0040,4005)
            "00404005": {"vr": "DT", "Value": [scheduled_start]},
            # Scheduled Procedure Step End DateTime (0040,4011) - Optional but recommended
            "00404011": {"vr": "DT", "Value": [scheduled_end]},
            # Procedure Step Label (0074,1204)
            "00741204": {"vr": "LO", "Value": ["Example Procedure"]},
            # Workitem Type (0040,4000)
            "00404000": {"vr": "CS", "Value": ["IMAGE_PROCESSING"]},
            # Procedure Step Description (0040,0007)
            "00400007": {"vr": "LO", "Value": ["Example procedure step description"]},
        }

        return workitem

    def _send_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_data: Any = None,  # noqa: ANN401
        success_code: int = 200,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Send an HTTP request to the UPS-RS server with retry logic.

        When ``self.content_type`` is ``application/dicom+xml`` and ``json_data``
        is provided, the body is serialised to XML via
        ``dicom_ups_rs_client.serialization.serialize_request`` and sent as raw
        bytes.  Response parsing similarly dispatches on the response Content-Type
        header via ``dicom_ups_rs_client.serialization.parse_response_body``.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            url: The URL to send the request to
            headers: Optional HTTP headers
            json_data: Optional data to send. Will be serialised to XML when the
                negotiated content type is ``application/dicom+xml``.
            success_code: Expected HTTP status code for success

        Returns:
            tuple containing success status and either the response data or error message

        """
        retry_count = 0
        while retry_count <= self.max_retries:
            try:
                # Determine how to pass the body
                if json_data is not None and self.content_type == CONTENT_TYPE_XML:
                    body_bytes = serialize_request(json_data, self.content_type)
                    response = self.session.request(method, url, headers=headers, data=body_bytes, timeout=self.timeout)
                else:
                    response = self.session.request(method, url, headers=headers, json=json_data, timeout=self.timeout)

                for key, value in response.headers.items():
                    self.logger.debug(f"Response headers: {key}: {value}")
                # Check for warning headers
                warning_header = response.headers.get("Warning")
                if warning_header:
                    self.logger.debug(f"Server warning: {warning_header}")

                # Check response status
                if response.status_code == success_code:
                    self.logger.info(f"Request to {url} successful")
                    parsed_success = self._parse_success_response(response, url)
                    return True, parsed_success  # type: ignore[return-value]

                # Handle no content (204) and partial content (206) specially
                if response.status_code == 204:
                    return True, self._parse_no_content_response(response, url)

                if response.status_code == 206:
                    parsed = self._parse_partial_content_response(response)
                    if parsed is None:
                        return False, "Failed to parse partial content response"
                    return True, parsed

                # Error response handling
                brief_error_msg = f"Failed request to {url}. Status code: {response.status_code}"
                error_msg = self._build_error_message(brief_error_msg, response)

                if warning_header:
                    error_msg = f"{error_msg}, Warning: {warning_header}"

                # Don't retry client errors except timeout (408) and too many requests (429)
                if 400 <= response.status_code < 500 and response.status_code not in [408, 429]:
                    self.logger.error(error_msg)
                    return False, error_msg

                # For other errors, retry if we haven't exceeded max retries
                if retry_count < self.max_retries:
                    retry_count += 1
                    self.logger.warning(f"{brief_error_msg}. Retrying ({retry_count}/{self.max_retries})...")
                    self.logger.debug(f"Full error details: {error_msg}")
                    time.sleep(self.retry_delay * retry_count)  # Exponential backoff
                    continue
                else:
                    error_msg = f"{error_msg}. Max retries exceeded."
                    self.logger.error(f"{error_msg}")
                    return False, error_msg

            except requests.RequestException as e:
                error_msg = f"Request error: {str(e)}"

                if retry_count < self.max_retries:
                    retry_count += 1
                    self.logger.warning(f"{error_msg}. Retrying ({retry_count}/{self.max_retries})...")
                    time.sleep(self.retry_delay * retry_count)  # Exponential backoff
                    continue
                else:
                    error_msg = f"{error_msg}. Max retries exceeded."
                    self.logger.error(f"{error_msg}")
                    return False, error_msg

        # This should not be reached, but just in case
        return False, "Unknown error occurred during request"

    def _parse_success_response(self, response: requests.Response, url: str) -> dict[str, Any] | list[Any]:
        """
        Parse the body of a successful HTTP response.

        The parsing strategy is determined by the response ``Content-Type`` header:
        - ``application/dicom+xml`` → parse as a single DICOM XML document.
        - ``multipart/related`` → parse as multipart DICOM XML.
        - Anything else (including empty body) → JSON or a default success dict.

        After parsing, selected response headers (``Content-Location``,
        ``Location``, ``Warning``) are merged into the result dict when the
        parsed body is a dict.  When the body is a list (e.g. search results),
        these headers are not merged (they are not expected in that case).

        Args:
            response: The successful HTTP response object.
            url: The request URL, used only for log messages.

        Returns:
            A dict or list containing the parsed response body.
            When the body is a dict, selected response headers are also
            included as keys.

        """
        response_content_type = response.headers.get("Content-Type", "")

        parsed_result: dict[str, Any] | list[Any]

        if not response.text:
            parsed_result = {"status": "Success"}
        else:
            try:
                parsed = parse_response_body(response.content, response.text, response_content_type)
                # parse_response_body may return a Dataset or list[Dataset] for XML
                if isinstance(parsed, Dataset):
                    parsed_result = json.loads(parsed.to_json())
                elif isinstance(parsed, list) and parsed and isinstance(parsed[0], Dataset):
                    # Multipart XML search response — convert each Dataset to JSON-dict
                    parsed_result = [json.loads(ds.to_json()) for ds in parsed]
                elif isinstance(parsed, list):
                    parsed_result = parsed
                elif isinstance(parsed, dict):
                    parsed_result = parsed
                else:
                    parsed_result = {"data": parsed}
            except (json.JSONDecodeError, Exception):
                parsed_result = {"status": "Success", "response_text": response.text}

        # Add headers of interest — only when parsed_result is a dict
        if isinstance(parsed_result, dict):
            for header in ["Content-Location", "Location", "Warning"]:
                header_lower = header.lower()
                if header_lower in response.headers:
                    self.logger.info(f"Response header {header_lower}: {response.headers.get(header_lower)}")
                    parsed_result[header_lower.replace("-", "_")] = response.headers.get(header_lower)
                    snake_case_header = header_lower.replace("-", "_")
                    self.logger.info(
                        f"Added {header_lower} to result in {snake_case_header}: {parsed_result[snake_case_header]}"
                    )

        return parsed_result

    def _parse_no_content_response(self, response: requests.Response, url: str) -> dict[str, Any]:
        """
        Parse a 204 No Content response.

        Args:
            response: The HTTP response with status 204.
            url: The request URL, used only for log messages.

        Returns:
            A dict with ``status_code`` and ``message``, and optionally ``data``.

        """
        result: dict[str, Any] = {"status_code": response.status_code, "message": "No Content"}
        if response.text:
            try:
                result["data"] = json.loads(response.text)
            except json.JSONDecodeError:
                self.logger.warning(
                    "Unexpected: received non-JSON body in 204 No Content response from %s; body discarded",
                    url,
                )
        return result

    def _parse_partial_content_response(self, response: requests.Response) -> list[Any] | None:
        """
        Parse a 206 Partial Content response.

        Args:
            response: The HTTP response with status 206.

        Returns:
            A list of parsed results, or ``None`` if parsing fails.

        """
        response_content_type = response.headers.get("Content-Type", "")
        try:
            parsed = parse_response_body(response.content, response.text, response_content_type)
        except (json.JSONDecodeError, Exception):
            return None

        self.logger.info("Partial results received. There may be more results available.")
        if "Warning" in response.headers:
            self.logger.debug(f"Server warning: {response.headers['Warning']}")

        if isinstance(parsed, list):
            return parsed
        return [parsed]

    def _build_error_message(self, brief_error_msg: str, response: requests.Response) -> str:
        """
        Build a descriptive error message from a non-success HTTP response.

        Error responses from servers always use JSON (the server may return JSON
        error bodies even when XML was requested), so we attempt JSON parsing first
        and fall back to the raw response text.

        Args:
            brief_error_msg: Short message identifying the failing request.
            response: The HTTP error response object.

        Returns:
            A string describing the error, including any available details.

        """
        error_msg = brief_error_msg
        if response.text:
            try:
                error_details = json.loads(response.text)
                if error_details:
                    self.logger.debug(f"Response details: {error_details}")
                    error_msg += f". Error details: {error_details}"
                else:
                    error_msg += f". Response: {response.text}"
            except json.JSONDecodeError:
                error_msg = f"{error_msg}, Response: {response.text}"
            except ValueError:
                error_msg = f"{brief_error_msg}. Response: {response.text}"
        return error_msg

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
        self.logger.info(f"base_path {base_path}, endpoint_path {endpoint_path}")

        # Construct appropriate WebSocket path
        # Try to extract subscription ID if present
        path_segments = endpoint_path.split("/")
        if len(path_segments) > 2 and path_segments[-2] == "subscribers":
            subscription_id = path_segments[-1]
            ws_path = f"{base_path}/ws/subscribers/{subscription_id}"
        else:
            ws_path = f"{base_path}/ws"

        ws_url = urlunparse((ws_scheme, ws_netloc, ws_path, "", "", ""))
        self.logger.info(f"Constructed default WebSocket URL: {ws_url}")

        return ws_url

    def _send_subscription_request(self, endpoint: str) -> tuple[bool, dict[str, Any] | str]:
        """
        Send a subscription request to the server.

        Args:
            endpoint: The complete endpoint URL including query parameters

        Returns:
            tuple containing success status and either the response data or error message

        """
        success, response = self._send_request("POST", endpoint, success_code=201)
        self.logger.info(f"endpoint {endpoint}")
        if success and isinstance(response, dict):
            for key, value in response.items():
                self.logger.debug(f"{key}: {value}")

            # Extract WebSocket URL from Content-Location header that has been
            # converted to lower case and snake_case in _send_request()
            ws_url = response.get("content_location")

            if ws_url:
                self.logger.info(f"WebSocket URL from  response content_location: {ws_url}")

            # If we have a WebSocket URL override template, use it
            if self.websocket_url_override:
                # Replace {aetitle} placeholder if present.
                # A KeyError means the template contains an unknown placeholder name.
                try:
                    self.ws_url = self.websocket_url_override.format(aetitle=self.aetitle)
                except KeyError as exc:
                    raise UPSRSValidationError(
                        f"websocket_url_override template contains an unsupported placeholder: {exc}. "
                        f"Template: '{self.websocket_url_override}'. Only {{{{aetitle}}}} is supported."
                    ) from exc
                self.logger.info(f"Using WebSocket URL override: {self.ws_url}")
            elif ws_url:
                # Convert WebSocket URL to match the SSL configuration of the base URL
                from urllib.parse import urlparse, urlunparse

                base_parsed = urlparse(self.base_url)
                ws_parsed = urlparse(ws_url)

                # If base URL is HTTPS and WebSocket URL is WS, convert to WSS
                if base_parsed.scheme == "https" and ws_parsed.scheme == "ws":
                    ws_scheme = "wss"
                else:
                    ws_scheme = ws_parsed.scheme

                # Use the host and port from the base URL if they differ
                ws_host = ws_parsed.hostname
                ws_port = ws_parsed.port

                # If the base URL has a different host/port, use those instead
                if base_parsed.hostname != ws_parsed.hostname or base_parsed.port != ws_parsed.port:
                    ws_host = base_parsed.hostname
                    ws_port = base_parsed.port

                    # Build the netloc with the correct host/port
                    if ws_port and ws_port not in (80, 443):
                        ws_netloc = f"{ws_host}:{ws_port}"
                    else:
                        ws_netloc = ws_host
                else:
                    ws_netloc = ws_parsed.netloc

                # Reconstruct the WebSocket URL
                self.ws_url = urlunparse(
                    (ws_scheme, ws_netloc, ws_parsed.path, ws_parsed.params, ws_parsed.query, ws_parsed.fragment)
                )

                self.logger.info(f"WebSocket URL converted from {ws_url} to {self.ws_url}")
            else:
                self.ws_url = self._construct_default_websocket_url(self.base_url, endpoint)
                self.logger.info("No WebSocket URL provided in response, using constructed default")
            response["ws_url"] = self.ws_url

        return success, response

    def _send_unsubscription_request(self, endpoint: str) -> tuple[bool, dict[str, Any] | str]:
        """
        Send an unsubscription request to the server.

        Args:
            endpoint: The complete endpoint URL including query parameters

        Returns:
            tuple containing success status and either the response data or error message

        """
        return self._send_request("DELETE", endpoint)

    # ========== WebSocket Methods ==========

    def _run_websocket_thread(self) -> None:
        """WebSocket connection thread that runs the asyncio event loop."""
        # Create a new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Run the WebSocket client
            loop.run_until_complete(self._websocket_client())
        except Exception as e:
            self.logger.error(f"WebSocket thread error: {str(e)}")
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
            self.logger.info(f"Received event: {event_data}")

            # Convert JSON to DICOM Dataset
            ds = Dataset.from_json(message)

            # Log the relevant DICOM attributes from the event
            affected_sop_instance_uid = ds.AffectedSOPInstanceUID if hasattr(ds, "AffectedSOPInstanceUID") else "Unknown"
            event_type_id = ds.EventTypeID if hasattr(ds, "EventTypeID") else "Unknown"

            self.logger.info(f"UPS Event Type: {event_type_id} with Affected SOP Instance UID: {affected_sop_instance_uid}")

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
                    self.executor.submit(_run_callback)
            else:
                self.logger.warning("No event_callback assigned.  Check application level call to connect_websocket")

        except json.JSONDecodeError:
            self.logger.error(f"Failed to parse message as JSON: {message}")
        except Exception as e:
            self.logger.error(f"Error processing message: {str(e)}")

    async def _websocket_client(self) -> None:
        """Asynchronous WebSocket client implementation."""
        # Import websockets here to make it an optional dependency
        import ssl

        import websockets

        # Initial connection
        retry_delay = self.retry_delay  # seconds
        max_retries = self.max_retries
        retry_count = 0

        # Configure SSL context for WebSocket
        ssl_context: ssl.SSLContext | None = None
        if self.ws_url and self.ws_url.startswith("wss://"):
            ssl_context = ssl.create_default_context()

            if self.verify_ssl is False:
                # Disable SSL verification (use with caution)
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
            elif isinstance(self.verify_ssl, str):
                # Use custom CA bundle
                ssl_context.load_verify_locations(self.verify_ssl)

            # Load client certificate if provided
            if self.client_cert:
                if isinstance(self.client_cert, tuple):
                    ssl_context.load_cert_chain(self.client_cert[0], self.client_cert[1])
                else:
                    ssl_context.load_cert_chain(self.client_cert)

        while self.running:
            try:
                self.logger.info(f"Connecting to WebSocket: {self.ws_url}")

                # Pass SSL context to websockets.connect only when needed
                connect_kwargs: dict[str, Any] = {}
                if ssl_context:
                    connect_kwargs["ssl"] = ssl_context

                async with websockets.connect(self.ws_url, **connect_kwargs) as websocket:
                    self.ws_connection = websocket
                    self.logger.info("WebSocket connection established")
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
                            self.logger.warning(f"WebSocket connection closed: {e}")
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
                if retry_count > max_retries:
                    self.logger.error(f"Maximum retries exceeded. Last error: {e}")
                    self.running = False
                    break

                self.logger.info(f"Attempting to reconnect in {retry_delay} seconds... (Attempt {retry_count}/{max_retries})")
                await asyncio.sleep(retry_delay)

                # Exponential backoff for retry delay (capped at 60 seconds)
                retry_delay = min(retry_delay * 1.5, 60)

            except Exception as e:
                self.logger.error(f"Unexpected error: {str(e)}")
                if self.running:
                    self.logger.info(f"Attempting to reconnect in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                else:
                    break

        self.ws_connection = None
        self.logger.info("WebSocket client stopped")


# Keep backward-compatible entry points — the real implementations are in cli.py
def main() -> None:
    """Execute the CLI entry point.  Implementation lives in :mod:`dicom_ups_rs_client.cli`."""
    from dicom_ups_rs_client.cli import main as _main

    _main()


def _event_handler(event_data: dict[str, Any]) -> None:
    """
    Handle incoming UPS-RS events (re-exported from cli module for backward compatibility).

    Args:
        event_data: dictionary containing event information

    """
    from dicom_ups_rs_client.cli import _event_handler as _cli_event_handler

    _cli_event_handler(event_data)


if __name__ == "__main__":
    main()
