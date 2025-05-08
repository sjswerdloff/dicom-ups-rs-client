"""Tests for UPS-RS client error handling capabilities."""

import json
import logging
import sys
from unittest.mock import Mock

import pytest
import requests
from requests import Response

from dicom_ups_rs_client.ups_rs_client import (
    UPSRSClient,
    UPSRSError,
    UPSRSRequestError,
    UPSRSResponseError,
    UPSRSValidationError,
)


def test_http_error_responses(
    mock_ups_rs_client: UPSRSClient,
    response_factory: callable,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Test handling of HTTP error responses.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses
        monkeypatch: Pytest monkeypatch fixture

    """
    workitem_uid = "1.2.3.4.5"
    # Disable retries for this test to keep it simple
    monkeypatch.setattr(mock_ups_rs_client, "max_retries", 0)
    # Test various HTTP error codes
    error_codes = [400, 401, 403, 404, 409, 500, 501, 503]
    error_messages = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        500: "Internal Server Error",
        501: "Not Implemented",
        503: "Service Unavailable",
    }

    for code in error_codes:
        # Reset the session for each test
        # Get the MockSession class from the existing instance
        MockSessionClass = type(mock_ups_rs_client.session)  # noqa: N806

        # Reset the session for each test
        mock_ups_rs_client.session = MockSessionClass()
        # Configure mock response with error
        response = response_factory(status_code=code, text=f"Error: {error_messages[code]}", reason=error_messages[code])
        mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response)

        # Call the method
        success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

        # Check error handling
        assert success is False
        assert error_messages[code] in result, f"Expected '{error_messages[code]}' in '{result}'"
        assert str(code) in result, f"Expected '{code}' in '{result}'"


def test_json_error_responses(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test handling of error responses with JSON error details.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"

    # Configure mock response with JSON error details
    error_details = {"errors": [{"code": "UPSInvalidUID", "message": "Invalid UID format"}]}
    response = response_factory(status_code=400, json_data=error_details, reason="Bad Request")
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response)

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Check error handling
    assert success is False
    assert "UPSInvalidUID" in result
    assert "Invalid UID format" in result


def test_warning_headers(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test handling of warning headers in responses.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"

    # Configure mock response with warning header
    warning_message = '299 UPS-RS "This is a test warning"'
    response = response_factory(
        status_code=200, json_data={"status": "Success with warnings"}, headers={"Warning": warning_message}
    )
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response)

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Check warning handling
    assert success is True
    assert "warning" in result
    assert result["warning"] == warning_message


def test_retry_logic_for_server_errors(
    mock_ups_rs_client: UPSRSClient,
    response_factory: callable,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    Test retry logic for server errors.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses
        monkeypatch: Pytest monkeypatch fixture
        caplog: pytest Log Capture Fixture

    """
    # Configure logging for this test
    logger = logging.getLogger("ups_rs_client")
    logger.setLevel(logging.DEBUG)

    # Create a stream handler that writes to stderr
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    workitem_uid = "1.2.3.4.5"

    # Capture original request method
    original_request = mock_ups_rs_client.session.request

    # Create a wrapper that prints info
    def request_wrapper(*args: tuple, **kwargs: dict[str, any]) -> Response:
        print(f"\nMOCK SESSION REQUEST: {args[0]} {args[1]}")
        response = original_request(*args, **kwargs)
        print(f"RESPONSE STATUS: {response.status_code}")
        return response

    # Replace request method with our wrapper
    mock_ups_rs_client.session.request = request_wrapper
    # Disable actual sleep for faster tests
    monkeypatch.setattr("time.sleep", lambda x: None)

    # Configure mock responses: first two with 503 errors, then success
    response_error1 = response_factory(status_code=503, text="Service Unavailable", reason="Service Unavailable")
    response_error2 = response_factory(status_code=503, text="Service Unavailable", reason="Service Unavailable")
    response_success = response_factory(status_code=200, json_data={"status": "Success after retry"})

    # Add responses in sequence
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response_error1)
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response_error2)
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response_success)

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Check that it eventually succeeded
    assert success is True
    assert result["status"] == "Success after retry"

    # Should have made 3 requests (2 failures, 1 success)
    assert len(mock_ups_rs_client.session.requests) == 3
    assert all(r["method"] == "GET" for r in mock_ups_rs_client.session.requests)
    assert all(
        r["url"] == f"http://example.com/dicom-web/workitems/{workitem_uid}" for r in mock_ups_rs_client.session.requests
    )


