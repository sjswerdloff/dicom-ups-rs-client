"""Tests for UPS-RS client utility methods."""

from datetime import datetime, timedelta

from pydicom.uid import generate_uid

from dicom_ups_rs_client.ups_rs_client import InputReadinessState, UPSRSClient, UPSState


def test_validate_uid() -> None:
    """Test the validate_uid static method with various UIDs."""
    # Valid UIDs
    assert UPSRSClient.validate_uid("1.2.3.4.5") is True
    assert UPSRSClient.validate_uid("1.2.840.10008.5.1.4.34.5") is True  # Global subscription well-known UID
    assert UPSRSClient.validate_uid("1.2.840.10008.5.1.4.34.5.1") is True  # Filtered subscription well-known UID
    assert UPSRSClient.validate_uid("1.2.840.10008.5.1.4.34.6.1") is True  # UPS Push SOP Class UID
    assert UPSRSClient.validate_uid(str(generate_uid())) is True  # Generated UID
    assert UPSRSClient.validate_uid("1.2.99999999999.3.4") is True  # Component value is not too large
    assert UPSRSClient.validate_uid("0.1.2.3.4") is True  # Leading zero
    # Invalid UIDs
    assert UPSRSClient.validate_uid("invalid.uid") is False
    assert UPSRSClient.validate_uid("1.2.3.") is False  # Trailing dot
    assert UPSRSClient.validate_uid("1..2.3.4") is False  # Empty component
    assert UPSRSClient.validate_uid(".1.2.3.4") is False  # Leading dot

    assert UPSRSClient.validate_uid("2.1.2.3.00.4") is False  # double zero


def test_create_default_workitem() -> None:
    """Test the _create_default_workitem method."""
    client = UPSRSClient(base_url="http://example.com/dicom-web")

    # Get the default workitem
    workitem = client._create_default_workitem()

    # Check required attributes
    assert "00741000" in workitem  # Procedure Step State
    assert workitem["00741000"]["vr"] == "CS"
    assert workitem["00741000"]["Value"][0] == "SCHEDULED"

    assert "00404041" in workitem  # Input Readiness State
    assert workitem["00404041"]["vr"] == "CS"
    assert workitem["00404041"]["Value"][0] == "READY"

    assert "00404005" in workitem  # Scheduled Procedure Step Start DateTime
    assert workitem["00404005"]["vr"] == "DT"

    assert "00404011" in workitem  # Scheduled Procedure Step End DateTime
    assert workitem["00404011"]["vr"] == "DT"

    assert "00741204" in workitem  # Procedure Step Label
    assert workitem["00741204"]["vr"] == "LO"
    assert workitem["00741204"]["Value"][0] == "Example Procedure"

    assert "00404000" in workitem  # Workitem Type
    assert workitem["00404000"]["vr"] == "CS"
    assert workitem["00404000"]["Value"][0] == "IMAGE_PROCESSING"

    assert "00400007" in workitem  # Procedure Step Description
    assert workitem["00400007"]["vr"] == "LO"
    assert workitem["00400007"]["Value"][0] == "Example procedure step description"

    # Check datetime values
    start_str = workitem["00404005"]["Value"][0]
    end_str = workitem["00404011"]["Value"][0]

    # Parse the datetimes
    start_dt = datetime.strptime(start_str, "%Y%m%d%H%M%S")
    end_dt = datetime.strptime(end_str, "%Y%m%d%H%M%S")

    # Scheduled start should be in the future
    now = datetime.now()
    assert start_dt > now

    # Scheduled end should be after start
    assert end_dt > start_dt
    assert (end_dt - start_dt) >= timedelta(hours=1)


def test_ups_state_enum_string_representation() -> None:
    """Test string representation of UPSState enum values."""
    assert str(UPSState.SCHEDULED) == "SCHEDULED"
    assert str(UPSState.IN_PROGRESS) == "IN PROGRESS"
    assert str(UPSState.CANCELED) == "CANCELED"
    assert str(UPSState.COMPLETED) == "COMPLETED"


