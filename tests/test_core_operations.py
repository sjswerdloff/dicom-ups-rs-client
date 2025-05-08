"""Tests for UPS-RS client core operations."""

import pytest

from dicom_ups_rs_client.ups_rs_client import UPSRSClient, UPSState


def test_create_workitem_with_data(mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable) -> None:
    """
    Test creating a workitem with provided data.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    # Configure mock response
    response = response_factory(
        status_code=201, json_data={"status": "Success"}, headers={"Content-Location": "/workitems/1.2.3.4.5"}
    )
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", response)

    # Call the method
    success, result = mock_ups_rs_client.create_workitem(sample_workitem)

    # # Process all headers into snake_case keys
    # for header_key, header_value in response.headers.items():
    #     result[header_key.lower().replace("-", "_")] = header_value
    # Check response
    assert success is True
    assert "status" in result
    assert result["status"] == "Success"
    assert "content_location" in result
    assert result["content_location"] == "/workitems/1.2.3.4.5"

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == "http://example.com/dicom-web/workitems"
    assert request["headers"] == {"Content-Type": "application/dicom+json", "Accept": "application/dicom+json"}
    assert request["json"] == sample_workitem


def test_create_workitem_with_uid(mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable) -> None:
    """
    Test creating a workitem with a specified UID.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    # Configure mock response
    response = response_factory(
        status_code=201, json_data={"status": "Success"}, headers={"Content-Location": "/workitems/1.2.3.4.5"}
    )
    mock_ups_rs_client.session.add_response(
        "POST", r"http://example.com/dicom-web/workitems\?workitem=1\.2\.3\.4\.5", response
    )

    # Call the method
    success, result = mock_ups_rs_client.create_workitem(sample_workitem, workitem_uid="1.2.3.4.5")

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == "http://example.com/dicom-web/workitems?workitem=1.2.3.4.5"


def test_create_workitem_with_invalid_uid(mock_ups_rs_client: UPSRSClient, sample_workitem: dict) -> None:
    """
    Test creating a workitem with an invalid UID.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data

    """
    # Call the method with invalid UID
    success, result = mock_ups_rs_client.create_workitem(sample_workitem, workitem_uid="invalid.uid")

    # Should fail validation without making a request
    assert success is False
    assert "Invalid DICOM UID format" in result
    assert len(mock_ups_rs_client.session.requests) == 0


def test_create_workitem_default_data(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test creating a workitem with default data (no data provided).

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Configure mock response
    response = response_factory(
        status_code=201, json_data={"status": "Success"}, headers={"Content-Location": "/workitems/1.2.3.4.5"}
    )
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", response)

    # Call the method with no data
    success, result = mock_ups_rs_client.create_workitem()

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]

    # Verify default workitem data was created and sent
    assert request["json"] is not None
    assert "00741000" in request["json"]  # Procedure Step State
    assert request["json"]["00741000"]["Value"][0] == "SCHEDULED"
    assert "00404041" in request["json"]  # Input Readiness State
    assert request["json"]["00404041"]["Value"][0] == "READY"


def test_create_workitem_server_error(
    mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Test creating a workitem when server returns an error.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses
        monkeypatch: Pytest monkeypatch fixture

    """
    # Disable retries for this test
    monkeypatch.setattr(mock_ups_rs_client, "max_retries", 0)
    # Configure mock response
    response = response_factory(status_code=500, text="Internal Server Error", reason="Internal Server Error")
    mock_ups_rs_client.session.add_response("POST", r"http://example.com/dicom-web/workitems", response)

    # Call the method
    success, result = mock_ups_rs_client.create_workitem(sample_workitem)

    # Should fail
    assert success is False
    assert "Internal Server Error" in result


def test_retrieve_workitem(mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable) -> None:
    """
    Test retrieving a workitem.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"

    # Configure mock response
    response = response_factory(status_code=200, json_data=sample_workitem)
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response)

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Check response
    assert success is True
    assert result == sample_workitem

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "GET"
    assert request["url"] == f"http://example.com/dicom-web/workitems/{workitem_uid}"
    assert request["headers"] == {"Accept": "application/dicom+json", "Cache-Control": "no-cache"}


