"""Event management mixin for DICOM UPS-RS Client."""

from typing import Any
from urllib.parse import urlencode

from dicom_ups_rs_client.exceptions import UPSRSValidationError


class EventManagementMixin:
    """
    Mixin providing UPS-RS subscription and event management operations.

    This mixin implements the subscribe/unsubscribe operations for UPS-RS
    worklist and workitem event notifications.  All methods delegate to
    ``self._send_request``, ``self.validate_uid``, and the WebSocket URL
    helpers provided by WebSocketMixin.
    """

    def subscribe_to_worklist(self, deletion_lock: bool = False) -> tuple[bool, dict[str, Any] | str]:
        """
        Subscribe to all workitems in the worklist.

        Args:
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:  # type: ignore[attr-defined]
            return False, "AE Title is required for subscription operations"

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/1.2.840.10008.5.1.4.34.5/subscribers/{self.aetitle}"  # type: ignore[attr-defined]

        # Add deletion lock parameter if requested
        if deletion_lock:
            endpoint += "?deletionlock=true"

        return self._send_subscription_request(endpoint)  # type: ignore[attr-defined]

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
        if not self.aetitle:  # type: ignore[attr-defined]
            return False, "AE Title is required for subscription operations"

        # Build filter parameter string per DICOM PS3.18: KeyValuePair[,KeyValuePair]*
        filter_str = ",".join([f"{key}={value}" for key, value in filter_params.items()])

        # Set endpoint URL with filter parameter. urlencode percent-encodes reserved
        # characters in the value; the server URL-decodes before parsing DICOM syntax.
        endpoint = f"{self.base_url}/workitems/1.2.840.10008.5.1.4.34.5.1/subscribers/{self.aetitle}"  # type: ignore[attr-defined]
        params: dict[str, str] = {"filter": filter_str}
        if deletion_lock:
            params["deletionlock"] = "true"
        endpoint += "?" + urlencode(params)

        return self._send_subscription_request(endpoint)  # type: ignore[attr-defined]

    def subscribe_to_workitem(self, workitem_uid: str, deletion_lock: bool = False) -> tuple[bool, dict[str, Any] | str]:
        """
        Subscribe to a specific workitem.

        Args:
            workitem_uid: UID of the workitem to subscribe to
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:  # type: ignore[attr-defined]
            return False, "AE Title is required for subscription operations"

        # Validate the DICOM UID format
        if not self.validate_uid(workitem_uid):  # type: ignore[attr-defined]
            return False, f"Invalid DICOM UID format for workitem_uid: {workitem_uid}"

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/{workitem_uid}/subscribers/{self.aetitle}"  # type: ignore[attr-defined]

        # Add deletion lock parameter if requested
        if deletion_lock:
            endpoint += "?deletionlock=true"

        return self._send_subscription_request(endpoint)  # type: ignore[attr-defined]

    def unsubscribe_from_worklist(self, deletion_lock: bool = False) -> tuple[bool, dict[str, Any] | str]:
        """
        Unsubscribe from all workitems in the worklist.

        Args:
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:  # type: ignore[attr-defined]
            return False, "AE Title is required for subscription operations"

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/1.2.840.10008.5.1.4.34.5/subscribers/{self.aetitle}"  # type: ignore[attr-defined]

        # Add deletion lock parameter if requested
        if deletion_lock:
            endpoint += "?deletionlock=true"

        return self._send_unsubscription_request(endpoint)  # type: ignore[attr-defined]

    def unsubscribe_from_filtered_worklist(
        self, filter_params: dict[str, str] | None = None, deletion_lock: bool = False
    ) -> tuple[bool, dict[str, Any] | str]:
        """
        Unsubscribe from workitems matching the specified filter criteria.

        Per PS3.18 §11.10, the DELETE on /workitems/{uid}/subscribers/{AET}
        identifies the subscription by the subscriber AE Title; the filter
        parameters are not required on unsubscribe.

        Args:
            filter_params: Optional dictionary of filter parameters. Servers do
                not need these to identify the subscription on DELETE, but some
                may echo them for logging. Omit (default) for a plain unsubscribe.
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:  # type: ignore[attr-defined]
            return False, "AE Title is required for subscription operations"

        # Set endpoint URL. urlencode percent-encodes reserved characters in the
        # filter value; the server URL-decodes before parsing DICOM syntax.
        endpoint = f"{self.base_url}/workitems/1.2.840.10008.5.1.4.34.5.1/subscribers/{self.aetitle}"  # type: ignore[attr-defined]

        params: dict[str, str] = {}
        if filter_params:
            params["filter"] = ",".join([f"{key}={value}" for key, value in filter_params.items()])
        if deletion_lock:
            params["deletionlock"] = "true"
        if params:
            endpoint += "?" + urlencode(params)

        return self._send_unsubscription_request(endpoint)  # type: ignore[attr-defined]

    def unsubscribe_from_workitem(self, workitem_uid: str, deletion_lock: bool = False) -> tuple[bool, dict[str, Any] | str]:
        """
        Unsubscribe from a specific workitem.

        Args:
            workitem_uid: UID of the workitem to unsubscribe from
            deletion_lock: Whether to request a deletion lock for the subscription

        Returns:
            tuple containing success status and either the response data or error message

        """
        if not self.aetitle:  # type: ignore[attr-defined]
            return False, "AE Title is required for subscription operations"

        # Validate the DICOM UID format
        if not self.validate_uid(workitem_uid):  # type: ignore[attr-defined]
            return False, f"Invalid DICOM UID format for workitem_uid: {workitem_uid}"

        # Set endpoint URL
        endpoint = f"{self.base_url}/workitems/{workitem_uid}/subscribers/{self.aetitle}"  # type: ignore[attr-defined]

        # Add deletion lock parameter if requested
        if deletion_lock:
            endpoint += "?deletionlock=true"

        return self._send_unsubscription_request(endpoint)  # type: ignore[attr-defined]

    def _send_subscription_request(self, endpoint: str) -> tuple[bool, dict[str, Any] | str]:
        """
        Send a subscription request to the server.

        Args:
            endpoint: The complete endpoint URL including query parameters

        Returns:
            tuple containing success status and either the response data or error message

        """
        success, response = self._send_request("POST", endpoint, success_code=201)  # type: ignore[attr-defined]
        self.logger.info(f"endpoint {endpoint}")  # type: ignore[attr-defined]
        if success and isinstance(response, dict):
            for key, value in response.items():
                self.logger.debug(f"{key}: {value}")  # type: ignore[attr-defined]

            # Extract WebSocket URL from Content-Location header that has been
            # converted to lower case and snake_case in _send_request()
            ws_url = response.get("content_location")

            if ws_url:
                self.logger.info(f"WebSocket URL from  response content_location: {ws_url}")  # type: ignore[attr-defined]

            # If we have a WebSocket URL override template, use it
            if self.websocket_url_override:  # type: ignore[attr-defined]
                # Replace {aetitle} placeholder if present.
                # A KeyError means the template contains an unknown placeholder name.
                try:
                    self.ws_url = self.websocket_url_override.format(aetitle=self.aetitle)  # type: ignore[attr-defined]
                except KeyError as exc:
                    raise UPSRSValidationError(
                        f"websocket_url_override template contains an unsupported placeholder: {exc}. "
                        f"Template: '{self.websocket_url_override}'. Only {{{{aetitle}}}} is supported."  # type: ignore[attr-defined]
                    ) from exc
                self.logger.info(f"Using WebSocket URL override: {self.ws_url}")  # type: ignore[attr-defined]
            elif ws_url:
                # Convert WebSocket URL to match the SSL configuration of the base URL
                from urllib.parse import urlparse, urlunparse

                base_parsed = urlparse(self.base_url)  # type: ignore[attr-defined]
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
                self.ws_url = urlunparse(  # type: ignore[attr-defined]
                    (ws_scheme, ws_netloc, ws_parsed.path, ws_parsed.params, ws_parsed.query, ws_parsed.fragment)
                )

                self.logger.info(f"WebSocket URL converted from {ws_url} to {self.ws_url}")  # type: ignore[attr-defined]
            else:
                self.ws_url = self._construct_default_websocket_url(self.base_url, endpoint)  # type: ignore[attr-defined]
                self.logger.info("No WebSocket URL provided in response, using constructed default")  # type: ignore[attr-defined]
            response["ws_url"] = self.ws_url  # type: ignore[attr-defined]

        return success, response

    def _send_unsubscription_request(self, endpoint: str) -> tuple[bool, dict[str, Any] | str]:
        """
        Send an unsubscription request to the server.

        Args:
            endpoint: The complete endpoint URL including query parameters

        Returns:
            tuple containing success status and either the response data or error message

        """
        return self._send_request("DELETE", endpoint)  # type: ignore[attr-defined]
