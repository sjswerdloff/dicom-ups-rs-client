"""DICOM UPS-RS Client."""

import argparse
import asyncio
import json
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Any

# from asyncio import Future
from urllib.parse import urlencode

import requests
from pydicom import Dataset, dcmread, uid
from pydicom.uid import generate_uid

__all__ = [
    "UPSRSClient",
    "UPSState",
    "InputReadinessState",
    "UPSRSError",
    "UPSRSResponseError",
    "UPSRSRequestError",
    "UPSRSValidationError",
]


class UPSState(Enum):
    """UPS Procedure Step States as defined in DICOM."""

    SCHEDULED = auto()
    IN_PROGRESS = auto()
    CANCELED = auto()
    COMPLETED = auto()

    def __str__(self) -> str:
        """Return string representation for UPS-RS protocol."""
        return self.name.replace("_", " ")


class InputReadinessState(Enum):
    """UPS Input Readiness States as defined in DICOM."""

    READY = auto()
    UNAVAILABLE = auto()
    INCOMPLETE = auto()

    def __str__(self) -> str:
        """Return string representation for UPS-RS protocol."""
        return self.name


class UPSRSError(Exception):
    """Base exception class for UPS-RS client errors."""

    pass


class UPSRSResponseError(UPSRSError):
    """Exception raised for errors in the response from the UPS-RS server."""

    def __init__(self, message: str, status_code: int, response_text: str | None = None) -> None:
        """
        Initialize the exception.

        Args:
            message (str): _description_
            status_code (int): _description_
            response_text (str | None, optional): _description_. Defaults to None.

        """
        self.status_code = status_code
        self.response_text = response_text
        super().__init__(message)


class UPSRSRequestError(UPSRSError):
    """Exception raised for errors in making requests to the UPS-RS server."""

    pass


