"""DICOM UPS-RS Client."""

import logging
import time
from typing import Any
from urllib.parse import urlencode

import requests
from pydicom.uid import generate_uid

from dicom_ups_rs_client.async_operations import AsyncOperationsMixin
from dicom_ups_rs_client.enums import InputReadinessState, UPSState
from dicom_ups_rs_client.event_management import EventManagementMixin
from dicom_ups_rs_client.exceptions import UPSRSError, UPSRSRequestError, UPSRSResponseError, UPSRSValidationError
from dicom_ups_rs_client.response_parsing import ResponseParsingMixin
from dicom_ups_rs_client.serialization import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_XML,
    make_headers,
    serialize_request,
)
from dicom_ups_rs_client.websocket import WebSocketMixin
from dicom_ups_rs_client.workitem_utils import WorkitemUtilsMixin

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


class UPSRSClient(WebSocketMixin, AsyncOperationsMixin, EventManagementMixin, ResponseParsingMixin, WorkitemUtilsMixin):
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

        # Initialize WebSocket state from mixin
        self._init_websocket()

        # Session for connection pooling
        self.session = requests.Session()
        self.session.verify = self.verify_ssl
        if self.client_cert:
            self.session.cert = self.client_cert

        # Thread pool for async operations
        from concurrent.futures import ThreadPoolExecutor

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

    # ========== Utility Methods ==========

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
