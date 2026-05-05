"""Response parsing mixin for DICOM UPS-RS Client."""

import json
from typing import Any

import requests
from pydicom import Dataset

from dicom_ups_rs_client.serialization import parse_response_body


class ResponseParsingMixin:
    """
    Mixin providing HTTP response parsing helpers for UPSRSClient.

    This mixin contains the logic for parsing successful, no-content,
    partial-content, and error HTTP responses from a UPS-RS server.
    All methods rely on ``self.logger`` provided by the host class.
    """

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
                    self.logger.info(f"Response header {header_lower}: {response.headers.get(header_lower)}")  # type: ignore[attr-defined]
                    parsed_result[header_lower.replace("-", "_")] = response.headers.get(header_lower)
                    snake_case_header = header_lower.replace("-", "_")
                    self.logger.info(  # type: ignore[attr-defined]
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
                self.logger.warning(  # type: ignore[attr-defined]
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

        self.logger.info("Partial results received. There may be more results available.")  # type: ignore[attr-defined]
        if "Warning" in response.headers:
            self.logger.debug(f"Server warning: {response.headers['Warning']}")  # type: ignore[attr-defined]

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
                    self.logger.debug(f"Response details: {error_details}")  # type: ignore[attr-defined]
                    error_msg += f". Error details: {error_details}"
                else:
                    error_msg += f". Response: {response.text}"
            except json.JSONDecodeError:
                error_msg = f"{error_msg}, Response: {response.text}"
            except ValueError:
                error_msg = f"{brief_error_msg}. Response: {response.text}"
        return error_msg