class UPSRSValidationError(UPSRSError):
    """Exception raised for validation errors in client inputs."""

    pass


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

        """
        self.base_url = base_url.rstrip("/")
        self.aetitle = aetitle
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # Set up logging
        self.logger = logger or logging.getLogger("ups_rs_client")
        if not logger:
            self.logger.setLevel(logging.DEBUG)
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

        # Session for connection pooling
        self.session = requests.Session()

        # Thread pool for async operations
        self.executor = ThreadPoolExecutor(max_workers=5)

    def __enter__(self):  # noqa: ANN204
        """Enter the runtime context for this client."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):  # noqa: ANN001, ANN204
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

        # Set headers
        headers = {
            "Content-Type": "application/dicom+json",
            "Accept": "application/dicom+json",
        }

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
        headers = {"Accept": "application/dicom+json", "Cache-Control": "no-cache"}

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

        # Set headers
        headers = {"Accept": "application/dicom+json"}

        # Add Cache-Control header if no_cache is True
        if no_cache:
            headers["Cache-Control"] = "no-cache"

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

        # Set headers
        headers = {
            "Content-Type": "application/dicom+json",
            "Accept": "application/dicom+json",
        }

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

        # Set headers
        headers = {
            "Content-Type": "application/dicom+json",
            "Accept": "application/dicom+json",
        }

        # Prepare payload
        payload = {
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
        payload = {}

        # Add Reason For Cancellation (0074,1238) if provided
        if reason:
            payload["00741238"] = {"vr": "LT", "Value": [reason]}

        # Add Contact Display Name (0074,100E) if provided
        if contact_name:
            payload["0074100E"] = {"vr": "PN", "Value": [contact_name]}

        # Add Contact URI (0074,100F) if provided
        if contact_uri:
            payload["0074100F"] = {"vr": "UT", "Value": [contact_uri]}

        # Set headers
        headers = {
            "Content-Type": "application/dicom+json",
            "Accept": "application/dicom+json",
        }

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
            lambda: self.search_workitems(
                match_parameters,
                include_fields,
                fuzzy_matching,
                offset,
                limit,
                no_cache,
            ),
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
            transaction_uid: Transaction UID (required for updates to IN PROGRESS workitems)
            update_data: dictionary containing the workitem attributes to update

        Returns:
            tuple containing success status and either the response data or error message

        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self.executor,
            lambda: self.update_workitem(workitem_uid, transaction_uid, update_data),
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
            workitem_uid: UID of the workitem to change
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
        headers: dict[str, str] = None,
        json_data: Any = None,  # noqa: ANN401
        success_code: int = 200,
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Send an HTTP request to the UPS-RS server with retry logic.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            url: The URL to send the request to
            headers: Optional HTTP headers
            json_data: Optional JSON data to send
            success_code: Expected HTTP status code for success

        Returns:
            tuple containing success status and either the response data or error message

        """
        retry_count = 0
        while retry_count <= self.max_retries:
            try:
                response = self.session.request(method, url, headers=headers, json=json_data, timeout=self.timeout)

                # Check for warning headers
                warning_header = response.headers.get("Warning")
                if warning_header:
                    self.logger.warning(f"Server warning: {warning_header}")

                # Check response status
                if response.status_code == success_code:
                    self.logger.info(f"Request to {url} successful")

                    try:
                        result = response.json() if response.text else {"status": "Success"}
                    except json.JSONDecodeError:
                        result = {"status": "Success", "response_text": response.text}

                    # Add headers of interest
                    for header in ["Content-Location", "Location", "Warning"]:
                        header_lower = header.lower()
                        if header_lower in response.headers:
                            result[header_lower.replace("-", "_")] = response.headers.get(header_lower)

                    return True, result

                # Handle no content (204) and partial content (206) specially
                elif response.status_code == 204:
                    result = {"status_code": response.status_code}
                    result["message"] = "No Content"
                    try:
                        if response.text:
                            result["data"] = response.json()
                    except json.JSONDecodeError:
                        pass

                    return True, result
                # For partial content (status code 206)
                elif response.status_code == 206:
                    try:
                        # Parse the JSON response
                        result_list = response.json()

                        # Log warning about partial results
                        self.logger.info("Partial results received. There may be more results available.")
                        if "Warning" in response.headers:
                            self.logger.warning(f"Server warning: {response.headers['Warning']}")

                        # Return just the list of results (to match 200 OK behavior)
                        return True, result_list
                    except json.JSONDecodeError:
                        # Handle parsing error
                        return False, "Failed to parse partial content response"

                else:
                    error_msg = f"Failed request to {url}. Status code: {response.status_code}"
                    if response.text:
                        try:
                            error_details = response.json() if response.text else {}
                            if error_details:
                                error_msg += f". Error details: {error_details}"
                            else:
                                error_msg += f". Response: {response.text}"
                            # return False, error_msg
                        except json.JSONDecodeError:
                            error_msg = f"{error_msg}, Response: {response.text}"
                            #  return False, error_msg
                        except ValueError:
                            # Handle case where error response is not JSON
                            error_msg = (
                                f"Failed request to {url}. Status code: {response.status_code}. Response: {response.text}"
                            )
                        # return False, error_msg

                    if warning_header:
                        error_msg = f"{error_msg}, Warning: {warning_header}"

                    # Don't retry client errors except timeout (408) and too many requests (429)
                    if 400 <= response.status_code < 500 and response.status_code not in [408, 429]:
                        self.logger.error(error_msg)
                        return False, error_msg

                    # For other errors, retry if we haven't exceeded max retries
                    if retry_count < self.max_retries:
                        retry_count += 1
                        self.logger.warning(f"{error_msg}. Retrying ({retry_count}/{self.max_retries})...")
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

    def _send_subscription_request(self, endpoint: str) -> tuple[bool, dict[str, Any] | str]:
        """
        Send a subscription request to the server.

        Args:
            endpoint: The complete endpoint URL including query parameters

        Returns:
            tuple containing success status and either the response data or error message

        """
        success, response = self._send_request("POST", endpoint, success_code=201)

        if success and isinstance(response, dict):
            # Extract WebSocket URL from Content-Location header
            self.ws_url = response.get("content_location")
            if not self.ws_url:
                self.logger.warning("No WebSocket URL provided in response")
            else:
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

            # Call user-provided event callback if it exists
            if self.event_callback:
                # Call the callback in the main thread to avoid threading issues
                if threading.current_thread() is threading.main_thread():
                    self.event_callback(event_data)
                else:
                    # Schedule the callback to run in the main thread
                    threading.Thread(target=self.event_callback, args=(event_data,)).start()
            else:
                self.logger.warning("No event_callback assigned.  Check application level call to connect_websocket")

        except json.JSONDecodeError:
            self.logger.error(f"Failed to parse message as JSON: {message}")
        except Exception as e:
            self.logger.error(f"Error processing message: {str(e)}")

    async def _websocket_client(self) -> None:
        """Asynchronous WebSocket client implementation."""
        # Import websockets here to make it an optional dependency
        import websockets

        # Initial connection
        retry_delay = 5  # seconds
        max_retries = 10
        retry_count = 0

        while self.running:
            try:
                self.logger.info(f"Connecting to WebSocket: {self.ws_url}")

                async with websockets.connect(self.ws_url) as websocket:
                    self.ws_connection = websocket
                    self.logger.info("WebSocket connection established")
                    retry_count = 0  # Reset retry counter on successful connection

                    # Keep receiving messages until connection is closed
                    while self.running:
                        try:
                            message = await websocket.recv()
                            await self._handle_message(message)
                        except websockets.exceptions.ConnectionClosed as e:
                            self.logger.warning(f"WebSocket connection closed: {e}")
                            break

            except (
                websockets.exceptions.WebSocketException,
                ConnectionRefusedError,
            ) as e:
                if not self.running:
                    break  # Exit if we're shutting down

                retry_count += 1
                self.logger.error(f"WebSocket connection error: {str(e)}")

                if retry_count >= max_retries:
                    self.logger.error(f"Maximum retries ({max_retries}) reached. Giving up.")
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


def _event_handler(event_data: dict[str, Any]) -> None:
    """
    Handle incoming UPS-RS events.

    Args:
        event_data: dictionary containing event information

    """
    try:
        event_type_id = event_data.get("00001002", {}).get("Value", ["unknown"])[0]
        affected_sop_instance_uid = event_data.get("00001000", {}).get("Value", ["unknown"])[0]
        print(f"\nEVENT RECEIVED: {event_type_id} - Workitem: {affected_sop_instance_uid}")
    except (KeyError, IndexError):
        print("\nEVENT RECEIVED: (unable to extract event type or workitem UID)")

    print(json.dumps(event_data, indent=2))
    print("-" * 60)


def main() -> None:
    """Execute Main CLI entry point for UPS-RS client."""
    parser = argparse.ArgumentParser(description="DICOM UPS-RS Client")
    parser.add_argument(
        "--server",
        type=str,
        required=True,
        help="URL of the UPS-RS server (e.g., http://localhost:5000)",
    )
    parser.add_argument(
        "--aetitle",
        type=str,
        help="Application Entity Title for subscription operations",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout in seconds")
    parser.add_argument("--max-retries", type=int, default=3, help="Maximum number of request retries")

    # Create subparsers for different commands
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Create workitem command
    create_parser = subparsers.add_parser("create", help="Create a new workitem")
    create_parser.add_argument("--workitem-uid", type=str, help="Optional UID for the workitem")
    create_parser.add_argument("--input-file", type=str, help="JSON file containing workitem data")
    create_parser.add_argument("--input-dcm", type=str, help="DICOM file containing workitem data")

    # Retrieve workitem command
    retrieve_parser = subparsers.add_parser("retrieve", help="Retrieve a workitem")
    retrieve_parser.add_argument(
        "--workitem-uid",
        type=str,
        required=True,
        help="UID of the workitem to retrieve",
    )
    retrieve_parser.add_argument(
        "--output-file",
        type=str,
        help="Output file to save the retrieved workitem JSON",
    )

    # Search workitems command
    search_parser = subparsers.add_parser("search", help="Search for workitems")
    search_parser.add_argument(
        "--match",
        action="append",
        help="Match parameters (e.g., '00741000=SCHEDULED')",
        default=[],
    )
    search_parser.add_argument(
        "--includefield",
        action="append",
        help="Fields to include in results",
        default=[],
    )
    search_parser.add_argument("--fuzzy", action="store_true", help="Enable fuzzy matching")
    search_parser.add_argument(
        "--state",
        choices=["SCHEDULED", "IN PROGRESS", "CANCELED", "COMPLETED"],
        help="Filter by Procedure Step State (00741000)",
    )
    search_parser.add_argument(
        "--readiness",
        choices=["READY", "UNAVAILABLE", "INCOMPLETE"],
        help="Filter by Input Readiness State (00404041)",
    )
    search_parser.add_argument(
        "--start-date",
        type=str,
        help="Filter by Scheduled Start Date (00404005) in YYYYMMDD format",
    )
    search_parser.add_argument("--label", type=str, help="Filter by Procedure Step Label (00741204)")
    search_parser.add_argument("--offset", type=int, default=0, help="Starting position of results")
    search_parser.add_argument("--limit", type=int, help="Maximum number of results to return")
    search_parser.add_argument("--no-cache", action="store_true", help="Request non-cached results")
    search_parser.add_argument("--output-file", type=str, help="Output file to save search results")
    search_parser.add_argument("--summary", action="store_true", help="Display only a summary of results")
    search_parser.add_argument(
        "--display-fields",
        type=str,
        help="Comma-separated list of fields to display in output summary",
    )

    # Update workitem command
    update_parser = subparsers.add_parser("update", help="Update a workitem")
    update_parser.add_argument("--workitem-uid", type=str, required=True, help="UID of the workitem to update")
    update_parser.add_argument("--transaction-uid", type=str, help="Transaction UID")
    update_parser.add_argument("--input-file", type=str, help="JSON file containing update data")
    update_parser.add_argument("--procedure-label", type=str, help="Set the Procedure Step Label (0074,1204)")
    update_parser.add_argument(
        "--procedure-description",
        type=str,
        help="Set the Procedure Step Description (0040,0007)",
    )

    # Change workitem state command
    state_parser = subparsers.add_parser("change-state", help="Change workitem state")
    state_parser.add_argument(
        "--workitem-uid",
        type=str,
        required=True,
        help="UID of the workitem to change state",
    )
    state_parser.add_argument(
        "--state",
        type=str,
        required=True,
        choices=["IN PROGRESS", "COMPLETED", "CANCELED"],
        help="New state for the workitem",
    )
    state_parser.add_argument(
        "--transaction-uid",
        type=str,
        help="Transaction UID (required for COMPLETED/CANCELED states, optional for IN PROGRESS)",
    )

    # Request cancellation command
    cancel_parser = subparsers.add_parser("request-cancel", help="Request cancellation of a workitem")
    cancel_parser.add_argument(
        "--workitem-uid",
        type=str,
        required=True,
        help="UID of the workitem to request cancellation for",
    )
    cancel_parser.add_argument("--reason", type=str, help="Reason for the cancellation request")
    cancel_parser.add_argument("--contact-name", type=str, help="Display name of the contact person")
    cancel_parser.add_argument(
        "--contact-uri",
        type=str,
        help="URI for contacting the requestor (e.g., mailto:user@example.com)",
    )

    # Subscribe command
    subscribe_parser = subparsers.add_parser("subscribe", help="Subscribe to workitem events")
    subscribe_group = subscribe_parser.add_mutually_exclusive_group(required=True)
    subscribe_group.add_argument("--worklist", action="store_true", help="Subscribe to the entire worklist")
    subscribe_group.add_argument(
        "--filtered-worklist",
        action="store_true",
        help="Subscribe to a filtered worklist",
    )
    subscribe_group.add_argument("--workitem", type=str, help="UID of a specific workitem to subscribe to")
    subscribe_parser.add_argument(
        "--filter",
        action="append",
        help="Filter parameters for filtered worklist (e.g., '00741000=SCHEDULED')",
        default=[],
    )
    subscribe_parser.add_argument(
        "--deletion-lock",
        action="store_true",
        help="Request deletion lock for the subscription",
    )
    subscribe_parser.add_argument(
        "--monitor",
        action="store_true",
        help="Monitor for event notifications after subscribing",
    )

    # Unsubscribe command
    unsubscribe_parser = subparsers.add_parser("unsubscribe", help="Unsubscribe from workitem events")
    unsubscribe_group = unsubscribe_parser.add_mutually_exclusive_group(required=True)
    unsubscribe_group.add_argument("--worklist", action="store_true", help="Unsubscribe from the entire worklist")
    unsubscribe_group.add_argument(
        "--filtered-worklist",
        action="store_true",
        help="Unsubscribe from a filtered worklist",
    )
    unsubscribe_group.add_argument("--workitem", type=str, help="UID of a specific workitem to unsubscribe from")
    unsubscribe_parser.add_argument(
        "--filter",
        action="append",
        help="Filter parameters for filtered worklist (e.g., '00741000=SCHEDULED')",
        default=[],
    )
    unsubscribe_parser.add_argument(
        "--deletion-lock",
        action="store_true",
        help="Request deletion lock for the unsubscription",
    )

    args = parser.parse_args()

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Check for required command
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Initialize client
    client = UPSRSClient(
        base_url=args.server,
        aetitle=args.aetitle,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )

    try:
        # Execute the requested command
        if args.command == "create":
            _handle_create_command(client, args)
        elif args.command == "retrieve":
            _handle_retrieve_command(client, args)
        elif args.command == "search":
            _handle_search_command(client, args)
        elif args.command == "update":
            _handle_update_command(client, args)
        elif args.command == "change-state":
            _handle_change_state_command(client, args)
        elif args.command == "request-cancel":
            _handle_cancel_request_command(client, args)
        elif args.command == "subscribe":
            _handle_subscribe_command(client, args)
        elif args.command == "unsubscribe":
            _handle_unsubscribe_command(client, args)
    finally:
        # Ensure proper cleanup
        client.close()


def _handle_create_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the create workitem command."""
    # Generate a DICOM UID if one wasn't provided on the command line
    workitem_uid = args.workitem_uid or str(generate_uid())

    # Load workitem data if provided
    workitem_data = None
    if args.input_file:
        try:
            with open(args.input_file) as f:
                workitem_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to load workitem data from {args.input_file}: {str(e)}")
            sys.exit(1)

    if args.input_dcm:
        try:
            dcm_path = Path(args.input_dcm)
            local_json_path = Path(dcm_path.name.removesuffix("dcm") + "json")

            workitem_ds = dcmread(args.input_dcm)
            # Make sure the workitem_uid is consistent in the post and the data
            if not args.workitem_uid:
                workitem_uid = str(workitem_ds.SOPInstanceUID)
            else:
                workitem_ds.SOPInstanceUID = workitem_uid
            workitem_uid = workitem_uid or str(generate_uid())

            workitem_data = json.loads(workitem_ds.to_json())
            local_json_path.write_text(json.dumps(workitem_data, indent=2))

        except Exception as e:
            logging.error(f"Failed to load workitem data from {args.input_dcm}: {str(e)}")
            sys.exit(1)

    # Create workitem
    success, response = client.create_workitem(workitem_data, workitem_uid)

    if success:
        print("Workitem created successfully")
        print(json.dumps(response, indent=2))
        print(f"Workitem UID: {workitem_uid}")
        sys.exit(0)
    else:
        print(f"Failed to create workitem: {response}")
        sys.exit(1)


def _handle_retrieve_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the retrieve workitem command."""
    success, response = client.retrieve_workitem(args.workitem_uid)

    if success:
        print("Workitem retrieved successfully")
        formatted_response = json.dumps(response, indent=2)
        print(formatted_response)

        # Save to file if requested
        if args.output_file:
            try:
                with open(args.output_file, "w") as f:
                    f.write(formatted_response)
                print(f"Workitem saved to {args.output_file}")
            except Exception as e:
                print(f"Failed to save workitem to file: {str(e)}")
                sys.exit(1)

        sys.exit(0)
    else:
        print(f"Failed to retrieve workitem: {response}")
        sys.exit(1)


def _handle_search_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the search workitems command."""
    # Parse match parameters
    match_parameters = {}
    for param in args.match:
        if "=" in param:
            key, value = param.split("=", 1)
            match_parameters[key] = value
        else:
            logging.warning(f"Ignoring invalid match parameter (missing '='): {param}")

    # Add common search parameters if provided
    if args.state:
        match_parameters["00741000"] = args.state

    if args.readiness:
        match_parameters["00404041"] = args.readiness

    if args.start_date:
        match_parameters["00404005"] = args.start_date

    if args.label:
        match_parameters["00741204"] = args.label

    # Inform user about search criteria
    _summarize_search_criteria(match_parameters)

    # Perform search
    success, response = client.search_workitems(
        match_parameters,
        args.includefield,
        args.fuzzy,
        args.offset,
        args.limit,
        args.no_cache,
    )

    if not success:
        print(f"Failed to search workitems: {response}")
        sys.exit(1)

    if isinstance(response, list) and response:
        result_count = len(response)
        print(f"Search returned {result_count} result(s)")

        # Display summary if requested or full results
        if args.summary:
            _summarize_search_results(args, response)
        else:
            # Print full formatted results
            formatted_response = json.dumps(response, indent=2)
            print(formatted_response)

        # Save to file if requested
        if args.output_file:
            try:
                with open(args.output_file, "w") as f:
                    f.write(json.dumps(response, indent=2))
                print(f"Search results saved to {args.output_file}")
            except Exception as e:
                print(f"Failed to save search results to file: {str(e)}")
                sys.exit(1)
    else:
        print("No matching workitems found")

    sys.exit(0)


def _summarize_search_criteria(match_parameters: dict[str, str]) -> None:
    """Print a summary of the search criteria."""
    if match_parameters:
        print("Searching with criteria:")
        for tag, value in match_parameters.items():
            tag_name = ""
            if tag == "00741000":
                tag_name = "Procedure Step State"
            elif tag == "00404041":
                tag_name = "Input Readiness State"
            elif tag == "00404005":
                tag_name = "Scheduled Start Date"
            elif tag == "00741204":
                tag_name = "Procedure Step Label"

            if tag_name:
                print(f"  {tag_name} ({tag}) = {value}")
            else:
                print(f"  {tag} = {value}")
    else:
        print("Searching with no criteria (will return all workitems)")


def _summarize_search_results(args: argparse.Namespace, response: list[dict[str, Any]]) -> None:
    """Print a summary of the search results."""
    print("\nSummary of Workitems:")
    print("-" * 80)

    # Determine which fields to display in summary
    display_fields = []
    display_fields = (
        args.display_fields.split(",") if args.display_fields else ["00080018", "00741000", "00404041", "00741204"]
    )
    # Print header
    header_row = []
    for field in display_fields:
        if field == "00080018":
            header_row.append("SOP Instance UID")
        elif field == "00404005":
            header_row.append("Scheduled Start")
        elif field == "00404041":
            header_row.append("Input Readiness")
        elif field == "00741000":
            header_row.append("Procedure Step State")
        elif field == "00741204":
            header_row.append("Procedure Label")
        else:
            header_row.append(field)

    print(" | ".join(header_row))
    print("-" * 80)

    # Print each workitem
    for wi in response:
        row = []
        for field in display_fields:
            if field in wi and "Value" in wi[field] and wi[field]["Value"]:
                value = wi[field]["Value"][0]
                # Truncate long values
                if isinstance(value, str) and len(value) > 30:
                    value = f"{value[:27]}..."
                row.append(str(value))
            else:
                row.append("N/A")

        print(" | ".join(row))

    print("-" * 80)


def _handle_update_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the update workitem command."""
    # Prepare update data
    update_data = {}

    # Load from file if provided
    if args.input_file:
        try:
            with open(args.input_file) as f:
                update_data = json.load(f)
        except Exception as e:
            logging.error(f"Failed to load update data from {args.input_file}: {str(e)}")
            sys.exit(1)

    # Add command line attributes if provided
    if args.procedure_label:
        update_data["00741204"] = {"vr": "LO", "Value": [args.procedure_label]}

    if args.procedure_description:
        update_data["00400007"] = {"vr": "LO", "Value": [args.procedure_description]}

    if not args.transaction_uid:
        print("Transaction UID not provided, only valid if UPS is SCHEDULED")

    # Ensure we have some update data
    if not update_data:
        logging.error("No update data provided. Use --input-file or command line options.")
        sys.exit(1)

    # Update workitem
    success, response = client.update_workitem(args.workitem_uid, args.transaction_uid, update_data)

    if success:
        print("Workitem updated successfully")

        # Display any warnings
        if isinstance(response, dict) and "warning" in response:
            print(f"Warning: {response['warning']}")

        # Display full response if available and verbose
        if args.verbose and isinstance(response, dict) and "response" in response:
            print("\nFull response:")
            print(json.dumps(response["response"], indent=2))

        sys.exit(0)
    else:
        print(f"Failed to update workitem: {response}")
        sys.exit(1)


def _handle_change_state_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the change workitem state command."""
    # Change workitem state
    success, response = client.change_workitem_state(args.workitem_uid, args.state, args.transaction_uid)

    if success:
        print(f"Workitem state changed successfully to {args.state}")

        # Display transaction UID for future reference
        if isinstance(response, dict) and "transaction_uid" in response:
            print(f"Transaction UID: {response['transaction_uid']}")
            print("Keep this UID for future state changes to this workitem")

        # Display any warnings
        if isinstance(response, dict) and "warning" in response:
            print(f"Warning: {response['warning']}")

        # Display full response if verbose
        if args.verbose and isinstance(response, dict) and "response" in response:
            print("\nFull response:")
            print(json.dumps(response["response"], indent=2))

        sys.exit(0)
    else:
        print(f"Failed to change workitem state: {response}")
        sys.exit(1)


def _handle_cancel_request_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the request cancellation command."""
    # Request cancellation
    success, response = client.request_cancellation(args.workitem_uid, args.reason, args.contact_name, args.contact_uri)

    if not success:
        print(f"Failed to request cancellation: {response}")
        sys.exit(1)

    print("Cancellation request sent successfully")

    # Display any warnings
    if isinstance(response, dict) and "warning" in response:
        print(f"Warning: {response['warning']}")

    # Display full response if available and verbose
    if args.verbose and isinstance(response, dict) and "response" in response:
        print("\nFull response:")
        print(json.dumps(response["response"], indent=2))

    # Include note about processing
    print("\nNote: The cancellation request has been accepted by the server, but the workitem")
    print("owner is not obliged to honor the request and may not receive notification.")

    sys.exit(0)


def _handle_subscribe_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the subscribe command."""
    # Check if AE Title is provided
    if not args.aetitle:
        print("Error: AE Title (--aetitle) is required for subscription operations")
        sys.exit(1)

    # Handle different subscription types
    if args.worklist:
        success, response = client.subscribe_to_worklist(args.deletion_lock)
        subscription_type = "worklist"
    elif args.filtered_worklist:
        # Parse filter parameters
        filter_params = {}
        for param in args.filter:
            if "=" in param:
                key, value = param.split("=", 1)
                filter_params[key] = value
            else:
                logging.warning(f"Ignoring invalid filter parameter (missing '='): {param}")

        if not filter_params:
            logging.error("Filtered worklist subscription requires at least one filter parameter")
            sys.exit(1)

        success, response = client.subscribe_to_filtered_worklist(filter_params, args.deletion_lock)
        subscription_type = "filtered worklist"
    else:  # workitem
        success, response = client.subscribe_to_workitem(args.workitem, args.deletion_lock)
        subscription_type = f"workitem {args.workitem}"

    if success:
        print(f"Successfully subscribed to {subscription_type}")

        # Display WebSocket URL if available
        if isinstance(response, dict) and "ws_url" in response:
            print(f"WebSocket URL: {response['ws_url']}")

        # Display any warnings
        if isinstance(response, dict) and "warning" in response:
            print(f"Warning: {response['warning']}")

        # Start monitoring if requested
        if args.monitor:
            print("\nStarting event monitoring. Press Ctrl+C to stop.")

            # Set up signal handler for graceful shutdown
            def signal_handler(sig, frame) -> None:  # noqa: ANN001
                print("\nShutting down...")
                client.disconnect()
                sys.exit(0)

            signal.signal(signal.SIGINT, signal_handler)

            # Connect to WebSocket and start receiving events
            # assign a callback to perform application specific processing
            client.connect_websocket(event_callback=_event_handler)

            # Keep the main thread alive
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                client.disconnect()

        sys.exit(0)
    else:
        print(f"Failed to subscribe to {subscription_type}: {response}")
        sys.exit(1)


def _handle_unsubscribe_command(client: UPSRSClient, args: argparse.Namespace) -> None:
    """Handle the unsubscribe command."""
    # Check if AE Title is provided
    if not args.aetitle:
        print("Error: AE Title (--aetitle) is required for subscription operations")
        sys.exit(1)

    # Handle different subscription types
    if args.worklist:
        success, response = client.unsubscribe_from_worklist(args.deletion_lock)
        subscription_type = "worklist"
    elif args.filtered_worklist:
        # Parse filter parameters
        filter_params = {}
        for param in args.filter:
            if "=" in param:
                key, value = param.split("=", 1)
                filter_params[key] = value
            else:
                logging.warning(f"Ignoring invalid filter parameter (missing '='): {param}")

        if not filter_params:
            logging.error("Filtered worklist subscription requires at least one filter parameter")
            sys.exit(1)

        success, response = client.unsubscribe_from_filtered_worklist(filter_params, args.deletion_lock)
        subscription_type = "filtered worklist"
    else:  # workitem
        success, response = client.unsubscribe_from_workitem(args.workitem, args.deletion_lock)
        subscription_type = f"workitem {args.workitem}"

    if success:
        print(f"Successfully unsubscribed from {subscription_type}")

        # Display any warnings
        if isinstance(response, dict) and "warning" in response:
            print(f"Warning: {response['warning']}")

        sys.exit(0)
    else:
        print(f"Failed to unsubscribe from {subscription_type}: {response}")
        sys.exit(1)


if __name__ == "__main__":
    main()