def test_retrieve_workitem_invalid_uid(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test retrieving a workitem with an invalid UID.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """
    # Call the method with invalid UID
    success, result = mock_ups_rs_client.retrieve_workitem("invalid.uid")

    # Should fail validation without making a request
    assert success is False
    assert "Invalid DICOM UID format" in result
    assert len(mock_ups_rs_client.session.requests) == 0


def test_retrieve_workitem_not_found(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test retrieving a non-existent workitem.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"

    # Configure mock response
    response = response_factory(status_code=404, text="Workitem not found", reason="Not Found")
    mock_ups_rs_client.session.add_response("GET", f"http://example.com/dicom-web/workitems/{workitem_uid}", response)

    # Call the method
    success, result = mock_ups_rs_client.retrieve_workitem(workitem_uid)

    # Should fail
    assert success is False
    assert "Workitem not found" in result


def test_search_workitems(mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable) -> None:
    """
    Test searching for workitems.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    # Configure mock response with a list of workitems
    response = response_factory(status_code=200, json_data=[sample_workitem])
    mock_ups_rs_client.session.add_response("GET", r"http://example.com/dicom-web/workitems\?", response)

    # Call the method with search parameters
    success, result = mock_ups_rs_client.search_workitems(
        match_parameters={"00741000": "SCHEDULED"},
        include_fields=["00741204"],
        fuzzy_matching=True,
        offset=0,
        limit=10,
        no_cache=True,
    )

    # Check response
    assert success is True
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0] == sample_workitem

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "GET"
    assert "00741000=SCHEDULED" in request["url"]
    assert "includefield=00741204" in request["url"]
    assert "fuzzymatching=true" in request["url"]
    assert "offset=0" in request["url"]
    assert "limit=10" in request["url"]
    assert request["headers"].get("Cache-Control") == "no-cache"


def test_search_workitems_no_results(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test searching for workitems when no results are found.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    # Configure mock response with 204 No Content
    response = response_factory(status_code=204, text="", reason="No Content")
    mock_ups_rs_client.session.add_response("GET", r"http://example.com/dicom-web/workitems\?", response)

    # Call the method
    success, result = mock_ups_rs_client.search_workitems(match_parameters={"00741000": "COMPLETED"})

    # Check response
    assert success is True
    assert isinstance(result, list)
    assert len(result) == 0


def test_search_workitems_partial_results(
    mock_ups_rs_client: UPSRSClient, sample_workitem: dict, response_factory: callable
) -> None:
    """
    Test searching for workitems with partial results.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        sample_workitem: Sample workitem data
        response_factory: Factory for creating mock responses

    """
    # Configure mock response with 206 Partial Content
    response = response_factory(
        status_code=206,
        json_data=[sample_workitem],
        headers={"Warning": '299 UPS-RS Server "There are more results available"'},
    )
    mock_ups_rs_client.session.add_response("GET", r"http://example.com/dicom-web/workitems\?", response)

    # Call the method
    success, result = mock_ups_rs_client.search_workitems(match_parameters={"00741000": "SCHEDULED"}, limit=1)

    # Check response
    assert success is True
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0] == sample_workitem

    # Check that the logger was informed about partial results (this will be logged but hard to test)