def test_max_retries_exceeded(
    mock_ups_rs_client: UPSRSClient, response_factory: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Test behavior when max retries are exceeded.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses
        monkeypatch: Pytest monkeypatch fixture

    """
    workitem_uid = "1.2.3.4.5"

    # Disable actual sleep for faster tests
    monkeypatch.setattr("time.sleep", lambda x: None)

    # Set max retries to 2
    mock_ups_rs_client.max_retries = 2

    # Configure mock responses: all with 503 errors
    response_error = response_factory(status_code=503, text="Service Unavailable", reason="Service Unavailable")

    # Add multiple error responses
    for _ in range(mock_ups_rs_client.max_retries + 1):
        mock_ups_rs_client.session.add_response(
            "GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response_error
        )

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Should fail after max retries
    assert success is False
    assert "Service Unavailable" in result
    assert "Max retries exceeded" in result

    # Should have made exactly max_retries + 1 requests
    assert len(mock_ups_rs_client.session.requests) == mock_ups_rs_client.max_retries + 1


def test_connection_errors(mock_ups_rs_client: UPSRSClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test handling of connection errors.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        monkeypatch: Pytest monkeypatch fixture

    """
    workitem_uid = "1.2.3.4.5"

    # Disable actual sleep for faster tests
    monkeypatch.setattr("time.sleep", lambda x: None)

    # Create a mock session that raises connection errors
    mock_session = Mock()
    mock_session.request = Mock(side_effect=requests.exceptions.ConnectionError("Connection refused"))
    mock_ups_rs_client.session = mock_session

    # Set max retries to 2
    mock_ups_rs_client.max_retries = 2

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Should fail after max retries
    assert success is False
    assert "Connection refused" in result
    assert "Max retries exceeded" in result

    # Should have called request exactly max_retries + 1 times
    assert mock_session.request.call_count == mock_ups_rs_client.max_retries + 1


def test_timeout_errors(mock_ups_rs_client: UPSRSClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Test handling of timeout errors.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        monkeypatch: Pytest monkeypatch fixture

    """
    workitem_uid = "1.2.3.4.5"

    # Disable actual sleep for faster tests
    monkeypatch.setattr("time.sleep", lambda x: None)

    # Create a mock session that raises timeout errors
    mock_session = Mock()
    mock_session.request = Mock(side_effect=requests.exceptions.Timeout("Request timed out"))
    mock_ups_rs_client.session = mock_session

    # Set max retries to 2
    mock_ups_rs_client.max_retries = 2

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Should fail after max retries
    assert success is False
    assert "Request timed out" in result
    assert "Max retries exceeded" in result

    # Should have called request exactly max_retries + 1 times
    assert mock_session.request.call_count == mock_ups_rs_client.max_retries + 1


def test_client_errors_not_retried(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test that client errors (4xx) are not retried.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"

    # Configure mock response with a 404 Not Found error
    response_error = response_factory(status_code=404, text="Not Found", reason="Not Found")

    # Add response
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response_error)

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Should fail without retrying
    assert success is False
    assert "Not Found" in result

    # Should have made exactly 1 request (no retries)
    assert len(mock_ups_rs_client.session.requests) == 1


def test_no_content_responses(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test handling of 204 No Content responses.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Configure mock response with 204 No Content
    response = response_factory(status_code=204, text="", reason="No Content")

    # Add response for search_workitems
    mock_ups_rs_client.session.add_response("GET", r"http://example.com/dicom-web/workitems\?", response)

    # Call the method
    success, result = mock_ups_rs_client.search_workitems(match_parameters={"00741000": "SCHEDULED"})

    # Should succeed with an empty list
    assert success is True
    assert isinstance(result, list)
    assert len(result) == 0


def test_json_decode_error(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test handling of JSON decode errors in responses.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"

    # Configure mock response with invalid JSON
    response = response_factory(status_code=200, text="This is not valid JSON", reason="OK")

    # Mock json method to raise JSONDecodeError
    def mock_json() -> any:
        raise json.JSONDecodeError("Invalid JSON", "", 0)

    response.json = mock_json

    # Add response
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response)

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Should succeed but include the raw text
    assert success is True
    assert "response_text" in result
    assert result["response_text"] == "This is not valid JSON"


def test_handle_too_many_requests(
    mock_ups_rs_client: UPSRSClient, response_factory: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Test handling of 429 Too Many Requests responses.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses
        monkeypatch: Pytest monkeypatch fixture

    """
    workitem_uid = "1.2.3.4.5"

    # Disable actual sleep for faster tests
    monkeypatch.setattr("time.sleep", lambda x: None)

    # Configure mock responses: first with 429 error, then success
    response_error = response_factory(
        status_code=429,
        text="Too Many Requests",
        reason="Too Many Requests",
        headers={"Retry-After": "1"},  # 1 second
    )
    response_success = response_factory(status_code=200, json_data={"status": "Success after rate limit"})

    # Add responses in sequence
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response_error)
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response_success)

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Check that it eventually succeeded
    assert success is True
    assert result["status"] == "Success after rate limit"

    # Should have made 2 requests (1 failure, 1 success)
    assert len(mock_ups_rs_client.session.requests) == 2


def test_exception_class_hierarchy() -> None:
    """Test the exception class hierarchy."""
    # Create instances of each exception class
    base_exception = UPSRSError("Base error")
    response_error = UPSRSResponseError("Response error", 400, "Bad Request")
    request_error = UPSRSRequestError("Request error")
    validation_error = UPSRSValidationError("Validation error")

    # Check inheritance
    assert isinstance(base_exception, Exception)
    assert isinstance(response_error, UPSRSError)
    assert isinstance(request_error, UPSRSError)
    assert isinstance(validation_error, UPSRSError)

    # Check response error attributes
    assert response_error.status_code == 400
    assert response_error.response_text == "Bad Request"

    # Check string representation
    assert str(base_exception) == "Base error"
    assert str(response_error) == "Response error"
    assert str(request_error) == "Request error"
    assert str(validation_error) == "Validation error"