def test_input_readiness_state_enum_string_representation() -> None:
    """Test string representation of InputReadinessState enum values."""
    assert str(InputReadinessState.READY) == "READY"
    assert str(InputReadinessState.UNAVAILABLE) == "UNAVAILABLE"
    assert str(InputReadinessState.INCOMPLETE) == "INCOMPLETE"


def test_send_subscription_request(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test the internal _send_subscription_request method.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Configure mock response
    response = response_factory(
        status_code=201,
        json_data={"status": "Success"},
        headers={"Content-Location": "http://example.com/dicom-web/subscribers/TEST_AE"},
    )
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/test-endpoint", response)

    # Call the method
    success, result = mock_ups_rs_client._send_subscription_request("http://example.com/dicom-web/test-endpoint")

    # Check response
    assert success is True
    assert "content_location" in result
    assert "ws_url" in result
    assert result["ws_url"] == "http://example.com/dicom-web/subscribers/TEST_AE"


def test_send_unsubscription_request(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test the internal _send_unsubscription_request method.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response("DELETE", r"http://example.com/dicom-web/test-endpoint", response)

    # Call the method
    success, result = mock_ups_rs_client._send_unsubscription_request("http://example.com/dicom-web/test-endpoint")

    # Check response
    assert success is True
    assert "status" in result
    assert result["status"] == "Success"


def test_send_request_with_headers(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test the internal _send_request method with custom headers.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response("GET", r"http://example.com/dicom-web/test-endpoint", response)

    # Custom headers
    headers = {"Custom-Header": "Test Value", "Another-Header": "Another Value"}

    # Call the method
    success, result = mock_ups_rs_client._send_request("GET", "http://example.com/dicom-web/test-endpoint", headers=headers)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert "Custom-Header" in request["headers"]
    assert request["headers"]["Custom-Header"] == "Test Value"
    assert "Another-Header" in request["headers"]
    assert request["headers"]["Another-Header"] == "Another Value"


def test_send_request_with_json_data(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test the internal _send_request method with JSON data.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/test-endpoint", response)

    # JSON data
    json_data = {"test_key": "test_value", "nested": {"inner_key": "inner_value"}}

    # Call the method
    success, result = mock_ups_rs_client._send_request(
        "POST", "http://example.com/dicom-web/test-endpoint", json_data=json_data
    )

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["json"] == json_data


def test_send_request_custom_success_code(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test the internal _send_request method with custom success code.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Configure mock response with 201 Created
    response = response_factory(status_code=201, json_data={"status": "Created"})
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/test-endpoint", response)

    # Call the method with 201 as success code
    success, result = mock_ups_rs_client._send_request("POST", "http://example.com/dicom-web/test-endpoint", success_code=201)

    # Check response
    assert success is True
    assert "status" in result
    assert result["status"] == "Created"

    # Configure mock response with 200 OK
    response = response_factory(status_code=200, json_data={"status": "OK"})
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/test-endpoint", response)

    # Call the method with 201 as success code
    success, result = mock_ups_rs_client._send_request("POST", "http://example.com/dicom-web/test-endpoint", success_code=201)

    # Check response - should fail as status code is not what was expected
    assert success is False
    assert "Failed request" in result


def test_event_handler() -> None:
    """Test the event_handler function."""
    from dicom_ups_rs_client.ups_rs_client import _event_handler as event_handler

    # Create a test event
    event_data = {
        "00001002": {"vr": "US", "Value": [1]},  # Event Type ID
        "00001000": {"vr": "UI", "Value": ["1.2.3.4.5"]},  # Affected SOP Instance UID
        "00741000": {"vr": "CS", "Value": ["SCHEDULED"]},  # Procedure Step State
    }

    # Capture stdout to verify output
    import io
    import sys

    captured_output = io.StringIO()
    sys.stdout = captured_output

    # Call the event handler
    event_handler(event_data)

    # Restore stdout
    sys.stdout = sys.__stdout__

    # Verify the output
    output = captured_output.getvalue()
    assert "EVENT RECEIVED: 1 - Workitem: 1.2.3.4.5" in output
    assert "SCHEDULED" in output