def test_update_workitem(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test updating a workitem.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"
    transaction_uid = "5.6.7.8.9"
    update_data = {"00741204": {"vr": "LO", "Value": ["Updated Procedure"]}}

    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response("PUT", f"http://example.com/dicom-web/workitems/{workitem_uid}", response)

    # Call the method
    success, result = mock_ups_rs_client.update_workitem(workitem_uid, transaction_uid, update_data)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "PUT"
    assert request["url"] == f"http://example.com/dicom-web/workitems/{workitem_uid}?transaction-uid={transaction_uid}"
    assert request["json"] == update_data


def test_update_workitem_no_transaction_uid(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test updating a workitem without transaction UID (only valid for SCHEDULED state).

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"
    update_data = {"00741204": {"vr": "LO", "Value": ["Updated Procedure"]}}

    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response("PUT", f"http://example.com/dicom-web/workitems/{workitem_uid}", response)

    # Call the method without transaction_uid
    success, result = mock_ups_rs_client.update_workitem(workitem_uid, None, update_data)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "PUT"
    assert request["url"] == f"http://example.com/dicom-web/workitems/{workitem_uid}"
    assert "transaction-uid" not in request["url"]


def test_update_workitem_invalid_uid(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test updating a workitem with an invalid UID.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """
    update_data = {"00741204": {"vr": "LO", "Value": ["Updated Procedure"]}}

    # Call the method with invalid workitem UID
    success, result = mock_ups_rs_client.update_workitem("invalid.uid", None, update_data)

    # Should fail validation without making a request
    assert success is False
    assert "Invalid DICOM UID format" in result
    assert len(mock_ups_rs_client.session.requests) == 0

    # Call the method with invalid transaction UID
    success, result = mock_ups_rs_client.update_workitem("1.2.3.4.5", "invalid.uid", update_data)

    # Should fail validation without making a request
    assert success is False
    assert "Invalid DICOM UID format" in result
    assert len(mock_ups_rs_client.session.requests) == 0


def test_change_workitem_state_in_progress(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test changing a workitem state to IN PROGRESS.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"

    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response("PUT", f"http://example.com/dicom-web/workitems/{workitem_uid}/state", response)

    # Call the method with UPSState enum
    success, result = mock_ups_rs_client.change_workitem_state(workitem_uid, UPSState.IN_PROGRESS)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "PUT"
    assert request["url"] == f"http://example.com/dicom-web/workitems/{workitem_uid}/state"

    # Check payload
    assert "00741000" in request["json"]
    assert request["json"]["00741000"]["Value"][0] == "IN PROGRESS"

    # IN PROGRESS should generate a transaction UID if one is not provided
    assert "00081195" in request["json"]
    assert len(request["json"]["00081195"]["Value"]) == 1

    # Transaction UID should be returned in result
    assert "transaction_uid" in result
    assert result["transaction_uid"] == request["json"]["00081195"]["Value"][0]


def test_change_workitem_state_completed(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test changing a workitem state to COMPLETED with a transaction UID.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"
    transaction_uid = "5.6.7.8.9"

    # Configure mock response
    response = response_factory(status_code=200, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response("PUT", f"http://example.com/dicom-web/workitems/{workitem_uid}/state", response)

    # Call the method with string state and transaction_uid
    success, result = mock_ups_rs_client.change_workitem_state(workitem_uid, "COMPLETED", transaction_uid)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]

    # Check payload
    assert "00741000" in request["json"]
    assert request["json"]["00741000"]["Value"][0] == "COMPLETED"
    assert "00081195" in request["json"]
    assert request["json"]["00081195"]["Value"][0] == transaction_uid


def test_change_workitem_state_invalid_state(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test changing a workitem state with an invalid state.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """
    workitem_uid = "1.2.3.4.5"

    # Call the method with invalid state
    success, result = mock_ups_rs_client.change_workitem_state(workitem_uid, "INVALID_STATE")

    # Should fail validation without making a request
    assert success is False
    assert "Invalid state: INVALID_STATE" in result
    assert len(mock_ups_rs_client.session.requests) == 0


def test_change_workitem_state_missing_transaction_uid(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test changing a workitem state to COMPLETED without transaction UID.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """
    workitem_uid = "1.2.3.4.5"

    # Call the method with COMPLETED state but no transaction_uid
    success, result = mock_ups_rs_client.change_workitem_state(workitem_uid, UPSState.COMPLETED)

    # Should fail validation without making a request
    assert success is False
    assert "Transaction UID is required for COMPLETED state" in result
    assert len(mock_ups_rs_client.session.requests) == 0

    # Same for CANCELED state
    success, result = mock_ups_rs_client.change_workitem_state(workitem_uid, UPSState.CANCELED)
    assert success is False
    assert "Transaction UID is required for CANCELED state" in result


def test_request_cancellation(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test requesting cancellation of a workitem.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"
    reason = "Test cancellation"
    contact_name = "Test User"
    contact_uri = "mailto:test@example.com"

    # Configure mock response
    response = response_factory(status_code=202, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response(
        "POST", f"http://example.com/dicom-web/workitems/{workitem_uid}/cancelrequest", response
    )

    # Call the method
    success, result = mock_ups_rs_client.request_cancellation(workitem_uid, reason, contact_name, contact_uri)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == f"http://example.com/dicom-web/workitems/{workitem_uid}/cancelrequest"

    # Check payload
    assert "00741238" in request["json"]  # Reason For Cancellation
    assert request["json"]["00741238"]["Value"][0] == reason
    assert "0074100E" in request["json"]  # Contact Display Name
    assert request["json"]["0074100E"]["Value"][0] == contact_name
    assert "0074100F" in request["json"]  # Contact URI
    assert request["json"]["0074100F"]["Value"][0] == contact_uri


def test_request_cancellation_minimal(mock_ups_rs_client: UPSRSClient, response_factory: callable) -> None:
    """
    Test requesting cancellation with minimal information.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client
        response_factory: Factory for creating mock responses

    """
    workitem_uid = "1.2.3.4.5"

    # Configure mock response
    response = response_factory(status_code=202, json_data={"status": "Success"})
    mock_ups_rs_client.session.add_response(
        "POST", f"http://example.com/dicom-web/workitems/{workitem_uid}/cancelrequest", response
    )

    # Call the method with only workitem_uid
    success, result = mock_ups_rs_client.request_cancellation(workitem_uid)

    # Check response
    assert success is True

    # Check request
    assert len(mock_ups_rs_client.session.requests) == 1
    request = mock_ups_rs_client.session.requests[0]

    # Check payload - should be empty dict or minimal
    assert request["json"] == {}


def test_request_cancellation_invalid_uid(mock_ups_rs_client: UPSRSClient) -> None:
    """
    Test requesting cancellation with an invalid UID.

    Args:
        mock_ups_rs_client: Mocked UPS-RS client

    """
    # Call the method with invalid UID
    success, result = mock_ups_rs_client.request_cancellation("invalid.uid")

    # Should fail validation without making a request
    assert success is False
    assert "Invalid DICOM UID format" in result
    assert len(mock_ups_rs_client.session.requests) == 0


def test_validate_uid() -> None:
    """Test the validate_uid static method."""
    # Valid UIDs
    assert UPSRSClient.validate_uid("1.2.3.4.5") is True
    assert UPSRSClient.validate_uid("1.2.840.10008.5.1.4.34.5") is True

    # Invalid UIDs
    assert UPSRSClient.validate_uid("invalid.uid") is False
    assert UPSRSClient.validate_uid("1.2.3.") is False
    assert UPSRSClient.validate_uid("1.2..3.4") is False
    assert UPSRSClient.validate_uid("1.2.999999.3.4") is True
